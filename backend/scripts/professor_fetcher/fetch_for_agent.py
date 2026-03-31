"""
Agent 介面層：對外提供 fetch_professor_docs() 函式。

意圖判斷已由 Decomposer (agent.py) 完成並存入 state，
此模組只負責：呼叫 SerpAPI 抓取教授資料，並轉換為 chunk 格式。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from professor_fetcher.run_fetch import fetch_one


# ─── 對外接口 ──────────────────────────────────────────────────────────────────

def _fetch_professor_docs(name: str, school: str, school_id: str, query: str) -> list[dict]:
    """透過 SerpAPI 抓取指定教授資料，轉換為 chunk 格式。"""
    print(f"[ProfessorFetcher] 抓取：{name} @ {school}（{school_id}）")

    try:
        records = fetch_one(name=name, school=school, school_id=school_id)
    except Exception as e:
        print(f"[ProfessorFetcher] 抓取失敗：{e}")
        return []

    if not records:
        print("[ProfessorFetcher] 無資料回傳")
        return []

    docs = []
    for rec in records:
        text = rec.get("data", "").strip()
        if not text:
            continue
        docs.append({
            "chunk_text":   text,
            "source_url":   rec.get("url", ""),
            "passed_types": rec.get("passed_types", []),
            "school_id":    rec.get("school_id", ""),
            "rerank_score": 0.8,
            "query":        query,
        })

    print(f"[ProfessorFetcher] 共取得 {len(docs)} 筆 chunk 文件")
    return docs


def run_professor_fetch(professor_query: dict | None, query: str) -> list[dict]:
    """
    Extension function node 的單一入口。
    接收 Decomposer 解析出的 professor_query，呼叫 SerpAPI 抓取資料。

    Args:
        professor_query: {"name": str, "school": str, "school_id": str} 或 None
        query:           使用者原始問題

    Returns:
        chunk 格式的 doc 列表；無教授查詢或失敗時回傳空列表。
    """
    if not professor_query:
        return []

    return _fetch_professor_docs(
        name=professor_query["name"],
        school=professor_query["school"],
        school_id=professor_query["school_id"],
        query=query,
    )
