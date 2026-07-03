"""
流程：
  1. Decomposer       — LLM 拆解子問題 + 偵測學校 + 偵測教授查詢意圖
  1.5 ProfessorFetcher — 若 query 含教授意圖，呼叫 SerpAPI 即時抓取教授資料
  2. SQLSearcher      — 每個子問題透過 text-to-SQL 查詢結構化申請要求資料
  3. Finalizer        — 彙整所有資料，呼叫 OpenAI 生成最終答案
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Annotated, Callable, Optional, TypedDict
import operator
import time


class AgentCancelledError(Exception):
    """由取消事件觸發，用來快速中止 agent 執行。"""

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from retriever.sql_search import sql_search
from generator.openai_client import call_llm, generate_answer, generate_answer_stream
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
    collected_docs:    Annotated[list[dict], operator.add]   # SQL 查詢結果
    extension_docs:    Annotated[list[dict], operator.add]   # 教授資料
    final_answer:      str
    professor_query:   dict | None   # Decomposer 偵測到的教授查詢 {name, school, school_id}
    professor_fetched: bool
    needs_sql_search:  bool         # Decomposer 判斷是否需要查 program_requirements


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
    """以 school_id + chunk_text/欄位內容去重複文件。"""
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        key = doc.get("chunk_text") or json.dumps(doc, sort_keys=True, default=str)
        key = f"{doc.get('school_id', '')}:{key[:200]}"
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


def _call_llm(prompt: str) -> str:
    """呼叫 OpenAI，回傳純文字。每次呼叫前先檢查是否已取消。"""
    _check_cancel()
    return call_llm(prompt)


# ─── Node 1：Decomposer ───────────────────────────────────────────────────────

def decomposer_node(state: AgentState) -> dict:
    query = state["original_query"]
    print(f"\n{'='*60}")
    print(f"[Decomposer] 原始問題：{query}")
    print(f"{'='*60}")
    prompt = f"""
你是一個 Query Decomposer，負責將使用者問題轉換為結構化資訊。

你需要完成兩個任務，並嚴格遵守規則輸出 JSON。
[嚴格遵守] decompose的問題最多9個，只有一間學校的話，一間最多5個，兩間學校的話，一間最多四個，若有三間以上，單一學校最多三個問題

====================
【任務一：學校辨識 + 子問題拆解】
====================
1. 從使用者問題中辨識出所有「明確提及」的學校（必須出現在已知學校清單中）
2. 若偵測到多所學校：
   - 為每一所學校產生一個對應的子問題
3. 若只偵測到一所學校：
   - 產生 1 個子問題
4. 若沒有偵測到任何學校：
   - school_ids = []
   - sub_queries = [將原問題改寫為較清楚的單一問題]
5. 若問題太過於廣泛，如只詢問了application requirement、申請資格等等這種廣泛問題，請在子問題種搜尋細項，
    請將廣泛問題的細項分開詢問，細項詢問甚麼由你判斷，參考範例如下。
    問題:application requirement 子問題拆成:min gpa/min english score/min gre/等等，類似這樣的子問題找答案

【子問題格式要求】
- 所有子問題必須用英文撰寫，無論使用者原始問題是何種語言
- 優先使用：「School + requirement content?」形式
- 保持語意完整，不要遺漏條件
- 不要自行新增未提及的資訊
- 若有分數相關問題如GRE/GPA/English test，請都要詢問 是否需要、最低分多少

====================
【任務二：教授查詢偵測（professor_query）】
====================
判斷問題是否在詢問「某位具體教授」的相關資訊。

- 若問題中有「明確的教授姓名」，提取其姓名與學校，professor_query 為物件
- 姓名必須完整保留原始寫法，包括連字號（-）與大小寫，例如 "Fei-Fei Li" 不得改寫為 "Feifei Li"
- 否則 professor_query 為 null

====================
【任務三：是否需要查詢結構化申請要求資料庫（needs_sql_search）】
====================
資料庫（program_requirements）只有 GPA、TOEFL/IELTS/GRE、申請截止日期等「申請要求」欄位，
不包含教授的研究領域、發表論文、經歷等資訊。

- 若使用者問題只在詢問教授本人相關資訊（如研究領域、論文、背景等），且完全沒有詢問任何申請要求，
  needs_sql_search 設為 false，此時 sub_queries 可以是空陣列 []
- 若使用者問題除了教授資訊外，還有詢問任何申請要求（GPA/TOEFL/IELTS/GRE/截止日期等），
  needs_sql_search 設為 true，並依任務一產生 sub_queries
- 若問題完全沒有教授查詢意圖，needs_sql_search 一律為 true

====================
【使用者問題】
{query}

【已知學校清單】（school_ids 只能從此清單選取；professor_query.school_id 若學校不在清單內，填寫學校英文全名即可）
{list(_SCHOOL_ALIASES.keys())}

====================
【輸出格式（嚴格遵守）】
====================
只輸出 JSON，禁止輸出任何說明文字

{{
  "school_ids": ["school_id_1", ...],
  "sub_queries": ["sub-query in English 1", "sub-query in English 2", ...],
  "professor_query": {{"name": "教授全名（英文）", "school": "學校名稱（英文）", "school_id": "學校ID"}} or null,
  "needs_sql_search": true or false
}}
"""

    raw = _call_llm(prompt)

    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("找不到 JSON")
        parsed      = json.loads(match.group())
        school_ids  = [
            str(s).strip()
            for s in parsed.get("school_ids", [])
            if str(s).strip() in _SCHOOL_ALIASES
        ]
        sub_queries = [str(q).strip() for q in parsed.get("sub_queries", []) if str(q).strip()]

        pq_raw = parsed.get("professor_query")
        professor_query = None
        if isinstance(pq_raw, dict):
            pq_name   = pq_raw.get("name", "").strip()
            pq_school = pq_raw.get("school", "").strip()
            pq_sid    = pq_raw.get("school_id", "").strip()
            if pq_name:
                if pq_sid not in _SCHOOL_ALIASES:
                    detected = _detect_school_ids(pq_school)
                    pq_sid = detected[0] if detected else (pq_school or pq_sid)
                professor_query = {"name": pq_name, "school": pq_school, "school_id": pq_sid}

        needs_sql_search = bool(parsed.get("needs_sql_search", True))

        if not sub_queries and needs_sql_search:
            raise ValueError("sub_queries 為空")

    except Exception as e:
        print(f"[Decomposer] LLM 解析失敗（{e}），使用原始問題作為 fallback")
        school_ids       = _detect_school_ids(query)
        sub_queries      = [query]
        professor_query  = None
        needs_sql_search = True

    if len(school_ids) >= 2:
        print(f"[Decomposer] 偵測到 {len(school_ids)} 所學校，拆解為 {len(sub_queries)} 個子問題：")
    else:
        label = f"（學校：{school_ids[0]}）" if school_ids else "（無特定學校）"
        print(f"[Decomposer] 單一問題 {label}：")
    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")

    if professor_query:
        print(f"[Decomposer] professor_query = {professor_query['name']} @ {professor_query['school']} "
              f"[{professor_query.get('school_id', '?')}]")
    else:
        print("[Decomposer] professor_query = None（無教授查詢意圖）")
    print(f"[Decomposer] needs_sql_search = {needs_sql_search}")

    return {
        "sub_queries":      sub_queries,
        "collected_docs":   [],
        "final_answer":     "",
        "professor_query":  professor_query,
        "needs_sql_search": needs_sql_search,
    }


def after_decompose(state: AgentState):
    """
    Decomposer 完成後的路由：
    - 有教授查詢 + 需要 SQL → search + extension_function 並行
    - 只查教授、不需要 SQL → 只走 extension_function
    - 一般問題（無教授查詢）→ 只走 search
    """
    professor_query  = state.get("professor_query")
    needs_sql_search = state.get("needs_sql_search", True)

    if professor_query is not None:
        if needs_sql_search:
            return [Send("search", state), Send("extension_function", state)]
        return [Send("extension_function", state)]
    return [Send("search", state)]


# ─── Node 1.5：Extension Function（教授查詢） ─────────────────────────────────

def extension_function_node(state: AgentState) -> dict:
    """擴充功能節點（與 search 並行）：抓取教授資料，寫入 extension_docs。"""
    _emit({"type": "thinking", "step": "extension_function"})

    extension_docs: list[dict] = []
    professor_fetched = False

    professor_query = state.get("professor_query")
    if professor_query is not None:
        print(f"[Extension] 抓取教授資料：{professor_query.get('name')}")
        docs = run_professor_fetch(professor_query, state["original_query"])
        if docs:
            _emit({
                "type": "tool_result",
                "tool": "fetch_professor",
                "preview": f"找到 {len(docs)} 筆教授相關資料",
            })
            professor_fetched = True
            extension_docs.extend(docs)
        else:
            print("[Extension] 未抓到教授資料")

    print(f"[Extension] 共取得 {len(extension_docs)} 筆擴充資料")
    return {
        "extension_docs":    extension_docs,
        "professor_fetched": professor_fetched,
    }


# ─── Node 2：SQL Searcher ─────────────────────────────────────────────────────

def _search_one_query(q: str, original_query: str) -> list[dict]:
    """對單一子問題執行 text-to-SQL 檢索。"""
    school_ids = _detect_school_ids(q) or _detect_school_ids(original_query)
    school_id = school_ids[0] if school_ids else None

    _emit({
        "type": "tool_call",
        "tool": "sql_search",
        "args": {"query": q, **({"school_id": school_id} if school_id else {})},
    })

    results, sql = sql_search(q)

    for item in results:
        item["query"] = q

    _emit({
        "type": "tool_result",
        "tool": "sql_search",
        "preview": f"找到 {len(results)} 筆相關資料" + (f"（SQL: {sql}）" if sql else ""),
    })

    return results


def searcher_node(state: AgentState) -> dict:
    """對所有子問題執行 text-to-SQL 檢索，一輪到位（無重試迴圈）。"""
    sub_queries     = state.get("sub_queries", [])
    original_query = state["original_query"]

    print(f"\n[Searcher] 共 {len(sub_queries)} 個子問題")
    _emit({"type": "thinking", "step": 1})

    new_docs: list[dict] = []

    for q in sub_queries:
        _check_cancel()
        results = _search_one_query(q, original_query)
        print(f"     查詢：{q}  取得 {len(results)} 筆")
        new_docs.extend(results)

    print(f"\n[Searcher] 共取得 {len(new_docs)} 筆資料")

    return {"collected_docs": new_docs}


# ─── Node 3：Finalizer ────────────────────────────────────────────────────────

def finalizer_node(state: AgentState) -> dict:
    """
    彙整所有收集到的資料（search 的 collected_docs + extension 的 extension_docs），
    呼叫 OpenAI 生成最終答案，並發送 answer 事件。
    """
    query = state["original_query"]

    search_docs    = state.get("collected_docs", [])
    extension_docs = state.get("extension_docs", [])

    # extension_docs 放在前面（教授資料優先作為上下文）
    all_docs = _deduplicate_docs(extension_docs + search_docs)

    print(
        f"\n[Finalizer] 搜尋文件：{len(search_docs)} 筆  "
        f"擴充文件：{len(extension_docs)} 筆  "
        f"合併去重後：{len(all_docs)} 筆"
    )

    if not all_docs:
        answer = "很抱歉，未能從資料庫中找到相關資訊。建議您直接前往各校官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    _check_cancel()
    _emit({"type": "llm_call", "purpose": "finalizer"})

    full_text = ""
    try:
        for chunk in generate_answer_stream(query, all_docs):
            _check_cancel()
            full_text += chunk
            if chunk:
                _emit({"type": "answer_chunk", "text": chunk})
    except Exception as e:
        print(f"[Finalizer] 串流失敗，回退到非串流: {e}")
        full_text = generate_answer(query, all_docs) or ""

    if full_text:
        import re as _re
        clean = full_text.replace("**", "")
        clean = _re.sub(
            r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
            r"\1", clean, flags=_re.IGNORECASE,
        )
        clean = _re.sub(r"</?span[^>]*>", "", clean, flags=_re.IGNORECASE)
        clean = clean.strip()
        _emit({"type": "answer", "text": clean})
        return {"final_answer": clean}
    else:
        msg = "OpenAI 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": None}


# ─── 路由 & 圖建構 ────────────────────────────────────────────────────────────

def after_professor_fetch(state: AgentState) -> str:
    return "finalize"


def _build_graph():
    """
    流程：
      decompose
        ├─ Send → search ──────────────────────────┐
        │                                          ▼
        └─ Send → extension_function ──────────► finalize
    兩條並行路徑（search / extension_function）fan-in 到 finalize。
    SQL 查詢通常一次到位，故不再有 planner 重試迴圈。
    """
    builder = StateGraph(AgentState)

    builder.add_node("decompose",          decomposer_node)
    builder.add_node("extension_function", extension_function_node)
    builder.add_node("search",             searcher_node)
    builder.add_node("finalize",           finalizer_node)

    builder.set_entry_point("decompose")

    builder.add_conditional_edges("decompose", after_decompose)

    builder.add_edge("extension_function", "finalize")
    builder.add_edge("search", "finalize")

    builder.add_edge("finalize", END)

    return builder.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# ─── 公開 API ─────────────────────────────────────────────────────────────────

def run_agent(
    query: str,
    max_steps: int = 10,
    verbose: bool = True,
    on_event: Optional[Callable[[dict], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str | None:
    """
    執行 Agentic RAG 流程。

    Args:
        query:     使用者問題
        max_steps: LangGraph 最大步驟數（安全閥）
        verbose:   是否顯示詳細日誌
        on_event:  SSE 事件 callback，格式：{"type": "thinking"|"tool_call"|..., ...}

    Returns:
        最終回答字串，或 None
    """
    _agent_context.on_event = on_event
    _agent_context.cancel_event = cancel_event

    try:
        if verbose:
            print(f"\n{'='*60}")
            print(f"   Agentic RAG 啟動")
            print(f"   Agentic RAG 啟動時間 : {time.localtime()}")
            print(f"   問題：{query}")
            print(f"{'='*60}")

        initial_state: AgentState = {
            "original_query":    query,
            "sub_queries":       [],
            "collected_docs":    [],
            "extension_docs":    [],
            "final_answer":      "",
            "professor_query":   None,
            "professor_fetched": False,
            "needs_sql_search":  True,
        }

        result_state = _get_graph().invoke(initial_state, {"recursion_limit": max_steps * 4})

        if verbose:
            total_docs = len(_deduplicate_docs(result_state.get("collected_docs", [])))
            print(f"\n{'='*60}")
            print(f"完成！彙整 {total_docs} 筆去重資料")
            print(f"{'='*60}")
        print(f"   Agentic RAG 結束時間 : {time.localtime()}")
        return result_state.get("final_answer") or None

    except AgentCancelledError:
        print("[Agent] 收到取消信號，提前結束。")
        return None

    finally:
        _agent_context.on_event = None
        _agent_context.cancel_event = None


# ─── CLI 入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import io

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="Agentic RAG（LangGraph）")
    parser.add_argument("query",       nargs="?", help="使用者問題")
    parser.add_argument("--max-steps", type=int, default=20, help="最大 LangGraph 步驟數")
    args = parser.parse_args()

    q = args.query or input("請輸入問題：").strip()
    if not q:
        print("未輸入問題。")
        sys.exit(1)

    answer = run_agent(q, max_steps=args.max_steps, verbose=True)
    if answer:
        print("\n" + "=" * 30 + " 最終回答 " + "=" * 30)
        print(answer)
        print("=" * 70 + "\n")
    else:
        print("生成回答失敗。")
        sys.exit(1)
