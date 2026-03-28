"""
流程：
  1. Decomposer  — 規則式拆解（無 LLM）：
                   - 偵測到 N 所學校 → 拆成 N 個子問題（每問題對應一所學校）
                   - 偵測不到多所學校 → 使用原始問題
  2. Searcher    — 每個子問題依自己的 school_id 向資料庫檢索
  3. Planner     — 判斷目前收集的資料是否夠（最多 2 輪）
                   若不夠，產生英文補充子問題繼續搜尋
  4. Evaluator   — 判斷是否需要備案推薦：
                   ① 使用者主動詢問備案
                   ② LLM 判斷目標校難度偏高（對照 admission_stats）
  5. AltSearcher — 從 admission_stats + admission_experience 組裝備案文件
  6. Finalizer   — 彙整所有文件（含備案），呼叫 Gemini 生成最終答案
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
from retriever.search import search_core, search_alternative
from retriever.analyzer import analyze_user_intent, evaluate_attainability
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
 
MAX_ROUNDS         = 2   # Planner 最多重試幾輪
TOP_K_PER_QUERY    = 5   # 每個子問題檢索幾筆
MAX_ALT_SCHOOLS    = 4   # 最多推薦幾所備案學校
MAX_EXP_PER_SCHOOL = 5   # 每所備案學校最多載入幾筆經驗
 
# 模組層級的 on_event callback（執行期間設定）
_current_on_event: Optional[Callable[[dict], None]] = None
 
 
# ─── State ───────────────────────────────────────────────────────────────────
 
class AgentState(TypedDict):
    # 原有欄位
    original_query:   str
    sub_queries:      list[str]
    extra_queries:    Annotated[list[str], operator.add]
    pending_queries:  list[str]
    collected_docs:   Annotated[list[dict], operator.add]
    searched_queries: Annotated[list[str], operator.add]
    is_sufficient:    bool
    round_count:      int
    final_answer:     str
    # 備案相關新增欄位
    active_profile:   dict          # 從 query 提取的 GPA/TOEFL/GRE
    trigger_alt:      bool          # 是否觸發備案推薦
    alt_schools:      list[str]     # 備案學校 ID 列表
    distribution:     list[dict]    # 備案學校的錄取統計
 
 
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
 

# ─── Node 1：Decomposer ───────────────────────────────────────────────────────
 
_SCHOOL_DISPLAY_NAME: dict[str, str] = {
    k: aliases[0] for k, aliases in _SCHOOL_ALIASES.items()
}
  
def _make_school_query(original_query: str, target_school_id: str, all_school_ids: list[str]) -> str:
    query = original_query
    for sid in all_school_ids:
        if sid == target_school_id:
            continue
        for alias in _SCHOOL_ALIASES[sid]:
            query = re.compile(re.escape(alias), re.IGNORECASE).sub("", query)
 
    query = re.sub(r"\b(and|or)\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"[,，、]|(跟|和|與|&|以及)", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s{2,}", " ", query).strip().strip(",，、 ")
 
    target_aliases = _SCHOOL_ALIASES[target_school_id]
    if not any(a in query.lower() for a in target_aliases):
        display = _SCHOOL_DISPLAY_NAME[target_school_id].title()
        query = f"{display} {query}"
 
    return query.strip()
 
 
def decomposer_node(state: AgentState) -> dict:
    query = state["original_query"]
    print(f"\n[Decomposer] 原始問題：{query}")
 
    school_ids = _detect_school_ids(query)
 
    if len(school_ids) >= 2:
        sub_queries = [_make_school_query(query, sid, school_ids) for sid in school_ids]
        print(f"[Decomposer] 偵測到 {len(school_ids)} 所學校，拆解為 {len(sub_queries)} 個子問題：")
    else:
        sub_queries = [query]
        label = f"（學校：{school_ids[0]}）" if school_ids else "（無特定學校）"
        print(f"[Decomposer] 單一問題 {label}：")
 
    for i, q in enumerate(sub_queries, 1):
        print(f"  Q{i}: {q}")
 
    return {
        "sub_queries":      sub_queries,
        "pending_queries":  sub_queries,
        "extra_queries":    [],
        "collected_docs":   [],
        "searched_queries": [],
        "is_sufficient":    False,
        "round_count":      0,
        "final_answer":     "",
    }
 

# ─── Node 2：Searcher ─────────────────────────────────────────────────────────
 
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
 
 
def searcher_node(state: AgentState) -> dict:
    pending          = state.get("pending_queries", [])
    already_searched = state.get("searched_queries", [])
    original_query   = state["original_query"]
    round_num        = state.get("round_count", 0) + 1
 
    print(f"\n[Searcher] 第 {round_num} 輪搜尋，共 {len(pending)} 個問題")
    _emit({"type": "thinking", "step": round_num})
 
    new_docs: list[dict] = []
    newly_searched: list[str] = []
 
    for q in pending:
        if q in already_searched:
            print(f"  ⏭ 略過（已搜尋）：{q}")
            continue
 
        results, school_id = _search_one_query(q, original_query)
        print(f"     搜尋：{q}  學校過濾：{school_id or '（全域）'}  取得 {len(results)} 筆")
 
        new_docs.extend(results)
        newly_searched.append(q)
 
    print(f"[Searcher] 本輪新增 {len(new_docs)} 筆文件")
 
    return {
        "collected_docs":   new_docs,
        "searched_queries": newly_searched,
        "round_count":      round_num,
        "pending_queries":  [],
    }
 
 
# ─── Node 3：Planner ──────────────────────────────────────────────────────────
 
def _build_planner_prompt(query: str, docs: list[dict], searched: list[str]) -> str:
    def _primary_type(doc: dict) -> str:
        pts = doc.get("passed_types") or []
        return max(pts, key=lambda x: x.get("score", 0))["type"] if pts else "?"
 
    context_preview = "".join(
        f"\n[{i+1}] {doc.get('school_id', '?')}/{_primary_type(doc)}: "
        f"{doc.get('chunk_text', '')[:300]}\n"
        for i, doc in enumerate(docs[:10])
    )
    searched_str = "\n".join(f"- {q}" for q in searched)
 
    return f"""你是一位資料充足性評估專家。請根據以下資訊判斷目前的資料是否足夠回答使用者問題。
 
【使用者原始問題】
{query}
 
【已搜尋的子問題】
{searched_str}
 
【目前收集到的資料摘要】（共 {len(docs)} 筆）
{context_preview}
 
【評估任務】
請判斷：目前的資料是否已足夠回答使用者的原始問題？
 
輸出格式（JSON）：
{{
  "is_sufficient": true 或 false,
  "reason": "簡短說明為何足夠/不夠（1-2句）",
  "extra_queries": ["English follow-up query 1", ..., "English follow-up query 5"]
}}
 
規則：
1. 預設為 sufficient=true。只要有找到相關資料，無論是否完整，都應標記 true。
2. 只有在某所學校或某個面向完全沒有任何文件時，才標記 false 並補問。
3. 補充問題只針對完全空白的學校或面向，不得重複已搜尋的問題。
4. 補充問題（extra_queries）必須用英文撰寫。
5. 只輸出 JSON，不要有其他文字。
 
你的判斷："""
 
 
def _parse_planner_response(raw: str) -> tuple[bool, str, list[str]]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("無法找到 JSON")
    parsed = json.loads(match.group())
    is_sufficient = bool(parsed.get("is_sufficient", True))
    reason        = parsed.get("reason", "")
    extra_queries = [str(q).strip() for q in parsed.get("extra_queries", []) if str(q).strip()]
    return is_sufficient, reason, extra_queries[:5]
 
 
def planner_node(state: AgentState) -> dict:
    query       = state["original_query"]
    docs        = _deduplicate_docs(state.get("collected_docs", []))
    round_count = state.get("round_count", 0)
    searched    = state.get("searched_queries", [])
 
    print(f"\n[Planner] 第 {round_count} 輪判斷，目前收集 {len(docs)} 筆去重文件")
 
    if round_count >= MAX_ROUNDS:
        print(f"[Planner] 已達最大輪次（{MAX_ROUNDS}），強制結束")
        return {"is_sufficient": True, "pending_queries": []}
 
    if not docs:
        print("[Planner] 無文件，結束搜尋")
        return {"is_sufficient": True, "pending_queries": []}
 
    _emit({"type": "llm_call", "purpose": "planner", "round": round_count})
    raw = _call_llm(_build_planner_prompt(query, docs, searched))
 
    try:
        is_sufficient, reason, extra_queries = _parse_planner_response(raw)
    except Exception as e:
        print(f"[Planner] JSON 解析失敗：{e}，預設 sufficient=true")
        return {"is_sufficient": True, "pending_queries": []}
 
    print(f"[Planner] 是否充足：{is_sufficient}  理由：{reason}")
    if not is_sufficient and extra_queries:
        print(f"[Planner] 補充問題：")
        for q in extra_queries:
            print(f"  → {q}")
 
    return {
        "is_sufficient":   is_sufficient,
        "extra_queries":   extra_queries if not is_sufficient else [],
        "pending_queries": extra_queries if not is_sufficient else [],
    }


 
# ─── Node 6：Finalizer ────────────────────────────────────────────────────────
 
def finalizer_node(state: AgentState) -> dict:
    query        = state["original_query"]
    docs         = _deduplicate_docs(state.get("collected_docs", []))
    alt_schools  = state.get("alt_schools", [])
    distribution = state.get("distribution", [])
    profile      = state.get("active_profile", {})
    trigger_alt  = state.get("trigger_alt", False)
 
    print(f"\n[Finalizer] 共 {len(docs)} 筆去重文件，備案：{alt_schools}")
 
    if not docs:
        answer = "很抱歉，未能從資料庫中找到相關資訊。建議您直接前往各校官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}
 
    docs_sorted = sorted(
        docs,
        key=lambda d: d.get("rerank_score", d.get("rrf_score", 0)),
        reverse=True,
    )
 
    # 若有觸發備案，在 query 尾端附上提示，讓 Gemini 在回答中整合
    full_query = query
    if trigger_alt and alt_schools:
        alt_hint = (
            f"\n\n【系統提示】根據申請人背景（{profile}），"
            f"已自動推薦以下備案學校：{', '.join(alt_schools)}。"
            f"請在回答中整合目標校分析與備案學校建議，"
            f"並說明備案學校的錄取門檻與申請人背景的匹配程度。"
        )
        full_query = query + alt_hint
        print(f"[Finalizer] 附加備案提示至 query")
 
    _emit({"type": "llm_call", "purpose": "finalizer"})
    answer = generate_answer(
        query        = full_query,
        context_docs = docs_sorted,
        profile      = profile,
        distribution = distribution,
    )
 
    if answer:
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}
    else:
        msg = "Gemini 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": None}
 
 
# ─── 路由 & 圖建構 ────────────────────────────────────────────────────────────
 
def _route_after_planner(state: AgentState) -> str:
    """Planner 結束後：繼續搜尋 or 進入 Evaluator。"""
    return "finalize" if state.get("is_sufficient", True) else "search"
    # 注意：sufficient=True 時才去 Evaluator，這裡先接 evaluate
 
 
def _route_after_planner_v2(state: AgentState) -> str:
    """
    Planner 完成後路由：
    - 資料不足 → 繼續搜尋
    - 資料充足 → 進入 Evaluator 判斷是否需要備案
    """
    return "evaluate" if state.get("is_sufficient", True) else "search"
 
 
def _route_after_evaluator(state: AgentState) -> str:
    """
    Evaluator 完成後路由：
    - 需要備案 → AltSearcher
    - 不需要備案 → Finalizer
    """
    return "alt_search" if state.get("trigger_alt", False) else "finalize"
 
 
def _build_graph():
    builder = StateGraph(AgentState)
 
    builder.add_node("decompose",   decomposer_node)
    builder.add_node("search",      searcher_node)
    builder.add_node("plan",        planner_node)
    builder.add_node("evaluate",    evaluator_node)    # 新增
    builder.add_node("alt_search",  alt_searcher_node) # 新增
    builder.add_node("finalize",    finalizer_node)
 
    builder.set_entry_point("decompose")
    builder.add_edge("decompose", "search")
    builder.add_edge("search",    "plan")
 
    # Planner → Searcher（補搜）or Evaluator（充足）
    builder.add_conditional_edges(
        "plan",
        _route_after_planner_v2,
        {"search": "search", "evaluate": "evaluate"},
    )
 
    # Evaluator → AltSearcher or Finalizer
    builder.add_conditional_edges(
        "evaluate",
        _route_after_evaluator,
        {"alt_search": "alt_search", "finalize": "finalize"},
    )
 
    builder.add_edge("alt_search", "finalize")
    builder.add_edge("finalize",   END)
 
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
    執行 Agentic RAG 流程（含備案推薦）。
 
    Args:
        query:     使用者問題
        max_steps: LangGraph 最大步驟數（安全閥）
        verbose:   是否顯示詳細日誌
        on_event:  SSE 事件 callback
 
    Returns:
        最終回答字串，或 None
    """
    global _current_on_event
    _current_on_event = on_event
 
    try:
        if verbose:
            print(f"\n{'='*60}")
            print(f"   Agentic RAG（含備案推薦）啟動")
            print(f"   問題：{query}")
            print(f"   最大搜尋輪次：{MAX_ROUNDS}")
            print(f"{'='*60}")
 
        initial_state: AgentState = {
            "original_query":   query,
            "sub_queries":      [],
            "extra_queries":    [],
            "pending_queries":  [],
            "collected_docs":   [],
            "searched_queries": [],
            "is_sufficient":    False,
            "round_count":      0,
            "final_answer":     "",
            # 備案欄位
            "active_profile":   {},
            "trigger_alt":      False,
            "alt_schools":      [],
            "distribution":     [],
        }
 
        result_state = _get_graph().invoke(
            initial_state,
            {"recursion_limit": max_steps * 4},
        )
 
        if verbose:
            total_docs  = len(_deduplicate_docs(result_state.get("collected_docs", [])))
            rounds      = result_state.get("round_count", 0)
            alt_schools = result_state.get("alt_schools", [])
            print(f"\n{'='*60}")
            print(f"完成！搜尋 {rounds} 輪，彙整 {total_docs} 筆文件")
            if alt_schools:
                print(f"備案推薦：{', '.join(alt_schools)}")
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
 
    parser = argparse.ArgumentParser(description="Agentic RAG（含備案推薦）")
    parser.add_argument("query",       nargs="?", help="使用者問題")
    parser.add_argument("--max-steps", type=int, default=20)
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