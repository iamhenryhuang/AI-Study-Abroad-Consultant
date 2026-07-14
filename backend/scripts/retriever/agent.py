"""Backward-compatible script entry point for the retriever agent package."""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from retriever.sql_search import sql_search, get_known_school_ids
from retriever.hybrid_search import hybrid_search_with_fallback
from retriever.applicant_search import applicant_search
from generator.openai_client import (
    call_llm, generate_answer, generate_answer_stream,
    format_context_for_prompt, clean_answer_text,
)
from professor_fetcher.fetch_for_agent import run_professor_fetch

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


def _deduplicate_docs(docs: list[dict]) -> list[dict]:
    """以 school_id + chunk_text/完整欄位內容去重複文件。

    對結構化 SQL doc 用完整內容的 hash 當 key（不截斷），避免只有尾端欄位
    （如 application_url）不同的兩筆 doc 被前綴截斷誤判為重複。
    """
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        content = doc.get("chunk_text") or json.dumps(doc, sort_keys=True, default=str)
        key = f"{doc.get('school_id', '')}:{hashlib.md5(content.encode('utf-8')).hexdigest()}"
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


def _call_llm(prompt: str) -> str:
    """呼叫 OpenAI，回傳純文字。每次呼叫前先檢查是否已取消。"""
    _check_cancel()
    return call_llm(prompt)


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


# ─── Node 1：Decomposer ───────────────────────────────────────────────────────

def _build_intent_prompt(query: str) -> str:
    """任務一：學校辨識 + 教授查詢偵測 + 判斷是否需要查 SQL（決定路由）。"""
    return f"""你是一個 Query Intent 分析助理，負責從使用者問題辨識學校、教授查詢意圖，並判斷是否需要查詢結構化資料庫。


__all__ = ["AgentCancelledError", "AgentState", "run_agent"]


if __name__ == "__main__":
    from retriever.agent.__main__ import main

    raise SystemExit(main())
