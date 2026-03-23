#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search.py — 向量檢索模組（v2）

配合新 schema（web_pages + document_chunks），
檢索時 JOIN web_pages 取得 url、page_type，
並回傳包含 source_url 的結果，讓 LLM 能引用來源。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 修復 Windows 控制台 UTF-8 輸出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 讓 scripts 目錄在 path 中
CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
ROOT_DIR = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db.connection import get_connection
from embedder.vectorize import embed_texts
from retriever.reranker import rerank


def search_core(
    query: str,
    top_k: int = 7,
    use_rerank: bool = True,
    school_id: str | None = None,
    page_type: str | None = None,
) -> list[dict]:
    """
    執行向量檢索，回傳文件列表。

    Args:
        query:      使用者查詢字串
        top_k:      最終返回筆數
        use_rerank: 是否啟用 Cross-Encoder 重排序
        school_id:  若指定則只搜尋該學校（e.g. 'cmu'）
        page_type:  若指定則只搜尋該類型頁面（e.g. 'faq'）

    Returns:
        list of dict，每個 dict 包含：
          - chunk_text
          - source_url
          - page_type
          - university_name
          - school_id
          - vector_score
          - rerank_score (若 use_rerank=True)
          - metadata (JSONB)
    """
    # 1. 向量化查詢
    query_embeddings = embed_texts([query])
    if not query_embeddings:
        return []
    query_vector = query_embeddings[0]

    # 2. 連線資料庫
    conn = get_connection()
    if not conn:
        return []

    try:
        with conn.cursor() as cur:
            # 3. 組裝 WHERE 條件
            filters = []
            params = []
            if school_id:
                filters.append("dc.school_id = %s")
                params.append(school_id)
            if page_type:
                filters.append("dc.page_type = %s")
                params.append(page_type)

            where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
            and_where_clause = ("AND " + " AND ".join(filters)) if filters else ""

            # 初篩數量
            initial_k = top_k * 4 if use_rerank else top_k * 2

            # Hybrid Search SQL (RRF - Reciprocal Rank Fusion)
            sql = f"""
                WITH vector_matches AS (
                    SELECT 
                        id, 
                        1 - (embedding <=> %s::vector) AS vector_score,
                        ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) as rank
                    FROM document_chunks dc
                    {where_clause}
                    LIMIT {initial_k}
                ),
                keyword_matches AS (
                    SELECT 
                        id, 
                        ts_rank_cd(fts_vector, websearch_to_tsquery('english', %s)) AS fts_score,
                        ROW_NUMBER() OVER (ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery('english', %s)) DESC) as rank
                    FROM document_chunks dc
                    WHERE fts_vector @@ websearch_to_tsquery('english', %s)
                    {and_where_clause}
                    LIMIT {initial_k}
                )
                SELECT
                    dc.chunk_text,
                    dc.source_url,
                    dc.page_type,
                    dc.school_id,
                    dc.metadata,
                    u.name AS university_name,
                    COALESCE(vm.vector_score, 0) AS vector_score,
                    COALESCE(km.fts_score, 0) AS fts_score,
                    (COALESCE(1.0 / (60 + vm.rank), 0) + COALESCE(1.0 / (60 + km.rank), 0)) AS rrf_score
                FROM document_chunks dc
                JOIN universities u ON dc.university_id = u.id
                LEFT JOIN vector_matches vm ON dc.id = vm.id
                LEFT JOIN keyword_matches km ON dc.id = km.id
                WHERE vm.id IS NOT NULL OR km.id IS NOT NULL
                ORDER BY rrf_score DESC
                LIMIT {initial_k};
            """

            # 參數列表: 
            # 1-2. query_vector (for vector_matches score & rank)
            # 3.   params (for vector_matches filter)
            # 4-6. query (for keyword_matches score, rank, and WHERE)
            # 7.   params (for keyword_matches filter)
            full_params = [query_vector, query_vector] + params + [query, query, query] + params
            cur.execute(sql, full_params)

            colnames = [desc[0] for desc in cur.description]
            rows = cur.fetchall()

            if not rows:
                return []

            candidates = [dict(zip(colnames, row)) for row in rows]

        # 4. 重排序（精篩）
        if use_rerank and len(candidates) > top_k:
            results = rerank(query, candidates, top_n=top_k)
        else:
            results = candidates[:top_k]

        return results

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
):
    """執行向量搜尋並印出結果（CLI 用）。"""
    print(f"\n正在檢索：'{query}'")
    if school_id:
        print(f"   [過濾] 學校: {school_id}")
    print(f"   [1/2] 執行向量搜尋...")

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

        print(
            f"【結果 {i}】 {score_str}\n"
            f"  學校: {res['university_name']} ({res['school_id']})\n"
            f"  頁面類型: {res.get('page_type', 'unknown')}\n"
            f"  來源 URL: {res.get('source_url', 'N/A')}\n"
            f"  內容摘要: {res['chunk_text'].strip()[:500]}...\n"
            + "-" * 80
        )

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="向量檢索")
    parser.add_argument("query", nargs="?", help="查詢字串")
    parser.add_argument("--top-k", type=int, default=5, help="回傳筆數")
    parser.add_argument("--school", type=str, default=None, help="限定學校 e.g. cmu, caltech")
    parser.add_argument("--no-rerank", action="store_true", help="關閉重排序")
    args = parser.parse_args()

    q = args.query or input("請輸入查詢: ").strip()
    if q:
        run_search(q, top_k=args.top_k, use_rerank=not args.no_rerank, school_id=args.school)
    else:
        print("未輸入查詢。")
