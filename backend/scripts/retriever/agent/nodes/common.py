"""Shared helpers for agent node implementations."""

from __future__ import annotations

import hashlib
import json
import re

from generator.client import call_llm, llm_is_unavailable

from ..state import _SCHOOL_ALIASES, _check_cancel, _detect_school_ids

def _deduplicate_docs(docs: list[dict]) -> list[dict]:
    """以 school_id + chunk_text/完整欄位內容去重複文件。

    對結構化 SQL doc 用完整內容的 hash 當 key（不截斷），避免只有尾端欄位
    （如 application_url）不同的兩筆 doc 被前綴截斷誤判為重複。
    hash 時排除 "query" 欄位（_search_one_query 注入的子問題標記）——
    否則兩個子問題查回同一列資料會因 query 不同而被誤判為不重複。
    """
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        content = doc.get("chunk_text") or json.dumps(
            {k: v for k, v in doc.items() if k != "query"}, sort_keys=True, default=str)
        key = f"{doc.get('school_id', '')}:{hashlib.md5(content.encode('utf-8')).hexdigest()}"
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


def _call_llm(prompt: str) -> str:
    """呼叫 OpenAI，回傳純文字。每次呼叫前先檢查是否已取消。"""
    _check_cancel()
    return call_llm(prompt)


def _llm_is_unavailable() -> bool:
    return llm_is_unavailable()


def _parse_json_object(raw: str) -> dict:
    """從 LLM 回覆中擷取第一個 JSON 物件並解析，失敗時拋出例外。"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("找不到 JSON")
    return json.loads(match.group())


def _parse_professor_query(pq_raw: object) -> dict | None:
    """
    將 LLM 回傳的 professor_query 物件正規化為 {name, school, school_id}。
    school_id 不在已知別名表時，嘗試從 school 名稱重新偵測；仍找不到就沿用原字串。
    decomposer_node 與 refiner_node 共用此邏輯，避免各自維護一份。
    """
    if not isinstance(pq_raw, dict):
        return None
    pq_name = (pq_raw.get("name") or "").strip()
    if not pq_name:
        return None
    pq_school = (pq_raw.get("school") or "").strip()
    pq_sid    = (pq_raw.get("school_id") or "").strip()
    if pq_sid not in _SCHOOL_ALIASES:
        detected = _detect_school_ids(pq_school)
        pq_sid = detected[0] if detected else (pq_school or pq_sid)
    return {"name": pq_name, "school": pq_school, "school_id": pq_sid}
