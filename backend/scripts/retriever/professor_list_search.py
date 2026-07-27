"""教授名單檢索：查 professors（種子資料，db/load_professors.py 寫入）。

回答「某校有哪些教授」這類列表型問題；與 professor_fetcher（指名教授查研究細節，
走 SerpAPI/Google Scholar）是互補的兩條路徑——先列名單，使用者再挑一位深入查。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from retriever.db_query import fetch_dicts, readonly_connection


def _format_professor_text(row: dict) -> str:
    areas = row.get("research_areas") or []
    areas_text = "、".join(areas) if areas else "未提供"
    title = row.get("title")
    title_text = f"{title}｜" if title else ""
    return f"{row['name']}（{title_text}研究領域：{areas_text}）"


def professor_list_search(school_id: str, limit: int = 30) -> list[dict]:
    """查指定學校的教授名單，回傳 chunk 格式 doc（type='professor_list'）。"""
    if not school_id:
        return []

    with readonly_connection() as conn:
        if not conn:
            print("[ProfessorListSearch] 無法取得資料庫連線")
            return []

        try:
            rows = fetch_dicts(
                conn,
                """
                SELECT p.name, p.title, p.research_areas, p.profile_url, u.school_id
                FROM professors p
                JOIN universities u ON p.university_id = u.id
                WHERE u.school_id = %(school_id)s
                ORDER BY p.name
                LIMIT %(limit)s
                """,
                {"school_id": school_id, "limit": limit},
            )
        except Exception as e:
            print(f"[ProfessorListSearch] 查詢失敗：{e}")
            return []

    docs = []
    for row in rows:
        docs.append({
            "type":        "professor_list",
            "chunk_text":  _format_professor_text(row),
            "source_url":  row.get("profile_url") or "",
            "school_id":   row.get("school_id") or "",
        })
    return docs
