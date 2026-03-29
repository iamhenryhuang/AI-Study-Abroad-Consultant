"""
Agent 介面層：統一對外提供 fetch_professor_docs(query) 函式。

流程：
  1. LLM 判斷 query 是否為「查詢特定教授」意圖，並提取教授姓名與學校
  2. 呼叫 fetch_one() 透過 SerpAPI 抓取教授資料
  3. 將 records 轉換為 agent pipeline 相容的 chunk 格式
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from generator.gemini import get_gemini_client
from professor_fetcher.run_fetch import fetch_one, infer_school_id


# ─── 學校別名（與 agent.py 保持一致） ──────────────────────────────────────────

_SCHOOL_ALIASES: dict[str, list[str]] = {
    "cmu":      ["cmu", "carnegie mellon", "卡內基梅隆"],
    "mit":      ["mit", "massachusetts institute", "麻省理工"],
    "stanford": ["stanford", "史丹佛", "斯坦福"],
    "caltech":  ["caltech", "california institute", "加州理工"],
    "gatech":   ["georgia tech", "gatech", "喬治亞理工"],
    "ucla":     ["ucla", "uc los angeles", "加州洛杉磯"],
    "ucsd":     ["ucsd", "uc san diego", "加州聖地牙哥"],
    "uci":      ["uci", "uc irvine", "加州爾灣"],
    "umass":    ["umass", "amherst", "麻州大學"],
    "purdue":   ["purdue", "purdure", "普渡"],
    "washu":    ["washu", "wustl", "washington university", "聖路易斯華盛頓"],
    "utoronto": ["utoronto", "toronto", "多倫多"],
}


# ─── 內部工具函式 ──────────────────────────────────────────────────────────────

def _call_llm(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    """呼叫 Gemini，回傳純文字。"""
    client = get_gemini_client()
    response = client.models.generate_content(model=model_name, contents=prompt)
    return (response.text or "").strip()


def _parse_professor_query(query: str) -> dict | None:
    """
    使用 LLM 判斷是否為「查詢特定教授」的 query，並提取教授姓名與學校。

    Returns: {"name": str, "school": str, "school_id": str}
    Returns None 若不是教授相關 query 或找不到具體教授姓名。
    """
    prompt = f"""判斷以下問題是否在詢問「某位具體教授」的相關資訊。

【使用者問題】
{query}

【已知學校清單】（school_id 只能從此清單選取）
{list(_SCHOOL_ALIASES.keys())}

【判斷規則】
- 若問題中提到「具體的教授姓名」，輸出該教授的姓名與學校
- 若問題與尋找特定教授無關（例如：申請要求、學費、截止日期等），輸出 unknown

【輸出格式（嚴格遵守，只輸出 JSON）】
若有具體教授姓名：
{{
  "mode": "by_name",
  "professor_name": "教授全名（英文）",
  "school_name": "學校名稱（英文）",
  "school_id": "學校ID（從已知清單選取，不確定填空字串）"
}}

否則：
{{"mode": "unknown"}}
"""
    raw = _call_llm(prompt)
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        parsed    = json.loads(match.group())
        if parsed.get("mode") != "by_name":
            return None
        name      = parsed.get("professor_name", "").strip()
        school    = parsed.get("school_name", "").strip()
        school_id = parsed.get("school_id", "").strip()
        if not name:
            return None
        if school_id not in _SCHOOL_ALIASES:
            school_id = infer_school_id(school) if school else ""
        return {"name": name, "school": school, "school_id": school_id}
    except Exception as e:
        print(f"[ProfessorFetcher] LLM 解析失敗：{e}")
        return None


def _records_to_docs(records: list[dict], query: str) -> list[dict]:
    """
    將 format_professor_to_json 回傳的 records 轉為 agent pipeline 相容的 doc 格式。
    """
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
    return docs


# ─── 對外接口 ──────────────────────────────────────────────────────────────────

def fetch_professor_docs(query: str) -> list[dict]:
    """
    從自然語言 query 中提取具體教授姓名，並透過 SerpAPI 抓取 Google Scholar 資料。

    Args:
        query: 使用者原始問題

    Returns:
        chunk 格式的 doc 列表（可直接加入 agent collected_docs）；
        若非教授相關 query、找不到姓名或抓取失敗，回傳空列表。
    """
    print(f"\n[ProfessorFetcher] 開始解析 query 意圖...")

    parsed = _parse_professor_query(query)
    if not parsed:
        print("[ProfessorFetcher] 非教授相關 query，跳過")
        return []

    name      = parsed["name"]
    school    = parsed["school"]
    school_id = parsed["school_id"]

    print(f"[ProfessorFetcher] 教授：{name}  學校：{school}（{school_id}）")

    try:
        records = fetch_one(name=name, school=school, school_id=school_id)
    except Exception as e:
        print(f"[ProfessorFetcher] 抓取失敗：{e}")
        return []

    if not records:
        print("[ProfessorFetcher] 無資料回傳")
        return []

    docs = _records_to_docs(records, query)
    print(f"[ProfessorFetcher] 共取得 {len(docs)} 筆 chunk 文件")
    return docs
