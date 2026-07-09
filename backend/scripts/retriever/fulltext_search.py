"""
全文檢索 fallback：當 text-to-SQL 在 programs 家族查無結果時，
改對 document_chunks.fts_vector 做 PostgreSQL 全文檢索（不需要 embedding model）。

之後若要加語意向量檢索，只需在同一張表新增一段 cosine similarity 查詢，
document_chunks.embedding 欄位與 HNSW 索引已經在 schema 裡就緒。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection

# schema 用 'simple' text search config（無 stopword 清單、無 stemming，避免中文被英文規則誤傷），
# 代表使用者自然語句中的疑問詞（how/does/many...）也會被當成必要關鍵字。
# 用 plainto_tsquery 的 AND 語意在此情境下幾乎不會命中，因此改用 OR 語意（任一詞命中即可）當 fallback 廣撒網。
_WORD_PATTERN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _build_or_tsquery(text: str) -> str | None:
    """把自然語句拆詞後用 OR 組成 tsquery 字串（交給 to_tsquery('simple', ...) 解析）。"""
    words = [w for w in _WORD_PATTERN.findall(text.lower()) if len(w) > 1]
    if not words:
        return None
    return " | ".join(words)


def fulltext_search(query: str, school_id: str | None = None, limit: int = 5) -> list[dict]:
    """
    對 document_chunks 做全文檢索，依相關度排序回傳前 limit 筆。
    回傳格式比照教授 chunk 資料（chunk_text 格式），可直接餵給 format_context_for_prompt。
    """
    tsquery_str = _build_or_tsquery(query)
    if not tsquery_str:
        return []

    conn = get_connection()
    if not conn:
        print("[FulltextSearch] 無法取得資料庫連線")
        return []

    sql = """
        SELECT dc.school_id,
               u.name AS university_name,
               dc.chunk_text,
               dc.source_url,
               ts_rank(dc.fts_vector, to_tsquery('simple', %(q)s)) AS rank
        FROM document_chunks dc
        LEFT JOIN universities u ON u.school_id = dc.school_id
        WHERE dc.fts_vector @@ to_tsquery('simple', %(q)s)
    """
    params: dict = {"q": tsquery_str}
    if school_id:
        sql += " AND dc.school_id = %(school_id)s"
        params["school_id"] = school_id
    sql += " ORDER BY rank DESC LIMIT %(limit)s"
    params["limit"] = limit

    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        return [dict(zip(columns, row)) for row in rows if dict(zip(columns, row)).get("chunk_text")]
    except Exception as e:
        print(f"[FulltextSearch] 查詢失敗：{e}")
        return []
    finally:
        conn.close()
