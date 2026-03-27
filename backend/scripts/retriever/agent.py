"""
流程：
  1. Planner-Decompose — 使用 LLM 將原始問題拆成最多 5 個子問題
  2. Searcher          — 對當前子問題（或 follow-up）執行混合檢索
  3. Planner-Evaluate  — 判斷該子問題的資料是否充足（僅在第 1 輪後執行）
                         · 充足      → Advance（推進到下一個子問題）
                         · 不充足    → 換角度追問，回到 Searcher（第 2 輪）
  4. Advance           — 推進到下一個子問題，或標記全部完成
                         · 有更多子問題 → 回到 Searcher（第 1 輪）
                         · 全部完成     → Finalizer
  5. Finalizer         — 彙整所有文件，呼叫 Gemini 生成最終答案

每個子問題最多 2 輪搜尋（第 1 輪 + 1 輪 follow-up）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Annotated, Callable, Optional, TypedDict
import operator

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from retriever.search import search_core
from generator.gemini import get_gemini_client, generate_answer


# ─── 學校別名對照表 ───────────────────────────────────────────────────────────

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

TOP_K_PER_QUERY = 15

# 模組層級的 on_event callback（執行期間設定）
_current_on_event: Optional[Callable[[dict], None]] = None


# ─── State ───────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    original_query:   str
    sub_queries:      list[str]                              # 初始拆解的所有子問題
    current_sub_idx:  int                                    # 目前處理到第幾個子問題
    current_round:    int                                    # 當前子問題的輪次（0 或 1）
    pending_query:    str                                    # 下一次要搜尋的問題
    followup_query:   str                                    # Planner-Evaluate 產生的追問
    is_sufficient:    bool                                   # Planner-Evaluate 的判斷結果
    collected_docs:   Annotated[list[dict], operator.add]   # 累積所有文件
    searched_queries: Annotated[list[str], operator.add]    # 累積已搜尋的問題
    final_answer:     str


# ─── 工具函式 ─────────────────────────────────────────────────────────────────

def _emit(event: dict) -> None:
    if _current_on_event is not None:
        try:
            _current_on_event(event)
        except Exception:
            pass


def _detect_school_ids(text: str) -> list[str]:
    text_lower = text.lower()
    return [
        school_id
        for school_id, aliases in _SCHOOL_ALIASES.items()
        if any(alias in text_lower for alias in aliases)
    ]


def _deduplicate_docs(docs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        key = doc.get("chunk_text", "")[:200]
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


def _call_llm(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    client = get_gemini_client()
    response = client.models.generate_content(model=model_name, contents=prompt)
    return (response.text or "").strip()


def _search_one_query(q: str, original_query: str) -> tuple[list[dict], str | None]:
    school_ids = _detect_school_ids(q) or _detect_school_ids(original_query)
    school_id = school_ids[0] if school_ids else None

    _emit({
        "type": "tool_call",
        "tool": "search_school" if school_id else "search_general",
        "args": {"query": q, **({"school_id": school_id} if school_id else {})},
    })

    results = search_core(query=q, top_k=TOP_K_PER_QUERY, use_rerank=True, school_id=school_id)

    _emit({
        "type": "tool_result",
        "tool": "search_school" if school_id else "search_general",
        "preview": f"找到 {len(results)} 筆相關文獻",
    })

    return results, school_id


# ─── Node 1：Planner-Decompose ────────────────────────────────────────────────

def planner_decompose_node(state: AgentState) -> dict:
    """使用 LLM 將原始問題拆成最多 5 個英文子問題，並初始化搜尋狀態。"""
    query = state["original_query"]
    print(f"\n[Planner-Decompose] 原始問題：{query}")

    _emit({"type": "llm_call", "purpose": "planner_decompose"})

    prompt = f"""你是一個留學諮詢資料庫查詢助理。請分析以下使用者問題，將其拆解為適合資料庫檢索的子問題。

【使用者問題】
{query}

【拆解規則】
1. 拆解原則：以「學校 + 需求問題」為基本單位
   - 若問題提到多所學校，每所學校各產生一個子問題（學校A + 問題、學校B + 問題）
   - 不得自行發明原始問題中沒有提到的面向
   - 忠實反映使用者原本問的內容，不要猜測或擴充

2. 若問題只提到一所學校（或沒有特定學校）：
   - 問題夠具體 → 只輸出 1 個子問題
   - 問題涉及多個明確面向（如：申請截止日期 AND GRE要求）→ 每個面向各一個子問題，最多 5 個

3. 所有子問題必須用英文撰寫，格式：[School Name] + [specific requirement]

4. 若問題涉及英語能力（TOEFL/IELTS/Duolingo）或 GRE，子問題需包含：
   - 是否需要該考試
   - 最低接受分數

【範例】
- 使用者問：「CMU 和 Stanford 的 GRE 要求」
  → ["CMU CS master GRE requirement and minimum score", "Stanford CS master GRE requirement and minimum score"]
- 使用者問：「Caltech CS master 的申請截止日期和 GPA 要求」
  → ["Caltech CS master application deadline", "Caltech CS master minimum GPA requirement"]
- 使用者問：「MIT 的 TOEFL 最低分數」
  → ["MIT minimum TOEFL score requirement for admission"]

輸出格式（JSON）：
{{
  "sub_queries": [
    "English sub-question 1",
    "English sub-question 2"
  ]
}}

只輸出 JSON，不要有其他文字。"""

    raw = _call_llm(prompt)

    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group())
        sub_queries = [str(q).strip() for q in parsed.get("sub_queries", []) if str(q).strip()][:5]
    except Exception as e:
        print(f"[Planner-Decompose] 解析失敗：{e}，使用原始問題")
        sub_queries = []

    if not sub_queries:
        sub_queries = [query]

    print(f"[Planner-Decompose] 拆解為 {len(sub_queries)} 個子問題：")
    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")

    return {
        "sub_queries":      sub_queries,
        "current_sub_idx":  0,
        "current_round":    0,
        "pending_query":    sub_queries[0],
        "followup_query":   "",
        "is_sufficient":    False,
        "collected_docs":   [],
        "searched_queries": [],
        "final_answer":     "",
    }


# ─── Node 2：Searcher ─────────────────────────────────────────────────────────

def searcher_node(state: AgentState) -> dict:
    """對 pending_query 執行混合檢索。"""
    pending_query  = state["pending_query"]
    original_query = state["original_query"]
    current_round  = state["current_round"]
    current_sub_idx = state["current_sub_idx"]
    sub_queries    = state["sub_queries"]

    round_label = "第 1 輪" if current_round == 0 else "追問（第 2 輪）"
    print(f"\n[Searcher] 子問題 {current_sub_idx + 1}/{len(sub_queries)}，{round_label}")
    print(f"  搜尋：{pending_query}")

    _emit({"type": "thinking", "step": current_sub_idx * 2 + current_round + 1})

    results, school_id = _search_one_query(pending_query, original_query)
    print(f"  學校過濾：{school_id or '（全域）'}  取得 {len(results)} 筆")

    return {
        "collected_docs":   results,
        "searched_queries": [pending_query],
    }


# ─── Node 3：Planner-Evaluate ─────────────────────────────────────────────────

def _build_evaluate_prompt(original_query: str, sub_query: str, docs: list[dict]) -> str:
    def _primary_type(doc: dict) -> str:
        pts = doc.get("passed_types") or []
        return max(pts, key=lambda x: x.get("score", 0))["type"] if pts else "?"

    context_preview = "".join(
        f"\n[{i+1}] {doc.get('school_id', '?')}/{_primary_type(doc)}: "
        f"{doc.get('chunk_text', '')[:300]}\n"
        for i, doc in enumerate(docs[:10])
    )

    return f"""你是一個資料充足性評估助理。請判斷目前收集到的資料是否足夠回答以下子問題。

【原始使用者問題】
{original_query}

【當前子問題】
{sub_query}

【目前收集到的資料】（共 {len(docs)} 筆）
{context_preview}

【評估規則（請嚴格遵守）】
1. 預設為 sufficient=true。只要有找到相關資料，無論是否完整，都應標記 true。
2. 只有當前子問題對應的學校或面向完全沒有任何文件時，才標記 false。
3. 不要因為「想找更多資訊」就標記 false，資料不完美是正常的。
4. 若標記 false，補充問題需從不同角度切入（例如：換用不同關鍵字、換詢問相關的替代條件）。
5. 補充問題不得重複當前子問題，必須用英文撰寫。
6. 只輸出 JSON，不要有其他文字。

輸出格式（JSON）：
{{
  "is_sufficient": true 或 false,
  "reason": "簡短說明（1句）",
  "followup_query": "English follow-up question from a different angle"
}}

只輸出 JSON，不要有其他文字。"""


def planner_evaluate_node(state: AgentState) -> dict:
    """
    評估當前子問題（第 1 輪）的搜尋結果。
    - 充足      → is_sufficient=True
    - 不充足    → is_sufficient=False，設定 pending_query=followup，current_round=1
    """
    original_query  = state["original_query"]
    current_sub_idx = state["current_sub_idx"]
    sub_query       = state["pending_query"]
    docs            = _deduplicate_docs(state.get("collected_docs", []))

    print(f"\n[Planner-Evaluate] 子問題 {current_sub_idx + 1}，目前 {len(docs)} 筆去重文件")

    if not docs:
        print("[Planner-Evaluate] 無文件，標記不充足並嘗試追問")
        # 以原始子問題為基礎生成追問
        followup = f"alternative search for: {sub_query}"
        return {
            "is_sufficient":  False,
            "followup_query": followup,
            "pending_query":  followup,
            "current_round":  1,
        }

    _emit({"type": "llm_call", "purpose": "planner_evaluate", "sub_idx": current_sub_idx})
    raw = _call_llm(_build_evaluate_prompt(original_query, sub_query, docs))

    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group())
        is_sufficient  = bool(parsed.get("is_sufficient", True))
        reason         = parsed.get("reason", "")
        followup_query = str(parsed.get("followup_query", "")).strip()
    except Exception as e:
        print(f"[Planner-Evaluate] 解析失敗：{e}，預設 sufficient=true")
        return {"is_sufficient": True, "followup_query": ""}

    print(f"[Planner-Evaluate] 充足：{is_sufficient}  理由：{reason}")

    if not is_sufficient and followup_query:
        print(f"[Planner-Evaluate] 追問：{followup_query}")
        return {
            "is_sufficient":  False,
            "followup_query": followup_query,
            "pending_query":  followup_query,
            "current_round":  1,
        }

    return {"is_sufficient": True, "followup_query": ""}


# ─── Node 4：Advance ──────────────────────────────────────────────────────────

def advance_node(state: AgentState) -> dict:
    """推進到下一個子問題，若全部完成則設定 current_sub_idx 超出範圍。"""
    sub_queries     = state["sub_queries"]
    current_sub_idx = state["current_sub_idx"]
    next_idx        = current_sub_idx + 1

    if next_idx < len(sub_queries):
        next_query = sub_queries[next_idx]
        print(f"\n[Advance] 推進到子問題 {next_idx + 1}/{len(sub_queries)}: {next_query}")
        return {
            "current_sub_idx": next_idx,
            "current_round":   0,
            "pending_query":   next_query,
            "is_sufficient":   False,
            "followup_query":  "",
        }
    else:
        print(f"\n[Advance] 所有 {len(sub_queries)} 個子問題已完成，準備生成最終答案")
        return {
            "current_sub_idx": next_idx,   # 超出範圍，用於路由判斷
        }


# ─── Node 5：Finalizer ────────────────────────────────────────────────────────

def finalizer_node(state: AgentState) -> dict:
    query = state["original_query"]
    docs  = _deduplicate_docs(state.get("collected_docs", []))

    print(f"\n[Finalizer] 共 {len(docs)} 筆去重文件，開始生成回答...")

    if not docs:
        answer = "很抱歉，未能從資料庫中找到相關資訊。建議您直接前往各校官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    docs_sorted = sorted(
        docs,
        key=lambda d: d.get("rerank_score", d.get("rrf_score", 0)),
        reverse=True,
    )

    _emit({"type": "llm_call", "purpose": "finalizer"})
    answer = generate_answer(query, docs_sorted)

    if answer:
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}
    else:
        msg = "Gemini 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": None}


# ─── 路由函式 ─────────────────────────────────────────────────────────────────

def after_search(state: AgentState) -> str:
    """Searcher 結束後：第 2 輪 → Advance；第 1 輪 → Planner-Evaluate。"""
    return "advance" if state["current_round"] == 1 else "evaluate"


def after_evaluate(state: AgentState) -> str:
    """Planner-Evaluate 結束後：充足 → Advance；不充足 → Searcher（第 2 輪）。"""
    return "advance" if state["is_sufficient"] else "search"


def after_advance(state: AgentState) -> str:
    """Advance 結束後：還有子問題 → Searcher；全部完成 → Finalizer。"""
    return "finalize" if state["current_sub_idx"] >= len(state["sub_queries"]) else "search"


# ─── 圖建構 ───────────────────────────────────────────────────────────────────

def _build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("planner_decompose", planner_decompose_node)
    builder.add_node("search",            searcher_node)
    builder.add_node("evaluate",          planner_evaluate_node)
    builder.add_node("advance",           advance_node)
    builder.add_node("finalize",          finalizer_node)

    builder.set_entry_point("planner_decompose")
    builder.add_edge("planner_decompose", "search")

    builder.add_conditional_edges("search",   after_search,   {"evaluate": "evaluate", "advance": "advance"})
    builder.add_conditional_edges("evaluate", after_evaluate, {"advance": "advance",   "search":  "search"})
    builder.add_conditional_edges("advance",  after_advance,  {"search":  "search",    "finalize": "finalize"})

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
    global _current_on_event
    _current_on_event = on_event

    try:
        if verbose:
            print(f"\n{'='*60}")
            print(f"   Agentic RAG 啟動")
            print(f"   問題：{query}")
            print(f"{'='*60}")

        initial_state: AgentState = {
            "original_query":   query,
            "sub_queries":      [],
            "current_sub_idx":  0,
            "current_round":    0,
            "pending_query":    "",
            "followup_query":   "",
            "is_sufficient":    False,
            "collected_docs":   [],
            "searched_queries": [],
            "final_answer":     "",
        }

        result_state = _get_graph().invoke(initial_state, {"recursion_limit": max_steps * 10})

        if verbose:
            total_docs = len(_deduplicate_docs(result_state.get("collected_docs", [])))
            n_sub      = len(result_state.get("sub_queries", []))
            print(f"\n{'='*60}")
            print(f"完成！共 {n_sub} 個子問題，彙整 {total_docs} 筆去重文件")
            print(f"{'='*60}")

        return result_state.get("final_answer") or None

    finally:
        _current_on_event = None


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
