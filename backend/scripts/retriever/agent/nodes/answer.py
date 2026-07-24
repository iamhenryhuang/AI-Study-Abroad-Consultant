"""Final answer generation and hallucination critic nodes."""

from __future__ import annotations

from langgraph.graph import END

from generator.answer import generate_answer, generate_answer_stream
from generator.context import clean_answer_text, format_context_for_prompt
from retriever.sql_search import get_known_school_ids

from ..prompts import _build_critic_prompt
from ..state import AgentState, _check_cancel, _emit
from .common import _call_llm, _parse_json_object

def finalizer_node(state: AgentState) -> dict:
    """
    使用 Verifier 判斷過的資料生成最終答案。
    若 Verifier 判定資料不足/文不對題，直接誠實告知並發送 answer 事件（不需要 Critic 複查）。
    若成功生成答案，交給 Critic 檢查有無幻覺後，再由 Critic 發送最終的 answer 事件。
    """
    query = state["original_query"]
    all_docs = state.get("verified_docs", [])

    print(f"\n[Finalizer] Verifier 判斷後的資料：{len(all_docs)} 筆")

    if not all_docs:
        mentioned_ids   = state.get("mentioned_school_ids", [])
        mentioned_names = state.get("mentioned_school_names", [])
        known = get_known_school_ids()

        # mentioned_school_ids 與 mentioned_school_names 是兩組獨立偵測結果，可能同時存在
        # （例如同時問已知的 MIT 和清單外的 University of Toronto），故分別判斷、不互斥。
        # school_ids 已限定在已知別名清單內，該清單與 DB 收錄範圍同步，故其恆在 known 中；
        # 真正判斷「有沒有收錄」要看 mentioned_school_names（含清單外的學校原文）。
        unknown_ids   = [sid for sid in mentioned_ids if sid not in known]
        unrecognized = unknown_ids + mentioned_names

        if unrecognized:
            names = "、".join(unrecognized)
            answer = f"很抱歉，目前系統尚未收錄 {names} 的資料，暫時無法回答此問題。建議您直接前往該校官方網站查詢。"
        else:
            answer = "很抱歉，資料庫中查無與此問題相關的欄位資訊。建議您直接前往各校官方網站查詢。"

        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    if not state.get("is_sufficient", True):
        reason = state.get("insufficiency_reason", "").strip()
        answer = "很抱歉，目前檢索到的資料與您的問題不符" + (f"（{reason}）" if reason else "") + "，暫時無法提供可靠的回答。建議您直接前往官方網站查詢。"
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    _check_cancel()
    _emit({"type": "llm_call", "purpose": "finalizer"})

    # 經驗資料不足：在答案前加註，並先當作第一個 chunk 送出（前端立即看到）
    sparse_note = ""
    if state.get("experience_sparse"):
        sparse_note = ("（提醒：此校的申請經驗回報目前較少，以下為現有資料；"
                       "系統已在背景補充更多，稍後再問可能更完整。）\n\n")
        _emit({"type": "answer_chunk", "text": sparse_note})

    full_text = sparse_note
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
        clean = clean_answer_text(full_text)
        return {"final_answer": clean, "generated_answer": True}
    else:
        msg = "OpenAI 生成失敗"
        print(f"[Finalizer] {msg}")
        _emit({"type": "error", "message": msg})
        return {"final_answer": ""}


def after_finalize(state: AgentState) -> str:
    """只有真的呼叫 LLM 生成過答案，才需要跑 Critic 複查；其餘（誠實告知/生成失敗）直接結束。"""
    if state.get("generated_answer", False):
        return "critic"
    return END


# ─── Node 6：Critic ───────────────────────────────────────────────────────────



def critic_node(state: AgentState) -> dict:
    """
    在答案生成後、回傳給使用者前，檢查是否有內容在 verified_docs 中找不到根據（幻覺）。
    只標記警告、不重新生成——發現問題就直接把警告附在答案後面，交由使用者自行確認。
    """
    answer   = state.get("final_answer", "") or ""
    all_docs = state.get("verified_docs", [])

    if not answer or not all_docs:
        _emit({"type": "answer", "text": answer})
        return {"final_answer": answer}

    _check_cancel()
    context_text = format_context_for_prompt(all_docs)

    try:
        parsed        = _parse_json_object(_call_llm(_build_critic_prompt(answer, context_text)))
        has_issue     = bool(parsed.get("has_issue", False))
        issue_summary = str(parsed.get("issue_summary", "")).strip()
    except Exception as e:
        print(f"[Critic] 判斷失敗（{e}），略過品質檢查")
        has_issue     = False
        issue_summary = ""

    if has_issue:
        print(f"[Critic] 發現潛在問題：{issue_summary}")
        final_text = answer + f"\n\n⚠️ 提醒：{issue_summary or '部分內容可能未完全對應參考資料，建議自行確認。'}"
    else:
        print("[Critic] 未發現幻覺/文不對題問題")
        final_text = answer

    _emit({"type": "answer", "text": final_text})
    return {"final_answer": final_text}
