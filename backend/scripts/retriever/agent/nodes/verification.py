"""Retrieval verification, fallback routing, and query refinement."""

from __future__ import annotations

from generator.context import format_context_for_prompt

from ..prompts import _build_refine_prompt, _build_verify_prompt
from ..state import AgentState, _check_cancel, _emit
from .common import (
    _call_llm,
    _deduplicate_docs,
    _llm_is_unavailable,
    _parse_json_object,
    _parse_professor_query,
)

def verifier_node(state: AgentState) -> dict:
    """
    在生成答案前，檢查 search + extension_function 收集到的資料是否真的與問題相關、足以回答。
    只做好壞判斷，不重試、不重新查詢——文不對題時讓 finalizer 誠實告知使用者。
    """
    query = state["original_query"]

    search_docs     = state.get("collected_docs", [])
    fulltext_docs   = state.get("fulltext_docs", [])
    extension_docs  = state.get("extension_docs", [])
    experience_docs = state.get("experience_docs", [])
    recommend_docs  = state.get("recommend_docs", [])

    all_docs = _deduplicate_docs(
        extension_docs + search_docs + experience_docs + recommend_docs + fulltext_docs
    )

    if not all_docs:
        return {"verified_docs": [], "is_sufficient": False, "insufficiency_reason": ""}

    # 經驗類問題短路：needs_experience 且真的撈到經驗回報時直接放行。
    # Verifier 的「資訊點是否被觸及」檢查是為官方欄位設計的，對「無標準答案的錄取經驗題」
    # 不適用（案例本身就是答案），送審只會被誤判不足。護欄由 generator 的非官方警語負責。
    if state.get("needs_experience", False) and experience_docs:
        print(f"[Verifier] needs_experience 且有 {len(experience_docs)} 筆經驗回報，直接放行")
        return {"verified_docs": all_docs, "is_sufficient": True, "insufficiency_reason": ""}

    if state.get("wants_recommendation", False) and recommend_docs:
        print(f"[Verifier] wants_recommendation 且有 {len(recommend_docs)} 筆推薦，直接放行")
        return {"verified_docs": all_docs, "is_sufficient": True, "insufficiency_reason": ""}

    _check_cancel()
    context_text = format_context_for_prompt(all_docs)

    if _llm_is_unavailable():
        print("[Verifier] LLM 額度不可用，直接採用已檢索到的資料")
        return {
            "verified_docs": all_docs,
            "is_sufficient": True,
            "insufficiency_reason": "",
        }

    try:
        parsed = _parse_json_object(_call_llm(_build_verify_prompt(query, context_text)))
        is_sufficient = bool(parsed.get("sufficient", True))
        reason        = str(parsed.get("reason", "")).strip()
    except Exception as e:
        print(f"[Verifier] 判斷失敗（{e}），預設視為足夠，交由生成階段自行把關")
        is_sufficient = True
        reason        = ""

    print(f"[Verifier] is_sufficient={is_sufficient}" + (f"  reason={reason}" if reason else ""))

    return {
        "verified_docs":        all_docs,
        "is_sufficient":        is_sufficient,
        "insufficiency_reason": reason,
    }


def after_verify(state: AgentState) -> str:
    """
    Verifier 完成後的路由（分層 fallback：SQL → 全文檢索 → 改寫重查）：
    - is_sufficient=True → finalize
    - is_sufficient=False 且「本輪還沒做過全文檢索」→ fulltext（對 document_chunks 補查後回到 verify）
    - is_sufficient=False 且「已做過全文檢索」且曾檢索到資料且尚未重試過 → refine（改寫查詢再跑一輪 SQL）
    - 完全查無資料，或已重試過仍不足 → finalize（誠實告知）
    """
    if state.get("is_sufficient", True):
        return "finalize"

    # 第一層 fallback：SQL 不足時先擴大到全文檢索（本輪只做一次）
    if not state.get("fulltext_done", False):
        return "fulltext"

    # 第二層 fallback：全文檢索仍不足，且曾檢索到資料、尚未重試過，才改寫查詢重跑
    if state.get("verified_docs") and state.get("retry_count", 0) < 1:
        return "refine"

    return "finalize"


# ─── Node 4：Refiner ──────────────────────────────────────────────────────────



def refiner_node(state: AgentState) -> dict:
    """
    Verifier 判定資料不足時，參考 insufficiency_reason 重新改寫子問題 / 教授查詢，
    交由 route_to_retrieval 重新跑一次 search / extension_function。最多執行 1 輪。
    """
    query           = state["original_query"]
    reason          = state.get("insufficiency_reason", "")
    sub_queries     = state.get("sub_queries", [])
    professor_query = state.get("professor_query")
    retry_count     = state.get("retry_count", 0)

    print(f"\n[Refiner] 資料不足（{reason or '無具體原因'}），重新產生查詢（第 {retry_count + 1} 次重試）")
    _emit({"type": "thinking", "step": "refine"})

    try:
        parsed = _parse_json_object(_call_llm(_build_refine_prompt(query, reason, sub_queries, professor_query)))

        new_sub_queries = [
            str(q).strip() for q in parsed.get("sub_queries", []) if str(q).strip()
        ] or sub_queries

        new_professor_query = _parse_professor_query(parsed.get("professor_query")) or professor_query
    except Exception as e:
        print(f"[Refiner] 解析失敗（{e}），沿用原本查詢重試")
        new_sub_queries     = sub_queries
        new_professor_query = professor_query

    for i, q in enumerate(new_sub_queries, 1):
        print(f"  Q{i}: {q}")

    return {
        "sub_queries":     new_sub_queries,
        "professor_query": new_professor_query,
        "retry_count":     retry_count + 1,
        "fulltext_docs":   [],      # 清掉上一輪全文檢索結果
        "fulltext_done":   False,   # 允許改寫後的新查詢再走一次全文檢索
    }
