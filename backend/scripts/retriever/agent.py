#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py — Agentic RAG（LangGraph 實作）

流程：
  1. Decomposer  — 規則式拆解（無 LLM）：
                   - 偵測到 N 所學校 → 拆成 N 個子問題（每問題對應一所學校）
                   - 偵測不到多所學校 → 使用原始問題（單一問題）
  2. Searcher    — 每個子問題依自己的 school_id 直接向資料庫檢索（略過 page_type）
  3. Planner     — 判斷目前收集的資料是否夠（最多 3 輪）
                   若不夠，產生補充子問題繼續搜尋
  4. Finalizer   — 彙整所有文件，呼叫 Gemini 生成最終答案

特點：
  - Decomposer 完全無 LLM call，快速且可預期
  - Planner 補充問題才使用 LLM，聚焦在真正缺失的資訊
  - 略過 page_type 過濾，讓向量相似度自然篩選最相關內容
"""

from __future__ import annotations

import sys
import json
import re
from pathlib import Path
from typing import Annotated, TypedDict, Callable, Optional
import operator

# ─── path 修正 ────────────────────────────────────────────────────────────────
CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# ─── 外部依賴 ─────────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from retriever.search import search_core
from generator.gemini import get_gemini_client, format_context_for_prompt, _SYSTEM_PROMPT

# ─── 已知學校 ID 對照表（用於自動偵測） ──────────────────────────────────────
_SCHOOL_ALIASES: dict[str, list[str]] = {
    "cmu":       ["cmu", "carnegie mellon", "卡內基梅隆"],
    "mit":       ["mit", "massachusetts institute", "麻省理工"],
    "stanford":  ["stanford", "史丹佛", "斯坦福"],
    "caltech":   ["caltech", "california institute", "加州理工"],
    "harvard":   ["harvard", "哈佛"],
    "columbia":  ["columbia", "哥倫比亞"],
    "yale":      ["yale", "耶魯"],
    "princeton": ["princeton", "普林斯頓"],
    "cornell":   ["cornell", "康乃爾"],
    "gatech":    ["georgia tech", "gatech", "喬治亞理工"],
    "ucsd":      ["ucsd", "uc san diego", "加州聖地牙哥"],
    "ucla":      ["ucla", "uc los angeles", "加州洛杉磯"],
    "berkeley":  ["berkeley", "uc berkeley", "柏克萊"],
    "uiuc":      ["uiuc", "illinois", "伊利諾"],
    "umich":     ["umich", "michigan", "密西根"],
    "nyu":       ["nyu", "new york university", "紐約大學"],
    "purdue":    ["purdue", "普渡"],
    "utaustin":  ["ut austin", "university of texas", "德州大學"],
    "upenn":     ["upenn", "penn", "pennsylvania", "賓州"],
    "duke":      ["duke", "杜克"],
    "uw":        ["uw", "university of washington", "華盛頓大學"],
}

MAX_ROUNDS = 3          # Planner 最多重試幾輪
TOP_K_PER_QUERY = 5    # 每個子問題檢索幾筆

# ─── 模組層級的 on_event callback（執行期間設定）────────────────────────────
_current_on_event: Optional[Callable[[dict], None]] = None


def _emit(event: dict) -> None:
    """安全地呼叫目前的 on_event callback（若有設定）。"""
    if _current_on_event is not None:
        try:
            _current_on_event(event)
        except Exception:
            pass


# State 定義
class AgentState(TypedDict):
    # 輸入
    original_query: str

    # Decomposer 產出
    sub_queries: list[str]          # 初始子問題

    # Planner 補充問題（逐輪累加）
    extra_queries: Annotated[list[str], operator.add]

    # 目前這輪要搜尋的問題列表
    pending_queries: list[str]

    # 所有已收集的文件（去重）
    collected_docs: Annotated[list[dict], operator.add]

    # 已搜尋過的問題（避免重複）
    searched_queries: Annotated[list[str], operator.add]

    # Planner 判斷 & 輪次
    is_sufficient: bool
    round_count: int

    # 最終答案
    final_answer: str


# 工具函式
def _detect_school_ids(text: str) -> list[str]:
    """從文字中偵測提到的學校 ID 列表（可能多所）。"""
    text_lower = text.lower()
    found = []
    for school_id, aliases in _SCHOOL_ALIASES.items():
        if any(alias in text_lower for alias in aliases):
            found.append(school_id)
    return found


def _call_llm(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    """呼叫 Gemini，回傳純文字。"""
    client = get_gemini_client()
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    return (response.text or "").strip()


def _parse_json_list(text: str) -> list[str]:
    """從 LLM 輸出中安全地解析出字串列表。"""
    # 嘗試找 JSON 陣列
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            return [str(x).strip() for x in result if str(x).strip()]
        except json.JSONDecodeError:
            pass
    # fallback：逐行解析（去除數字前綴）
    lines = [re.sub(r"^[\d\.\-\*\s]+", "", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _deduplicate_docs(docs: list[dict]) -> list[dict]:
    """以 chunk_text 前 200 字去重複文件。"""
    seen = set()
    result = []
    for doc in docs:
        key = doc.get("chunk_text", "")[:200]
        if key not in seen:
            seen.add(key)
            result.append(doc)
    return result


# Node 1：Decomposer
# 對應表：school_id → 問題中常見的顯示名稱（取第一個作為規則替換用）
_SCHOOL_DISPLAY_NAME: dict[str, str] = {
    k: aliases[0] for k, aliases in _SCHOOL_ALIASES.items()
}


def _make_school_query(original_query: str, target_school_id: str, all_school_ids: list[str]) -> str:
    """
    從原始問題製作「只提到 target_school」的子問題。

    做法：
    1. 把原始問題中所有其他學校的 alias 刪除（連帶清理連接詞 and/跟/和/與/、）
    2. 若原始問題完全沒有包含 target_school 的文字，在開頭加上學校名稱
    3. 最後做空白清理
    """
    query = original_query
    text_lower = query.lower()

    # 移除「其他學校」的所有 alias
    other_ids = [sid for sid in all_school_ids if sid != target_school_id]
    for sid in other_ids:
        for alias in _SCHOOL_ALIASES[sid]:
            # case-insensitive 替換
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            query = pattern.sub("", query)

    # 清理殘留的連接詞與多餘空白
    query = re.sub(r"[,，、]?\s*(and|跟|和|與|&|or|以及)\s*", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s{2,}", " ", query).strip()
    query = query.strip(",，、 ")

    # 若目標學校的名稱完全不在結果中，在前面補上（保底）
    target_aliases = _SCHOOL_ALIASES[target_school_id]
    if not any(a in query.lower() for a in target_aliases):
        display = _SCHOOL_DISPLAY_NAME[target_school_id].title()
        query = f"{display} {query}"

    return query.strip()


def decomposer_node(state: AgentState) -> dict:
    """
    規則式拆解（無 LLM）：
    - 偵測到 N 所學校 → 拆成 N 個子問題，每個問題對應一所學校
    - 偵測不到多所學校 → 使用原始問題（單一問題，不拆）
    """
    query = state["original_query"]
    print(f"\n[Decomposer] 原始問題：{query}")

    school_ids = _detect_school_ids(query)

    if len(school_ids) >= 2:
        # 多校：每所學校生成一個子問題
        sub_queries = [
            _make_school_query(query, sid, school_ids)
            for sid in school_ids
        ]
        print(f"[Decomposer] 偵測到 {len(school_ids)} 所學校，拆解為 {len(sub_queries)} 個子問題：")
    else:
        # 單校或無學校：直接使用原始問題
        sub_queries = [query]
        label = f"（學校：{school_ids[0]}）" if school_ids else "（無特定學校）"
        print(f"[Decomposer] 單一問題 {label}：")

    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")

    return {
        "sub_queries": sub_queries,
        "pending_queries": sub_queries,
        "extra_queries": [],
        "collected_docs": [],
        "searched_queries": [],
        "is_sufficient": False,
        "round_count": 0,
        "final_answer": "",
    }


# Node 2：Searcher
def searcher_node(state: AgentState) -> dict:
    """
    對 pending_queries 中的每個子問題執行向量 + 關鍵字混合檢索。
    自動偵測 school_id，略過 page_type 過濾。
    每次搜尋都發送 thinking / tool_call / tool_result 事件。
    """
    pending = state.get("pending_queries", [])
    already_searched = state.get("searched_queries", [])
    original_query = state["original_query"]
    round_num = state.get("round_count", 0) + 1

    print(f"\n[Searcher] 第 {round_num} 輪搜尋，共 {len(pending)} 個問題")

    # 發送「思考中」事件
    _emit({"type": "thinking", "step": round_num})

    new_docs: list[dict] = []
    newly_searched: list[str] = []

    for q in pending:
        if q in already_searched:
            print(f"  ⏭ 略過（已搜尋）：{q}")
            continue

        # 偵測此子問題對應的學校（Decomposer 已保證多校時每問題只對應一所）
        # 若子問題偵測不到學校，退回用原始問題偵測
        school_ids = _detect_school_ids(q)
        if not school_ids:
            school_ids = _detect_school_ids(original_query)

        # 取第一個 school_id（子問題應已是單一學校）
        school_id = school_ids[0] if school_ids else None

        print(f"     搜尋：{q}")
        print(f"     學校過濾：{school_id or '（全域）'}")

        # 發送 tool_call 事件
        tool_args: dict = {"query": q}
        if school_id:
            tool_args["school_id"] = school_id

        _emit({
            "type": "tool_call",
            "tool": "search_school" if school_id else "search_general",
            "args": tool_args,
        })

        results = search_core(
            query=q,
            top_k=TOP_K_PER_QUERY,
            use_rerank=True,
            school_id=school_id,
            page_type=None,   # 略過 page_type 過濾
        )
        print(f"     取得 {len(results)} 筆")
        new_docs.extend(results)
        newly_searched.append(q)

        # 發送 tool_result 事件
        _emit({
            "type": "tool_result",
            "tool": "search_school" if school_id else "search_general",
            "preview": f"找到 {len(results)} 筆相關文獻",
        })

    print(f"[Searcher] 本輪新增 {len(new_docs)} 筆文件")

    return {
        "collected_docs": new_docs,
        "searched_queries": newly_searched,
        "round_count": round_num,
        "pending_queries": [],   # 清空，等 Planner 決定下一步
    }


# Node 3：Planner
def planner_node(state: AgentState) -> dict:
    """
    判斷目前收集的資料是否足夠回答原始問題。
    - 若足夠 → is_sufficient = True
    - 若不夠且輪次 < MAX_ROUNDS → 產生 1~3 個補充子問題
    - 若達到最大輪次 → 強制標記 is_sufficient = True
    """
    query = state["original_query"]
    docs = _deduplicate_docs(state.get("collected_docs", []))
    round_count = state.get("round_count", 0)
    searched = state.get("searched_queries", [])

    print(f"\n[Planner] 第 {round_count} 輪判斷，目前收集 {len(docs)} 筆去重文件")

    # 強制結束條件
    if round_count >= MAX_ROUNDS:
        print(f"[Planner] 已達最大輪次（{MAX_ROUNDS}），強制結束")
        return {"is_sufficient": True, "pending_queries": []}

    if not docs:
        print(f"[Planner] 無文件，結束搜尋")
        return {"is_sufficient": True, "pending_queries": []}

    # 摘要已收集的內容（避免 prompt 太長）
    context_preview = ""
    for i, doc in enumerate(docs[:10]):  # 只取前 10 筆給 Planner 看
        school = doc.get("school_id", "?")
        ptype  = doc.get("page_type", "?")
        text   = doc.get("chunk_text", "")[:300]
        context_preview += f"\n[{i+1}] {school}/{ptype}: {text}\n"

    searched_str = "\n".join(f"- {q}" for q in searched)

    prompt = f"""你是一位資料充足性評估專家。請根據以下資訊判斷目前的資料是否足夠回答使用者問題。

【使用者原始問題】
{query}

【已搜尋的子問題】
{searched_str}

【目前收集到的資料摘要】（共 {len(docs)} 筆）
{context_preview}

【評估任務】
請判斷：目前的資料是否已足夠全面地回答使用者的原始問題？

輸出格式（JSON）：
{{
  "is_sufficient": true 或 false,
  "reason": "簡短說明為何足夠/不夠（1-2句）",
  "extra_queries": ["補充問題1", "補充問題2"]  // 若 is_sufficient=false 才填，最多 2 個
}}

規則：
1. 若資料已涵蓋問題的主要面向，即使不完美，也可標記 sufficient=true
2. 若明顯缺少某個關鍵面向（例如問了 GPA 要求但完全沒有 GPA 相關資料），才標記 false
3. 補充問題必須是全新的問法，不得重複已搜尋的問題
4. 只輸出 JSON，不要有其他文字

你的判斷："""

    # 發送 Gemini API 呼叫事件
    _emit({"type": "llm_call", "purpose": "planner", "round": round_count})
    raw = _call_llm(prompt)

    # 解析 JSON 回應
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
        else:
            raise ValueError("無法找到 JSON")
    except Exception as e:
        print(f"[Planner] JSON 解析失敗：{e}，預設為 sufficient=true")
        return {"is_sufficient": True, "pending_queries": []}

    is_sufficient: bool = bool(parsed.get("is_sufficient", True))
    reason: str = parsed.get("reason", "")
    extra_queries: list[str] = [str(q).strip() for q in parsed.get("extra_queries", []) if str(q).strip()]
    extra_queries = extra_queries[:2]

    print(f"[Planner] 是否充足：{is_sufficient}")
    print(f"[Planner] 理由：{reason}")

    if not is_sufficient and extra_queries:
        print(f"[Planner] 補充問題：")
        for q in extra_queries:
            print(f"  → {q}")

    return {
        "is_sufficient": is_sufficient,
        "extra_queries": extra_queries if not is_sufficient else [],
        "pending_queries": extra_queries if not is_sufficient else [],
    }


# Node 4：Finalizer
def finalizer_node(state: AgentState) -> dict:
    """
    彙整所有收集到的文件，呼叫 Gemini 生成最終答案，並發送 answer 事件。
    """
    query = state["original_query"]
    docs = _deduplicate_docs(state.get("collected_docs", []))

    print(f"\n[Finalizer] 共 {len(docs)} 筆去重文件，開始生成回答...")

    if not docs:
        answer = "很抱歉，未能從資料庫中找到相關資訊。建議您直接前往各校官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    # 重新排序：優先展示 rerank_score 高的
    docs_sorted = sorted(
        docs,
        key=lambda d: d.get("rerank_score", d.get("rrf_score", 0)),
        reverse=True,
    )

    context_text = format_context_for_prompt(docs_sorted)

    prompt = f"""{_SYSTEM_PROMPT}

--- 參考資料（共 {len(docs_sorted)} 筆） ---
{context_text}

--- 使用者問題 ---
{query}

--- 你的回答 ---
（請嚴格遵守以上規則，若資料不足請直接說不知道並引導查官網）
"""

    # 發送 Gemini API 呼叫事件
    _emit({"type": "llm_call", "purpose": "finalizer"})
    client = get_gemini_client()
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = (response.text or "").strip()
        text = text.replace("**", "")
        text = re.sub(
            r"<span[^>]*>\s*(\[[^\]]+\]\([^\)]+\))\s*</span>",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)
        _emit({"type": "answer", "text": text})
        return {"final_answer": text}
    except Exception as e:
        print(f"[Finalizer] Gemini 呼叫失敗：{e}")
        _emit({"type": "error", "message": str(e)})
        return {"final_answer": None}


# 路由函式
def should_continue(state: AgentState) -> str:
    """Planner 結束後的路由：繼續搜尋 or 直接 Finalize。"""
    if state.get("is_sufficient", True):
        return "finalize"
    return "search"


# 建構 LangGraph
def _build_graph() -> "CompiledGraph":
    builder = StateGraph(AgentState)

    builder.add_node("decompose", decomposer_node)
    builder.add_node("search",    searcher_node)
    builder.add_node("plan",      planner_node)
    builder.add_node("finalize",  finalizer_node)

    builder.set_entry_point("decompose")
    builder.add_edge("decompose", "search")
    builder.add_edge("search",    "plan")
    builder.add_conditional_edges(
        "plan",
        should_continue,
        {
            "search":   "search",
            "finalize": "finalize",
        },
    )
    builder.add_edge("finalize", END)

    return builder.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# 公開 API
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
        on_event:  SSE 事件 callback，每次 agent 有動作時呼叫
                   事件格式：{"type": "thinking"|"tool_call"|"tool_result"|"answer"|"error", ...}

    Returns:
        最終回答字串，或 None
    """
    global _current_on_event
    _current_on_event = on_event  # 設定模組層級 callback

    try:
        if verbose:
            print(f"\n{'='*60}")
            print(f"   Agentic RAG 啟動")
            print(f"   問題：{query}")
            print(f"   最大搜尋輪次：{MAX_ROUNDS}")
            print(f"{'='*60}")

        graph = _get_graph()

        initial_state: AgentState = {
            "original_query":  query,
            "sub_queries":     [],
            "extra_queries":   [],
            "pending_queries": [],
            "collected_docs":  [],
            "searched_queries": [],
            "is_sufficient":   False,
            "round_count":     0,
            "final_answer":    "",
        }

        result_state = graph.invoke(initial_state, {"recursion_limit": max_steps * 4})

        if verbose:
            total_docs = len(_deduplicate_docs(result_state.get("collected_docs", [])))
            rounds = result_state.get("round_count", 0)
            print(f"\n{'='*60}")
            print(f"完成！共搜尋 {rounds} 輪，彙整 {total_docs} 筆去重文件")
            print(f"{'='*60}")

        return result_state.get("final_answer") or None

    finally:
        _current_on_event = None  # 執行完畢後清除 callback


# CLI 入口
if __name__ == "__main__":
    import argparse
    import io

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="Agentic RAG（LangGraph）")
    parser.add_argument("query", nargs="?", help="使用者問題")
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
