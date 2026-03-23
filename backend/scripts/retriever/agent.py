"""
agent.py — Agentic RAG 核心模組（LangGraph 版本）

流程：
  1. planner 節點：LLM 以 JSON 規劃下一步（搜尋或收斂）
  2. tool 節點：執行向量檢索工具並累積 context
  3. finalizer 節點：根據累積資料生成最終回答

此版本特色：
  - 只使用「問題字串」做搜尋，不再使用 school_id / page_type
  - planner 回傳多個 next_questions
  - tool 每輪只執行本輪 action 的 query
  - next_questions 會作為下一輪候選問題，不會覆蓋本輪 action
  - planner / finalizer 有 context budget 控制，避免 prompt 爆長
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

_ALLOWED_ACTIONS = {"search_general", "final"}

# ---- Prompt / context budget 設定 ----
PLANNER_MAX_ORIGINAL_QUERY_CHARS = 300
PLANNER_MAX_CURRENT_QUERIES_CHARS = 300
PLANNER_MAX_PENDING_QUERIES_CHARS = 300
PLANNER_MAX_QUESTION_HISTORY_CHARS = 500
PLANNER_MAX_HISTORY_CHARS = 700
PLANNER_MAX_SNIPPETS_CHARS = 1200
PLANNER_SNIPPET_PREVIEW_CHARS = 100
PLANNER_MAX_SNIPPETS = 5
PLANNER_HISTORY_STEPS = 3
PLANNER_QUESTION_HISTORY_SIZE = 8
PLANNER_PENDING_QUESTIONS_SIZE = 4

FINALIZER_MAX_DOCS = 6
FINALIZER_MAX_TOTAL_CHARS = 3000
FINALIZER_PER_DOC_CHARS = 500

_PLANNER_PROMPT = """你是 RAG 搜尋規劃器。你每次只能做一件事：
1) 呼叫搜尋動作（search_general）
2) 或結束（final）

請只輸出 JSON（不要輸出 Markdown code fence），格式如下：
{
  "action": "search_general|final",
  "args": {
    "query": "..."
  },
  "next_questions": [
    "...",
    "...",
    "..."
  ],
  "reason": "簡短理由"
}

規則：
- action=search_general 時 args 至少要有 query。
- action=final 時 args 可為空，next_questions 應為 []。
- 當資訊已足夠回答使用者問題時，請輸出 action=final。
- 若 action 不是 final，請根據「目前問題」與「目前已取得資料」生成 next_questions。
- next_questions 必須是陣列，每個元素都是一個可直接拿去搜尋的完整問題字串。
- next_questions 最多 5 個。
- next_questions 中的問題要具體、可直接查詢，且避免與以前問過的問題完全重複。
- 若只需要一個下一步問題，next_questions 就放 1 個字串。
- 不要輸出 next_question 單一字串欄位，只能輸出 next_questions。
- 搜尋完全依賴 query 本身，不要輸出 school_id、page_type 或任何額外識別欄位。
"""


class AgentState(TypedDict):
    query: str
    original_query: str
    current_query: str
    current_queries: list[str]
    pending_questions: list[str]
    question_history: list[str]
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


def _trim_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _compact_json(obj: Any, max_chars: int | None = None) -> str:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if max_chars is not None:
        text = _trim_text(text, max_chars)
    return text


def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) * 0.8))


def _select_context_docs(
    docs: list[dict[str, Any]],
    max_docs: int = FINALIZER_MAX_DOCS,
    max_total_chars: int = FINALIZER_MAX_TOTAL_CHARS,
    per_doc_chars: int = FINALIZER_PER_DOC_CHARS,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total = 0

    for doc in docs:
        chunk = (doc.get("chunk_text") or "").strip()
        if not chunk:
            continue

        trimmed_chunk = _trim_text(chunk, per_doc_chars)
        chunk_size = len(trimmed_chunk)

        if selected and total + chunk_size > max_total_chars:
            break

        copied = dict(doc)
        copied["chunk_text"] = trimmed_chunk
        selected.append(copied)
        total += chunk_size

        if len(selected) >= max_docs:
            break

    return selected


def _extract_json_block(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        content = match.group(0)
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None

    return None


def _parse_next_questions(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            q = str(item).strip()
            if q and q not in result:
                result.append(q)
        return result[:5]

    text = str(value).strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            result = []
            for item in parsed:
                q = str(item).strip()
                if q and q not in result:
                    result.append(q)
            return result[:5]
    except Exception:
        pass

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result: list[str] = []

    for line in lines:
        m = re.match(r"^(?:\d+[.)]\s*|[-*]\s*)(.+)$", line)
        q = m.group(1).strip() if m else line.strip()
        if q and q not in result:
            result.append(q)

    if not result and text:
        result = [text]

    return result[:5]


def _normalize_action(raw: dict[str, Any], user_query: str) -> dict[str, Any]:
    action = str(raw.get("action", "")).strip()
    args = raw.get("args", {})
    next_questions = raw.get("next_questions", [])

    if not isinstance(args, dict):
        args = {}

    parsed_next_questions = _parse_next_questions(next_questions)

    if action not in _ALLOWED_ACTIONS:
        return {
            "action": "search_general",
            "args": {"query": user_query},
            "next_questions": [user_query],
            "reason": "fallback",
        }

    if action == "search_general":
        q = str(args.get("query") or user_query).strip()
        return {
            "action": "search_general",
            "args": {"query": q},
            "next_questions": parsed_next_questions or [q],
            "reason": raw.get("reason", ""),
        }

    return {
        "action": "final",
        "args": {},
        "next_questions": [],
        "reason": raw.get("reason", ""),
    }


def _dedupe_keep_order(items: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        v = str(item).strip()
        if not v or v in seen:
            continue
        seen.add(v)
        result.append(v)
        if limit is not None and len(result) >= limit:
            break

    return result


def _build_planner_input(state: AgentState) -> str:
    recent_history = []
    for h in state["search_history"][-PLANNER_HISTORY_STEPS:]:
        recent_history.append(
            {
                "step": h.get("step"),
                "action": h.get("action"),
                "queries": (h.get("queries") or [])[:2],
                "result_count": h.get("result_count", 0),
            }
        )

    question_history = state["question_history"][-PLANNER_QUESTION_HISTORY_SIZE:]
    current_queries = state.get("current_queries") or [state["current_query"]]
    pending_questions = state.get("pending_questions") or []

    snippets = []
    for idx, doc in enumerate(state["all_results"][:PLANNER_MAX_SNIPPETS], 1):
        snippets.append(
            {
                "i": idx,
                "url": doc.get("source_url"),
                "preview": _trim_text(doc.get("chunk_text") or "", PLANNER_SNIPPET_PREVIEW_CHARS),
            }
        )

    prompt = (
        f"{_PLANNER_PROMPT}\n\n"
        f"使用者原始問題:{_trim_text(state['original_query'], PLANNER_MAX_ORIGINAL_QUERY_CHARS)}\n"
        f"目前這一輪問題:{_compact_json(current_queries, PLANNER_MAX_CURRENT_QUERIES_CHARS)}\n"
        f"待處理候選問題:{_compact_json(pending_questions, PLANNER_MAX_PENDING_QUERIES_CHARS)}\n"
        f"以前問過的問題:{_compact_json(question_history, PLANNER_MAX_QUESTION_HISTORY_CHARS)}\n"
        f"目前步數:{state['step']}/{state['max_steps']}\n"
        f"既有搜尋歷史:{_compact_json(recent_history, PLANNER_MAX_HISTORY_CHARS)}\n"
        f"目前已取得資料摘要:{_compact_json(snippets, PLANNER_MAX_SNIPPETS_CHARS)}\n"
    )

    return prompt


def _planner_step(state: AgentState) -> AgentState:
    if state["step"] >= state["max_steps"]:
        if state["verbose"]:
            print(f"\n[Agent] 已達最大步數 {state['max_steps']}，進入最終回答...")
        return {
            **state,
            "next_action": {
                "action": "final",
                "args": {},
                "next_questions": [],
                "reason": "max_steps_reached",
            },
        }

    step = state["step"] + 1

    if state["verbose"]:
        print(f"\n[Agent] 第 {step} 輪規劃...")

    _emit(
        state["on_event"],
        {
            "type": "thinking",
            "step": step,
            "current_query": state["current_query"],
            "current_queries": state.get("current_queries", [state["current_query"]]),
            "pending_questions": state.get("pending_questions", []),
        },
    )

    if state["verbose"]:
        print("目前問題集:", state.get("current_queries") or [state["current_query"]])

    planner_input = _build_planner_input(state)

    if state["verbose"]:
        print(f"[Planner] prompt chars={len(planner_input)} approx_tokens={_approx_tokens(planner_input)}")

    client = get_gemini_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=planner_input,
    )

    parsed = _extract_json_block(getattr(response, "text", "") or "") or {}
    action = _normalize_action(parsed, state["current_query"])

    question_history = list(state["question_history"])
    current_queries = state.get("current_queries") or [state["current_query"]]

    for q in current_queries:
        q = str(q).strip()
        if q and q not in question_history:
            question_history.append(q)

    pending_questions: list[str] = []

    if action["action"] != "final":
        next_questions = _parse_next_questions(action.get("next_questions"))

        deduped_next_questions: list[str] = []
        for q in next_questions:
            q = str(q).strip()
            if not q:
                continue
            if q in question_history:
                continue
            if q in deduped_next_questions:
                continue
            deduped_next_questions.append(q)

        fallback_q = str(action.get("args", {}).get("query") or "").strip()
        if not deduped_next_questions and fallback_q and fallback_q not in question_history:
            deduped_next_questions = [fallback_q]

        pending_questions = deduped_next_questions[:PLANNER_PENDING_QUESTIONS_SIZE]

    if state["verbose"]:
        print(f"  [Planner] action={action['action']} args={action['args']}")
        if action["action"] != "final":
            print(f"  [Planner] next_questions={pending_questions}")

    return {
        **state,
        "step": step,
        "question_history": question_history,
        "pending_questions": pending_questions,
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

    query = str(args.get("query") or "").strip()
    queries = [query] if query else []

    if not queries:
        fallback_q = str(state.get("current_query") or "").strip()
        if fallback_q:
            queries = [fallback_q]

    queries = _dedupe_keep_order(queries, limit=1)

    if state["verbose"]:
        args_display = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        print(f"  → 呼叫工具：{name}({args_display})")
        print(f"  → 本輪搜尋問題數：{len(queries)}")
        for i, q in enumerate(queries, 1):
            print(f"    [{i}] {q}")

    _emit(
        state["on_event"],
        {
            "type": "tool_call",
            "tool": name,
            "args": args,
            "queries": queries,
            "current_query": state["current_query"],
        },
    )

    top_k = 6
    round_results: list[dict[str, Any]] = []
    sub_search_history: list[dict[str, Any]] = []

    for q in queries:
        results = search_core(q, top_k=top_k, use_rerank=True)
        round_results = _merge_results(round_results, results)
        sub_search_history.append(
            {
                "query": q,
                "result_count": len(results),
            }
        )

    if round_results:
        preview_docs = _select_context_docs(
            round_results,
            max_docs=3,
            max_total_chars=800,
            per_doc_chars=200,
        )
        preview_text = format_context_for_prompt(preview_docs)[:200]
    else:
        preview_text = "[搜尋結果] 未找到相關資料。"

    if state["verbose"]:
        print(f"  ← 本輪合併後取得 {len(round_results)} 筆結果")

    _emit(
        state["on_event"],
        {
            "type": "tool_result",
            "tool": name,
            "preview": preview_text,
            "queries": queries,
            "current_query": state["current_query"],
        },
    )

    merged_results = _merge_results(state["all_results"], round_results)

    new_history_item = {
        "step": state["step"],
        "queries": queries,
        "action": name,
        "args": args,
        "result_count": len(round_results),
        "sub_searches": sub_search_history,
    }

    next_queries = state.get("pending_questions") or []
    next_queries = _dedupe_keep_order(next_queries, limit=PLANNER_PENDING_QUESTIONS_SIZE)

    if next_queries:
        new_current_queries = next_queries
        new_current_query = " / ".join(next_queries)
    else:
        new_current_queries = queries
        new_current_query = queries[0] if queries else state["current_query"]

    return {
        **state,
        "search_history": state["search_history"] + [new_history_item],
        "all_results": merged_results,
        "current_queries": new_current_queries,
        "current_query": new_current_query,
        "pending_questions": [],
        "next_action": None,
    }


def _finalizer_step(state: AgentState) -> AgentState:
    if state["verbose"]:
        print("\n[Agent] 生成最終回答...")

    context_docs = _select_context_docs(
        state["all_results"],
        max_docs=FINALIZER_MAX_DOCS,
        max_total_chars=FINALIZER_MAX_TOTAL_CHARS,
        per_doc_chars=FINALIZER_PER_DOC_CHARS,
    )

    if state["verbose"]:
        context_chars = sum(len((d.get("chunk_text") or "")) for d in context_docs)
        print(f"[Finalizer] context_docs={len(context_docs)} total_chars={context_chars}")

    if not context_docs:
        final_answer = "根據目前取得的資料，我無法確認此問題的答案。建議您直接前往官方網站查詢。"
    else:
        final_answer = generate_answer(
            query=state["original_query"],
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
        max_steps: 最大工具迭代次數
        verbose:   是否印出每步驟過程
        on_event:  回調函數，接收 {"type": "thinking"|"tool_call"|"tool_result"|"answer"|"error", ...}

    Returns:
        最終回答字串，或 None（失敗時）
    """
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"[Agent] 開始處理問題：{query}")
        print(f"{'=' * 60}")

    graph = _build_agent_graph()

    try:
        result = graph.invoke(
            {
                "query": query,
                "original_query": query,
                "current_query": query,
                "current_queries": [query],
                "pending_questions": [],
                "question_history": [],
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