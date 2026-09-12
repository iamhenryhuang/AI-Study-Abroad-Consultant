"""把現有檢索函式包成 ReAct 迴圈可調度的工具。

設計原則（對照公平性）：
  1. 工具集與現有 agent 對齊——就是這 6 個能力，不多不少。
  2. 工具「薄」：只把 LLM 給的參數轉呼叫現成函式、把回傳正規化成 list[dict]，
     不搬現有 agent 在節點外做的加值邏輯（學校偵測、去重、sparse 判斷、背景補爬）。
     那些屬於「pipeline 的後處理」，不屬於「檢索能力」本身；搬進來會讓對照不公平。
  3. 不做學校偵測：school_id 由 LLM 從問題自行推斷後傳入（對不到就查空，如實反映）。

每個工具的介面：fn(**kwargs) -> list[dict]。kwargs 就是 LLM 在 action JSON 裡填的參數。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from professor_fetcher.fetch_for_agent import run_professor_fetch
from retriever.applicant_search import applicant_search
from retriever.hybrid_search import hybrid_search_with_fallback
from retriever.professor_list_search import professor_list_search
from retriever.recommend import recommend
from retriever.sql_search import sql_search


# ─── 工具實作（薄包裝） ────────────────────────────────────────────────────────

def _tool_sql_search(query: str) -> list[dict]:
    """text-to-SQL 查結構化申請要求。sql_search 回 (rows, sql)，這裡只要 rows。"""
    rows, _sql = sql_search(query)
    return rows


def _tool_fulltext_search(query: str, school_id: str | None = None) -> list[dict]:
    """對頁面全文做混合檢索（向量+FTS，自動降級純 FTS）。school_id 由 LLM 傳入。"""
    return hybrid_search_with_fallback(query, school_id=school_id)


def _tool_applicant_search(query: str, school_id: str | None = None) -> list[dict]:
    """查非官方錄取經驗回報（GradCafe / 一畝三分地）。school_id 由 LLM 傳入。"""
    return applicant_search(query, school_id=school_id)


def _tool_professor_list_search(school_id: str) -> list[dict]:
    """列某校收錄的教授名單（種子資料）。"""
    return professor_list_search(school_id)


def _tool_fetch_professor(name: str, school: str, school_id: str,
                          query: str = "") -> list[dict]:
    """指名教授即時查詢（SerpAPI/Google Scholar）。組成 run_professor_fetch 要的 dict。"""
    professor_query = {"name": name, "school": school, "school_id": school_id}
    return run_professor_fetch(professor_query, query)


def _tool_recommend(profile: dict) -> list[dict]:
    """依 profile 分級推薦（衝刺/適中/保底）。recommend 回 tier 分組 dict，這裡攤平
    成 list[dict] 與其他工具一致，並把 tier 標回每一筆避免資訊遺失。"""
    tiers = recommend(profile)
    flat: list[dict] = []
    for tier, schools in tiers.items():
        for s in schools:
            flat.append({**s, "tier": tier})
    return flat


# ─── 工具註冊表：name → {fn, description}。description 是給 LLM 看的調度依據 ──────

TOOLS: dict[str, dict] = {
    "sql_search": {
        "fn": _tool_sql_search,
        "description": (
            "查官方申請要求的結構化欄位（GPA / TOEFL / IELTS / GRE / 截止日 / 學費 / "
            "推薦信數 / 獎助等）。參數：query（自然語言問題，內含學校名時本工具會自行轉 SQL）。"
        ),
    },
    "fulltext_search": {
        "fn": _tool_fulltext_search,
        "description": (
            "當結構化欄位查不到（如學分數、課程規定、細部條件）時，對學校官網頁面全文做檢索。"
            "參數：query（要找的內容）、school_id（小寫學校代碼，如 mit/cmu/stanford；不確定可省略）。"
        ),
    },
    "applicant_search": {
        "fn": _tool_applicant_search,
        "description": (
            "查非官方的網路錄取經驗回報（某分數有沒有機會、錄取者背景、案例）。"
            "注意：這是有樣本偏誤的經驗談，不是官方門檻。"
            "參數：query、school_id（小寫學校代碼，可省略）。"
        ),
    },
    "professor_list_search": {
        "fn": _tool_professor_list_search,
        "description": (
            "列出某校收錄的教授名單與研究領域（問「某校有哪些教授」時用）。"
            "參數：school_id（小寫學校代碼，必填）。"
        ),
    },
    "fetch_professor": {
        "fn": _tool_fetch_professor,
        "description": (
            "指名某位教授、即時抓其研究領域與最新論文（問題已給明確教授姓名時用）。"
            "參數：name（教授姓名）、school（學校全名）、school_id（小寫代碼）、query（原始問題）。"
        ),
    },
    "recommend": {
        "fn": _tool_recommend,
        "description": (
            "依使用者成績檔案把各校分成衝刺/適中/保底並附相近錄取案例（使用者上傳 GPA/TOEFL/GRE "
            "要選校時用）。參數：profile（如 {\"gpa\":3.5,\"toefl\":100,\"gre\":320}）。"
        ),
    },
}
