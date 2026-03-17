"""
agent.py — Agentic RAG 核心模組（LangGraph 版本）

流程：
  1. planner 節點：LLM 以 JSON 規劃下一步（搜尋或收斂）
  2. tool 節點：執行向量檢索工具並累積 context
  3. finalizer 節點：根據累積資料生成最終回答

此版本不使用 Gemini function calling。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generator.gemini import format_context_for_prompt, generate_answer, get_gemini_client
from retriever.search import search_core

_ALLOWED_ACTIONS = {"search_general", "search_school", "search_page_type", "final"}
_ALLOWED_PAGE_TYPES = {
    "faq",
    "admissions",
    "checklist",
    "requirements",
    "general",
    "professor_profile",
    "professor_paper",
}

_PLANNER_PROMPT = """你是 RAG 搜尋規劃器。你每次只能做一件事：
1) 呼叫一個搜尋動作（search_general / search_school / search_page_type）
2) 或結束（final）

請只輸出 JSON（不要輸出 Markdown code fence），格式如下：
{
  "action": "search_general|search_school|search_page_type|final",
  "args": {
    "query": "...",
    "school_id": "...",
    "page_type": "..."
  },
  "reason": "簡短理由"
}

規則：
- action=search_general 時 args 至少要有 query。
- action=search_school 時 args 必須有 query, school_id。
- action=search_page_type 時 args 必須有 query, school_id, page_type。
- action=final 時 args 可為空。
- page_type 只能是: faq, admissions, checklist, requirements, general, professor_profile, professor_paper。
- 當資訊已足夠回答使用者問題時，請輸出 action=final。
"""


class AgentState(TypedDict):
    query: str
    max_steps: int
    step: int
    verbose: bool
    on_event: Any
    search_history: list[dict[str, Any]]
    all_results: list[dict[str, Any]]
    next_action: dict[str, Any] | None
    final_answer: str | None


def _emit(on_event, event: dict) -> None:
    if on_event:
        try:
            on_event(event)
        except Exception:
            pass


def _extract_json_block(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None

    # 先嘗試整段 JSON 解析
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # 退而求其次：抓第一個 {...}
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None

    return None


def _normalize_action(raw: dict[str, Any], user_query: str) -> dict[str, Any]:
    action = str(raw.get("action", "")).strip()
    args = raw.get("args", {})
    if not isinstance(args, dict):
        args = {}

    if action not in _ALLOWED_ACTIONS:
        return {"action": "search_general", "args": {"query": user_query}, "reason": "fallback"}

    if action == "search_general":
        q = str(args.get("query") or user_query).strip()
        return {"action": action, "args": {"query": q}, "reason": raw.get("reason", "")}

    if action == "search_school":
        q = str(args.get("query") or user_query).strip()
        sid = str(args.get("school_id") or "").strip().lower()
        if not sid:
            return {"action": "search_general", "args": {"query": q}, "reason": "missing school_id"}
        return {"action": action, "args": {"query": q, "school_id": sid}, "reason": raw.get("reason", "")}

    if action == "search_page_type":
        q = str(args.get("query") or user_query).strip()
        sid = str(args.get("school_id") or "").strip().lower()
        ptype = str(args.get("page_type") or "").strip().lower()
        if ptype not in _ALLOWED_PAGE_TYPES:
            ptype = "general"
        if not sid:
            return {"action": "search_general", "args": {"query": q}, "reason": "missing school_id"}
        return {
            "action": action,
            "args": {"query": q, "school_id": sid, "page_type": ptype},
            "reason": raw.get("reason", ""),
        }

    return {"action": "final", "args": {}, "reason": raw.get("reason", "")}


def _build_planner_input(state: AgentState) -> str:
    recent_history = state["search_history"][-6:]
    history_text = json.dumps(recent_history, ensure_ascii=False, indent=2) if recent_history else "[]"

    # 僅帶前幾筆摘要，避免 prompt 膨脹
    snippets = []
    for idx, doc in enumerate(state["all_results"][:8], 1):
        snippets.append(
            {
                "i": idx,
                "school": doc.get("school_id"),
                "page_type": doc.get("page_type"),
                "url": doc.get("source_url"),
                "preview": (doc.get("chunk_text") or "")[:180],
            }
        )

    snippets_text = json.dumps(snippets, ensure_ascii=False, indent=2) if snippets else "[]"

    return (
        f"{_PLANNER_PROMPT}\n\n"
        f"使用者問題:\n{state['query']}\n\n"
        f"目前步數: {state['step']} / {state['max_steps']}\n"
        f"既有搜尋歷史:\n{history_text}\n\n"
        f"目前已取得資料摘要:\n{snippets_text}\n"
    )


def _planner_step(state: AgentState) -> AgentState:
    step = state["step"] + 1

    if state["verbose"]:
        print(f"\n[Agent] 第 {step} 輪規劃...")
    _emit(state["on_event"], {"type": "thinking", "step": step})

    # 達上限就直接進 finalizer
    if step >= state["max_steps"]:
        return {
            **state,
            "step": step,
            "next_action": {"action": "final", "args": {}, "reason": "max_steps_reached"},
        }

    client = get_gemini_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=_build_planner_input(state),
    )

    parsed = _extract_json_block(response.text or "") or {}
    action = _normalize_action(parsed, state["query"])

    if state["verbose"]:
        print(f"  [Planner] action={action['action']} args={action['args']}")

    return {
        **state,
        "step": step,
        "next_action": action,
    }


def _merge_results(existing: list[dict[str, Any]], new_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = {
        (str(d.get("source_url", "")), str(d.get("chunk_text", ""))) for d in existing
    }

    merged = list(existing)
    for doc in new_results:
        key = (str(doc.get("source_url", "")), str(doc.get("chunk_text", "")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(doc)

    return merged


def _tool_step(state: AgentState) -> AgentState:
    action = state.get("next_action") or {"action": "final", "args": {}}
    name = action.get("action")
    args = action.get("args", {})

    if name == "final":
        return state

    if state["verbose"]:
        args_display = json.dumps(args, ensure_ascii=False)
        print(f"  → 呼叫工具：{name}({args_display})")
    _emit(state["on_event"], {"type": "tool_call", "tool": name, "args": args})

    top_k = 4
    if name == "search_general":
        results = search_core(args.get("query", state["query"]), top_k=top_k, use_rerank=True)
    elif name == "search_school":
        results = search_core(
            args.get("query", state["query"]),
            top_k=top_k,
            use_rerank=True,
            school_id=args.get("school_id"),
        )
    elif name == "search_page_type":
        results = search_core(
            args.get("query", state["query"]),
            top_k=top_k,
            use_rerank=True,
            school_id=args.get("school_id"),
            page_type=args.get("page_type"),
        )
    else:
        results = []

    if results:
        preview_text = format_context_for_prompt(results)[:200]
    else:
        preview_text = "[搜尋結果] 未找到相關資料。"

    if state["verbose"]:
        print(f"  ← 取得 {len(results)} 筆結果")
    _emit(state["on_event"], {"type": "tool_result", "tool": name, "preview": preview_text})

    merged_results = _merge_results(state["all_results"], results)
    new_history_item = {
        "step": state["step"],
        "action": name,
        "args": args,
        "result_count": len(results),
    }

    return {
        **state,
        "search_history": state["search_history"] + [new_history_item],
        "all_results": merged_results,
        "next_action": None,
    }


def _finalizer_step(state: AgentState) -> AgentState:
    if state["verbose"]:
        print("\n[Agent] 生成最終回答...")

    # 控制上下文量，避免模型上下文過大
    context_docs = state["all_results"][:12]

    if not context_docs:
        final_answer = "根據目前取得的資料，我無法確認此問題的答案。建議您直接前往官方網站查詢。"
    else:
        final_answer = generate_answer(
            query=state["query"],
            context_docs=context_docs,
            model_name="gemini-2.5-flash",
        ) or "根據目前取得的資料，我無法確認此問題的答案。建議您直接前往官方網站查詢。"

    _emit(state["on_event"], {"type": "answer", "text": final_answer})

    if state["verbose"]:
        print(f"[Agent] 完成（共 {state['step']} 輪）")

    return {
        **state,
        "final_answer": final_answer,
    }


def _route_after_planner(state: AgentState) -> str:
    action = (state.get("next_action") or {}).get("action", "final")
    if action == "final":
        return "finalizer"
    return "tool"


def _build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("planner", _planner_step)
    graph.add_node("tool", _tool_step)
    graph.add_node("finalizer", _finalizer_step)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", _route_after_planner, {"tool": "tool", "finalizer": "finalizer"})
    graph.add_edge("tool", "planner")
    graph.add_edge("finalizer", END)

    return graph.compile()


def run_agent(
    query: str,
    max_steps: int = 5,
    verbose: bool = True,
    on_event=None,
) -> str | None:
    """
    執行 Agentic RAG 流程（LangGraph Planner-Tool Loop）。

    Args:
        query:     使用者問題
        max_steps: 最大規劃迭代次數（超過後會收斂到最終回答）
        verbose:   是否印出每步驟過程
        on_event:  回調函數，接收 {"type": "thinking"|"tool_call"|"tool_result"|"answer"|"error", ...}

    Returns:
        最終回答字串，或 None（失敗時）
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"[Agent] 開始處理問題：{query}")
        print(f"{'='*60}")

    graph = _build_agent_graph()

    try:
        result = graph.invoke(
            {
                "query": query,
                "max_steps": max_steps,
                "step": 0,
                "verbose": verbose,
                "on_event": on_event,
                "search_history": [],
                "all_results": [],
                "next_action": None,
                "final_answer": None,
            }
        )
        return result.get("final_answer")
    except Exception as exc:
        _emit(on_event, {"type": "error", "message": str(exc)})
        if verbose:
            print(f"[Agent] 發生錯誤: {exc}")
        return None
