from __future__ import annotations

import sys
from pathlib import Path

# 修復 Windows 控制台 UTF-8 輸出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection
from embedder.vectorize import embed_texts
from retriever.reranker import rerank


# ── 內部工具 ────────────────────────────────────────────────────────────────

def _build_filter_clauses(school_id: str | None) -> tuple[str, str, list]:
    """
    依 school_id 建立 WHERE 與 AND 子句及對應參數列表。

    Returns:
        (where_clause, and_where_clause, params)
    """
    if school_id:
        return "WHERE dc.school_id = %s", "AND dc.school_id = %s", [school_id]
    return "", "", []


def _execute_hybrid_search(
    conn,
    query_vector: list[float],
    query: str,
    where_clause: str,
    and_where_clause: str,
    filter_params: list,
    initial_k: int,
) -> list[dict]:
    """
    執行 RRF 混合搜尋 SQL，回傳候選文件列表。

    參數順序：
      1-2. query_vector（vector_matches 的分數與排名）
      3.   filter_params（vector_matches 的過濾）
      4-6. query（keyword_matches 的分數、排名與 WHERE）
      7.   filter_params（keyword_matches 的過濾）
    """
    sql = f"""
        WITH vector_matches AS (
            SELECT
                id,
                1 - (embedding <=> %s::vector) AS vector_score,
                ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
            FROM document_chunks dc
            {where_clause}
            LIMIT {initial_k}
        ),
        keyword_matches AS (
            SELECT
                id,
                ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %s)) AS fts_score,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %s)) DESC
                ) AS rank
            FROM document_chunks dc
            WHERE fts_vector @@ websearch_to_tsquery('simple', %s)
            {and_where_clause}
            LIMIT {initial_k}
        )
        SELECT
            dc.chunk_text,
            dc.source_url,
            dc.passed_types,
            dc.school_id,
            u.name AS university_name,
            COALESCE(vm.vector_score, 0) AS vector_score,
            COALESCE(km.fts_score, 0)   AS fts_score,
            (COALESCE(1.0 / (60 + vm.rank), 0) + COALESCE(1.0 / (60 + km.rank), 0)) AS rrf_score
        FROM document_chunks dc
        JOIN universities u ON dc.university_id = u.id
        LEFT JOIN vector_matches vm ON dc.id = vm.id
        LEFT JOIN keyword_matches km ON dc.id = km.id
        WHERE vm.id IS NOT NULL OR km.id IS NOT NULL
        ORDER BY rrf_score DESC
        LIMIT {initial_k};
    """

    full_params = (
        [query_vector, query_vector]
        + filter_params
        + [query, query, query]
        + filter_params
    )

    with conn.cursor() as cur:
        cur.execute(sql, full_params)
        colnames = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    return [dict(zip(colnames, row)) for row in rows]


# ── 公開 API ────────────────────────────────────────────────────────────────

def search_core(
    query: str,
    top_k: int = 5,
    use_rerank: bool = True,
    school_id: str | None = None,
) -> list[dict]:
    """
    執行向量 + 關鍵字混合檢索，回傳排序後的文件列表。

    Args:
        query:      使用者查詢字串
        top_k:      最終返回筆數
        use_rerank: 是否啟用 Cross-Encoder 重排序
        school_id:  若指定則只搜尋該學校（e.g. 'cmu'）

    Returns:
        list of dict，每筆包含 chunk_text、source_url、passed_types、
        school_id、university_name、vector_score、rerank_score。
    """
    query_vector = embed_texts([query])
    if not query_vector:
        return []

    conn = get_connection()
    if not conn:
        return []

    try:
        initial_k = top_k * 4 if use_rerank else top_k * 2
        where_clause, and_where_clause, filter_params = _build_filter_clauses(school_id)

        candidates = _execute_hybrid_search(
            conn, query_vector[0], query,
            where_clause, and_where_clause, filter_params,
            initial_k,
        )

        if not candidates:
            return []

        if use_rerank and len(candidates) > top_k:
            try:
                return rerank(query, candidates, top_n=top_k)
            except Exception as e:
                print(f"[search] 重排序失敗，改用初篩結果：{e}")

        return candidates[:top_k]

    except Exception as e:
        print(f"搜尋核心出錯: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        conn.close()


def run_search(
    query: str,
    top_k: int = 5,
    use_rerank: bool = True,
    school_id: str | None = None,
) -> bool:
    """執行向量搜尋並印出結果（CLI 用）。"""
    print(f"\n正在檢索：'{query}'")
    if school_id:
        print(f"   [過濾] 學校: {school_id}")
    print("   [1/2] 執行向量搜尋...")

    results = search_core(query, top_k=top_k, use_rerank=use_rerank, school_id=school_id)

    if not results:
        print("查無相關資料。")
        return True

    print(f"   [2/2] 完成，共 {len(results)} 筆結果\n")
    print("=" * 80)

    for i, res in enumerate(results, 1):
        score_str = f"向量分數: {res['vector_score']:.4f}"
        if "rerank_score" in res:
            score_str += f"  Re-rank: {res['rerank_score']:.4f}"
        types_str = ", ".join(
            f"{pt['type']}({pt['score']})" for pt in res.get("passed_types") or []
        )
        print(
            f"【結果 {i}】 {score_str}\n"
            f"  學校: {res['university_name']} ({res['school_id']})\n"
            f"  類型: {types_str or 'unknown'}\n"
            f"  來源 URL: {res.get('source_url', 'N/A')}\n"
            f"  內容摘要: {res['chunk_text'].strip()[:500]}...\n"
            + "-" * 80
        )

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="向量檢索")
    parser.add_argument("query",     nargs="?", help="查詢字串")
    parser.add_argument("--top-k",   type=int, default=5, help="回傳筆數")
    parser.add_argument("--school",  type=str, default=None, help="限定學校 e.g. cmu")
    parser.add_argument("--no-rerank", action="store_true", help="關閉重排序")
    args = parser.parse_args()

    q = args.query or input("請輸入查詢: ").strip()
    if q:
        run_search(q, top_k=args.top_k, use_rerank=not args.no_rerank, school_id=args.school)
    else:
        print("未輸入查詢。")
