"""Agent state and execution context."""

from __future__ import annotations

import threading
from typing import Callable, Optional, TypedDict

class AgentCancelledError(Exception):
    """由取消事件觸發，用來快速中止 agent 執行。"""


# ─── 學校別名對照表 ───────────────────────────────────────────────────────────

_SCHOOL_ALIASES: dict[str, list[str]] = {
    "cmu":          ["cmu", "carnegie mellon", "卡內基梅隆"],
    "mit":          ["mit", "massachusetts institute", "麻省理工"],
    "stanford":     ["stanford", "史丹佛", "斯坦福"],
    "caltech":      ["caltech", "california institute", "加州理工"],
    "gatech":       ["georgia tech", "gatech", "喬治亞理工"],
    "ucla":         ["ucla", "uc los angeles", "加州洛杉磯"],
    "ucsd":         ["ucsd", "uc san diego", "加州聖地牙哥"],
    "umass":        ["umass", "amherst", "麻州大學"],
    "berkeley":     ["berkeley", "uc berkeley", "柏克萊"],
    "washington":   ["university of washington", "uw", "華盛頓大學"],
    "uiuc":         ["uiuc", "illinois", "urbana-champaign", "伊利諾"],
    "cornell":      ["cornell", "康乃爾"],
    "princeton":    ["princeton", "普林斯頓"],
    "columbia":     ["columbia", "哥倫比亞"],
    "upenn":        ["upenn", "university of pennsylvania", "penn", "賓州大學", "賓夕法尼亞"],
    "umich":        ["umich", "michigan", "密西根"],
    "utaustin":     ["ut austin", "university of texas at austin", "texas austin", "德州大學奧斯汀"],
    "ucsb":         ["ucsb", "santa barbara", "聖塔芭芭拉"],
    "uci":          ["uci", "uc irvine", "irvine", "爾灣"],
    "purdue":       ["purdue", "普渡"],
    "wisc":         ["wisconsin", "madison", "威斯康辛"],
    "umd":          ["umd", "maryland", "college park", "馬里蘭"],
    "nyu":          ["nyu", "new york university", "紐約大學"],
    "ucsc":         ["ucsc", "santa cruz", "聖塔克魯茲"],
    "rice":         ["rice", "萊斯大學"],
    "northeastern": ["northeastern", "東北大學"],
    "ucdavis":      ["ucdavis", "uc davis", "戴維斯"],
    "osu":          ["ohio state", "osu", "俄亥俄州立"],
    "duke":         ["duke", "杜克"],
    "rutgers":      ["rutgers", "羅格斯"],
}

# Thread-local execution context prevents concurrent API requests from sharing callbacks.
_agent_context = threading.local()


def _check_cancel() -> None:
    """若取消事件已設定，立即拋出 AgentCancelledError。"""
    cancel_event = getattr(_agent_context, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelledError("Agent 已被取消")

# ─── State ───────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    original_query:    str
    sub_queries:       list[str]
    collected_docs:    list[dict]    # SQL 查詢結果（每輪由 searcher_node 整份覆寫，重試時不與前一輪疊加）
    fulltext_docs:     list[dict]    # 全文檢索結果（SQL 不足時才由 fulltext_search_node 補上）
    fulltext_done:     bool          # 是否已對本輪子問題做過全文檢索 fallback（避免重複做）
    extension_docs:    list[dict]    # 教授資料（每輪由 extension_function_node 整份覆寫，重試時不與前一輪疊加）
    experience_docs:   list[dict]    # 申請經驗回報（needs_experience 時由 experience_search_node 整份覆寫）
    final_answer:      str
    professor_query:   dict | None   # Decomposer 偵測到的教授查詢 {name, school, school_id}
    needs_sql_search:  bool         # Decomposer 判斷是否需要查 programs 申請要求資料
    needs_experience:  bool         # Decomposer 判斷是否問「錄取背景/機會/案例」，需查 applicant_reports
    mentioned_school_ids: list[str]   # Decomposer 從已知清單中偵測到的 school_id（不代表資料庫已收錄）
    mentioned_school_names: list[str] # Decomposer 偵測到的學校名稱原文（不限於已知清單）
    verified_docs:     list[dict]    # Verifier 去重後的資料（search + extension 合併）
    is_sufficient:     bool          # Verifier 判斷資料是否足以回答問題
    insufficiency_reason: str        # 資料不足或文不對題時的簡短原因
    retry_count:       int           # 已重試次數（Refiner 重新查詢的次數，上限 1 輪）
    generated_answer:  bool          # finalizer 是否真的呼叫 LLM 生成過答案（決定是否需要跑 Critic）


# ─── 工具函式 ─────────────────────────────────────────────────────────────────

def _emit(event: dict) -> None:
    """安全地呼叫目前的 on_event callback（若有設定）。"""
    on_event = getattr(_agent_context, "on_event", None)
    if on_event is not None:
        try:
            on_event(event)
        except Exception:
            pass


def _detect_school_ids(text: str) -> list[str]:
    """從文字中偵測提到的學校 ID 列表。"""
    text_lower = text.lower()
    return [
        school_id
        for school_id, aliases in _SCHOOL_ALIASES.items()
        if any(alias in text_lower for alias in aliases)
    ]


def create_initial_state(query: str) -> AgentState:
    """Build a fresh state for each agent execution."""
    return {
        "original_query": query,
        "sub_queries": [],
        "collected_docs": [],
        "fulltext_docs": [],
        "fulltext_done": False,
        "extension_docs": [],
        "experience_docs": [],
        "final_answer": "",
        "professor_query": None,
        "needs_sql_search": True,
        "needs_experience": False,
        "mentioned_school_ids": [],
        "mentioned_school_names": [],
        "verified_docs": [],
        "is_sufficient": True,
        "insufficiency_reason": "",
        "retry_count": 0,
        "generated_answer": False,
    }


def set_execution_context(
    on_event: Optional[Callable[[dict], None]],
    cancel_event: Optional[threading.Event],
) -> None:
    _agent_context.on_event = on_event
    _agent_context.cancel_event = cancel_event


def clear_execution_context() -> None:
    _agent_context.on_event = None
    _agent_context.cancel_event = None
