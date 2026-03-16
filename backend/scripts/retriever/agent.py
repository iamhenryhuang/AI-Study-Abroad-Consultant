"""
agent.py — Agentic RAG 核心模組

使用 Gemini Function Calling（ReAct 模式）讓 LLM 自行決定：
  1. 要搜尋什麼、要搜哪個學校
  2. 結果是否足夠，或需要再次搜尋
  3. 何時停止並生成最終回答

工具清單：
  - search_general(query)
      全庫向量搜尋，適合跨學校問題
  - search_school(query, school_id)
      限定特定學校搜尋，精準度更高
  - search_page_type(query, school_id, page_type)
      限定頁面類型搜尋（e.g. 只搜 faq, checklist）

Agent 迭代上限：max_steps（預設 5）
每輪搜尋結果會累積進 context，供最終生成使用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from typing_extensions import TypedDict
from langgraph.graph import END, START, StateGraph

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from google import genai
from google.genai import types

from generator.gemini import get_gemini_client, format_context_for_prompt
from retriever.search import search_core

# ── 工具定義（Gemini Function Calling 格式）────────────────────────

_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_general",
            description=(
                "在全部學校的資料庫中進行向量語意搜尋。"
                "適合用在跨學校比較問題，或不確定哪個學校的問題。"
                "每次呼叫會回傳最相關的段落文字和來源。"
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="搜尋的具體語句，越精確越好（英文效果更佳）",
                    ),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_school",
            description=(
                "在指定學校的資料中進行向量搜尋。"
                "當問題明確指向某所學校時優先使用此工具，精確度更高。"
                "school_id 可以是: cmu, caltech, stanford, berkeley, mit, "
                "uiuc, gatech, cornell, ucla, ucsd, uw, nccu"
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="搜尋的具體語句",
                    ),
                    "school_id": types.Schema(
                        type=types.Type.STRING,
                        description="學校識別碼，例如 'cmu', 'stanford', 'mit'",
                    ),
                },
                required=["query", "school_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_page_type",
            description=(
                "在指定學校的特定類型頁面中搜尋。"
                "page_type 可以是: faq, admissions, checklist, requirements, general, professor_profile, professor_paper。"
                "例如：要找申請截止日期 → page_type='admissions'；"
                "要找常見問題的詳細回答 → page_type='faq'；"
                "要找申請文件清單 → page_type='checklist'；"
                "要找教授研究領域與專長 → page_type='professor_profile'；"
                "要找教授近年發表的論文題目 → page_type='professor_paper'。"
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="搜尋的具體語句。建議使用英文關鍵字以獲得更佳的美國大學官網檢索效果。",
                    ),
                    "school_id": types.Schema(
                        type=types.Type.STRING,
                        description="學校識別碼",
                    ),
                    "page_type": types.Schema(
                        type=types.Type.STRING,
                        description="頁面類型: faq | admissions | checklist | requirements | general | professor_profile | professor_paper",
                    ),
                },
                required=["query", "school_id", "page_type"],
            ),
        ),
    ]
)

# ── Agent System Prompt ─────────────────────────────────────────────

_AGENT_SYSTEM_PROMPT = """你是一位北美 CS 研究所申請諮詢 AI Agent。你擁有三個搜尋工具，可以從向量資料庫中查詢各大學的招生資訊。

你的任務流程：
1. 分析使用者的問題，判斷需要哪些具體資訊。
2. 有策略地呼叫搜尋工具取得資料。
3. 評估搜到的資料是否足夠回答問題；若不夠，繼續搜尋。
4. 當資料充足時，生成清晰、準確的最終回答。

搜尋策略指南：
- 複合問題（比較多校、詢問多個面向）→ 拆成多次單一目標搜尋
- 問題明確指定學校 → 優先使用 search_school
- 問題涉及 FAQ 或政策細節 → 用 search_page_type 加上 page_type='faq'
- 問題涉及申請文件清單 → page_type='checklist'
- 找教授的個人資訊、研究專長或聯絡資訊 → page_type='professor_profile'
- 找教授最近發表的具體論文標題或年份 → page_type='professor_paper'
- 全域比較問題 → 先用 search_general，再針對性補充

回答規則（生成最終答案時必須遵守）：
- 語言規範：不論使用者用中文或英文提問，你都必須使用「繁體中文」回答。
- 翻譯與處理：你可以讀懂參考資料中的英文文件，並將其準確翻譯為繁體中文後納入回答。
- 嚴格依賴搜尋到的資料，不得自行腦補任何數字、日期或政策。
- 若資料不足，明確說「找不到相關資訊」並建議使用者查官網。
- 善用 Markdown：請使用 **粗體** 標註重點（如學校名稱、數據、截止日期），並使用無序列表 (`-`) 或有序列表 (`1.`) 讓資訊層次分明。避免寫成冗長的純文字大段落。
- 引用來源規範：
  - 引用數據時必須使用 Markdown 連結標註來源，例如：[資料來源](URL)。避免產生包含括號的純文字網址而導致前端解析錯誤。
  - 若多項資訊來自同一來源或教授，請合併標註於段落末尾一次即可，保持頁面整潔。
- 直接切入重點，無需開場白。

對比問題格式規定（這是最重要的輸出格式要求）：
當問題涉及多所學校時，必須按「維度」組織回答，不能按學校分段。且必須使用 Markdown 標題（如 `### [GPA 要求]`）。

正確格式：
### [GPA 要求]
- **Stanford**: ...
- **CMU**: ...
- **MIT**: ...

### [截止日期]
- **Stanford**: ...
- **CMU**: ...
- **MIT**: ...

錯誤格式（不得使用）：
Stanford: GPA 要求...、截止日期...、其他...
CMU: GPA 要求...、截止日期...、其他...

教授研究與個人資訊排版規範：
- 必須以教授姓名為首：每個教授的資訊必須以 `### [教授姓名]` 作為標題開頭。
- 善用粗體與列表：具體分點說明其 **研究領域**、**重點實驗室**、以及 **代表性成果**。
- 總體分析：若檢索集包含摘要，請分析並總結其核心研究方向與近年突破。
- 結構分明：不同教授之間請保持空行，確保排版有條理。

一般問題回答排版規範：
- 層次分明：大量使用 `-` 條列式說明，避免擠在一起的冗長段落。
- 重點突出：重要結論或數據應放置於段落開頭並使用 **粗體**。
- 邏輯清晰：按「核心問題回答」→「詳細資訊解析」→「補充建議與來源」的結構組織。
"""

# ── SSE 事件輔助函式 ──────────────────────────────────────────────

def _emit(on_event, event: dict) -> None:
    """安全地觸發 SSE 事件回調（若 on_event 為 None 則略過）。"""
    if on_event:
        try:
            on_event(event)
        except Exception:
            pass


# ── 工具執行器 ─────────────────────────────────────────────────────

def _execute_tool(name: str, args: dict) -> str:
    """
    執行 Agent 呼叫的工具，回傳格式化的搜尋結果字串。
    """
    top_k = 4  # 每次搜尋取 4 筆，避免 context 膨脹

    if name == "search_general":
        results = search_core(args["query"], top_k=top_k, use_rerank=True)

    elif name == "search_school":
        results = search_core(
            args["query"],
            top_k=top_k,
            use_rerank=True,
            school_id=args.get("school_id"),
        )

    elif name == "search_page_type":
        results = search_core(
            args["query"],
            top_k=top_k,
            use_rerank=True,
            school_id=args.get("school_id"),
            page_type=args.get("page_type"),
        )

    else:
        return f"[錯誤] 未知工具：{name}"

    if not results:
        return "[搜尋結果] 未找到相關資料。"

    return format_context_for_prompt(results)


class AgentState(TypedDict):
    """LangGraph state for the Agentic RAG workflow."""

    query: str
    max_steps: int
    step: int
    verbose: bool
    on_event: Any
    contents: list[types.Content]
    pending_function_calls: list[Any]
    final_answer: str | None


def _agent_model_step(state: AgentState) -> AgentState:
    """Call Gemini with tools and decide whether to continue searching."""
    step = state["step"] + 1

    if state["verbose"]:
        print(f"\n[Agent] 第 {step} 輪推理...")
    _emit(state["on_event"], {"type": "thinking", "step": step})

    client = get_gemini_client()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=state["contents"],
        config=types.GenerateContentConfig(
            system_instruction=_AGENT_SYSTEM_PROMPT,
            tools=[_TOOLS],
        ),
    )

    candidate = response.candidates[0]
    function_calls = []
    text_parts = []

    for part in candidate.content.parts:
        if part.function_call:
            function_calls.append(part.function_call)
        elif part.text:
            text_parts.append(part.text)

    contents = state["contents"] + [candidate.content]

    # No tool calls -> final answer generated.
    if not function_calls:
        final_answer = "".join(text_parts).strip()
        if state["verbose"]:
            print(f"\n[Agent] 生成最終回答（共 {step} 輪搜尋）")
            print(f"{'='*60}\n")
        _emit(state["on_event"], {"type": "answer", "text": final_answer})
        return {
            **state,
            "step": step,
            "contents": contents,
            "pending_function_calls": [],
            "final_answer": final_answer,
        }

    return {
        **state,
        "step": step,
        "contents": contents,
        "pending_function_calls": function_calls,
    }


def _agent_tool_step(state: AgentState) -> AgentState:
    """Execute requested tools and append tool responses to conversation history."""
    tool_response_parts = []

    for fc in state["pending_function_calls"]:
        tool_name = fc.name
        tool_args = dict(fc.args) if fc.args else {}

        if state["verbose"]:
            args_display = json.dumps(tool_args, ensure_ascii=False)
            print(f"  → 呼叫工具：{tool_name}({args_display})")
        _emit(state["on_event"], {"type": "tool_call", "tool": tool_name, "args": tool_args})

        tool_result = _execute_tool(tool_name, tool_args)

        if state["verbose"]:
            preview = tool_result[:200].replace("\n", " ")
            print(f"  ← 結果預覽：{preview}...")
        _emit(
            state["on_event"],
            {"type": "tool_result", "tool": tool_name, "preview": tool_result[:200]},
        )

        tool_response_parts.append(
            types.Part(
                function_response=types.FunctionResponse(
                    name=tool_name,
                    response={"result": tool_result},
                )
            )
        )

    contents = state["contents"] + [types.Content(role="tool", parts=tool_response_parts)]
    return {
        **state,
        "contents": contents,
        "pending_function_calls": [],
    }


def _force_final_answer_step(state: AgentState) -> AgentState:
    """Force a text-only final answer when max steps are reached."""
    if state["verbose"]:
        print(f"\n[Agent] 達到最大迭代次數 ({state['max_steps']})，強制生成回答...")

    contents = state["contents"] + [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text="請根據以上你搜尋到的所有資料，現在生成最終回答。不要再呼叫任何工具。"
                )
            ],
        )
    ]

    client = get_gemini_client()
    final_response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=_AGENT_SYSTEM_PROMPT,
        ),
    )

    final_answer = final_response.text.strip()
    _emit(state["on_event"], {"type": "answer", "text": final_answer})

    if state["verbose"]:
        print(f"[Agent] 完成（共 {state['max_steps']} 輪，強制停止）")
        print(f"{'='*60}\n")

    return {
        **state,
        "contents": contents,
        "final_answer": final_answer,
    }


def _route_after_model(state: AgentState) -> str:
    """Route to END, tools, or forced final generation."""
    if state.get("final_answer"):
        return END
    if state["step"] >= state["max_steps"]:
        return "force_final"
    return "tool"


def _build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("model", _agent_model_step)
    graph.add_node("tool", _agent_tool_step)
    graph.add_node("force_final", _force_final_answer_step)

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route_after_model, {END: END, "tool": "tool", "force_final": "force_final"})
    graph.add_edge("tool", "model")
    graph.add_edge("force_final", END)

    return graph.compile()


# ── 主 Agent Loop ──────────────────────────────────────────────────

def run_agent(
    query: str,
    max_steps: int = 5,
    verbose: bool = True,
    on_event=None,
) -> str | None:
    """
    執行 Agentic RAG 流程（ReAct Loop）。

    Args:
        query:     使用者問題
        max_steps: 最大搜尋迭代次數（超過後強制生成回答）
        verbose:   是否印出每步驟的推理過程
        on_event:  回調函數，接收 {"type": "thinking"|"tool_call"|"tool_result"|"answer"|"error", ...}

    Returns:
        最終回答字串，或 None（失敗時）
    """
    # 初始化對話歷史
    contents: list[types.Content] = [types.Content(role="user", parts=[types.Part(text=query)])]

    if verbose:
        print(f"\n{'='*60}")
        print(f"[Agent] 開始處理問題：{query}")
        print(f"{'='*60}")

    graph = _build_agent_graph()
    result = graph.invoke(
        {
            "query": query,
            "max_steps": max_steps,
            "step": 0,
            "verbose": verbose,
            "on_event": on_event,
            "contents": contents,
            "pending_function_calls": [],
            "final_answer": None,
        }
    )

    return result.get("final_answer")
