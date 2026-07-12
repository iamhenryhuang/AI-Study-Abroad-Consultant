"""向量 + FTS 的 RRF 混合檢索（agent 文本檢索 fallback 的升級版）。

自 backup/AI-Study-Abroad-Consultant-1.0.0 移植，三處適配：
  1. vector_matches CTE 加 WHERE embedding IS NOT NULL
     （pipeline 預設 ENABLE_EMBEDDING=off，存在 NULL 向量，不濾會污染排名）
  2. conn.read_only = True（與現行檢索模組統一）
  3. doc 形狀對齊 fulltext_search（chunk_text/source_url/school_id/university_name）

錯誤語意：查詢/連線/embedding 失敗一律 raise（由 hybrid_search_with_fallback
降級純 FTS）；只有真的查無資料才回傳 []。reranker 失敗例外：退回 RRF 排序。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection
from retriever.vectorize import embed_query
from retriever.reranker import rerank

# RRF 常數：backup 沿用學界慣例 k=60
_RRF_K = 60


def _to_pgvector_literal(vec: list[float]) -> str:
    """轉成 pgvector 文字字面值 '[0.1,0.2,...]'，避免 psycopg 型別轉接問題。"""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def _build_sql(school_id: str | None, initial_k: int) -> str:
    school_filter = "AND school_id = %(school_id)s" if school_id else ""
    return f"""
        WITH vector_matches AS (
            SELECT id,
                   1 - (embedding <=> %(vec)s::vector) AS vector_score,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
            FROM document_chunks
            WHERE embedding IS NOT NULL
              {school_filter}
            ORDER BY embedding <=> %(vec)s::vector
            LIMIT {initial_k}
        ),
        keyword_matches AS (
            SELECT id,
                   ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %(q)s)) AS fts_score,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %(q)s)) DESC
                   ) AS rank
            FROM document_chunks
            WHERE fts_vector @@ websearch_to_tsquery('simple', %(q)s)
              {school_filter}
            LIMIT {initial_k}
        )
        SELECT dc.chunk_text,
               dc.source_url,
               dc.school_id,
               u.name AS university_name,
               COALESCE(vm.vector_score, 0) AS vector_score,
               COALESCE(km.fts_score, 0)    AS fts_score,
               (COALESCE(1.0 / ({_RRF_K} + vm.rank), 0)
                + COALESCE(1.0 / ({_RRF_K} + km.rank), 0)) AS rrf_score
        FROM document_chunks dc
        LEFT JOIN universities u ON u.school_id = dc.school_id
        LEFT JOIN vector_matches vm ON dc.id = vm.id
        LEFT JOIN keyword_matches km ON dc.id = km.id
        WHERE vm.id IS NOT NULL OR km.id IS NOT NULL
        ORDER BY rrf_score DESC
        LIMIT {initial_k}
    """


def hybrid_search(
    query: str,
    school_id: str | None = None,
    limit: int = 5,
    use_rerank: bool = True,
) -> list[dict]:
    """RRF 混合檢索 → CrossEncoder 重排序，回傳前 limit 筆 doc。

    embedding 全部未 backfill 時 vector_matches 為空，RRF 自然退化為
    FTS-only 排名，仍可運作；reranker 失敗則退回 RRF 排序結果。
    """
    vec = embed_query(query)
    if not vec:
        return []

    conn = get_connection()
    if not conn:
        raise ConnectionError("[HybridSearch] 無法取得資料庫連線")

    conn.read_only = True
    initial_k = limit * 2

    try:
        with conn.cursor() as cur:
            params = {"vec": _to_pgvector_literal(vec), "q": query}
            if school_id:
                params["school_id"] = school_id
            cur.execute(_build_sql(school_id, initial_k), params)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    candidates = [dict(zip(columns, row)) for row in rows]
    candidates = [c for c in candidates if c.get("chunk_text")]
    if not candidates:
        return []

    if use_rerank and len(candidates) > limit:
        try:
            return rerank(query, candidates, top_n=limit)
        except Exception as e:
            print(f"[HybridSearch] 重排序失敗，改用 RRF 排序：{e}")

    return candidates[:limit]
