"""手刻的 ReAct（Reason + Act）迴圈——論文對照組。

刻意用與現有 agent 相同的 call_llm（同一把 key、同款判斷模型），唯一變因是「調度方式」：
現有 agent 是寫死拓撲的狀態機；這裡是「LLM 每一步自己決定叫哪個工具、何時收尾」的自由迴圈。

刻意「不含」的東西（純 ReAct 的定義）：
  - 無 Verifier（不預先判斷資料夠不夠）
  - 無 Critic（不做幻覺複查）
  - 無防幻覺護欄 prompt（不叮嚀「非官方須標註」「不可當門檻」等）
  - 無並行、無寫死路由

迴圈協定：每步要 LLM 只輸出一個 JSON——
  呼叫工具： {"thought": "...", "tool": "<name>", "args": {...}}
  收尾：     {"thought": "...", "final_answer": "..."}
工具的 observation 併回對話，餵下一步；到 max_steps 仍未收尾則強制收尾。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from generator.client import call_llm
from generator.context import format_context_for_prompt
from retriever.agent.prompts import _build_critic_prompt

from .tools import TOOLS

_PREVIEW_LEN = 500      # observation / trace 預覽截斷長度


def _tool_catalog() -> str:
    """把工具註冊表描述組成給 LLM 看的清單。"""
    return "\n".join(
        f"- {name}：{spec['description']}" for name, spec in TOOLS.items()
    )


def _build_system_prompt() -> str:
    return f"""你是一個留學申請顧問。你可以使用下列工具查資料，然後回答使用者的問題。

【可用工具】
{_tool_catalog()}

【運作方式】
每一輪你只能輸出「一個」JSON 物件，不要有其他文字、不要 markdown code fence：

  要呼叫工具時：
    {{"thought": "你的推理", "tool": "工具名稱", "args": {{工具參數}}}}

  資料已足夠、要給最終答案時：
    {{"thought": "你的推理", "final_answer": "給使用者的完整回答"}}

【規則】
- school_id 是小寫學校代碼（例如 mit、cmu、stanford、ucla、gatech）。你要自己從問題判斷。
- 一次只呼叫一個工具；看到工具結果（observation）後再決定下一步。
- 查到足夠資料就用 final_answer 收尾，不要無止盡地查。
- 只能輸出合法 JSON，不要多加說明文字。"""


def _extract_json(raw: str) -> dict | None:
    """從 LLM 回應抽出第一個 JSON 物件（容忍前後雜訊 / code fence）。"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _preview(obj) -> str:
    """把工具結果轉成給 LLM 當 observation、以及存進 trace 的短字串。"""
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= _PREVIEW_LEN else text[:_PREVIEW_LEN] + "…（截斷）"


def _run_tool(name: str, args: dict) -> tuple[str, list]:
    """派工到 TOOLS[name]，回傳 (observation 字串, 完整結果 docs)。

    observation 是給 LLM 看的截斷字串；完整 docs 另外回傳供 Critic 使用（enable_critic
    時彙整成參考資料）。工具不存在或報錯都轉成 error observation 餵回 LLM（不讓迴圈當機），
    此時 docs 為空 list。"""
    spec = TOOLS.get(name)
    if spec is None:
        return f"[error] 沒有名為 '{name}' 的工具。可用工具：{', '.join(TOOLS)}", []
    try:
        result = spec["fn"](**(args or {}))
    except Exception as e:                       # noqa: BLE001 — 任何工具錯誤都回饋給 LLM
        return f"[error] 工具 '{name}' 執行失敗：{type(e).__name__}: {e}", []
    docs = result if isinstance(result, list) else []
    return _preview(result), docs


def _run_loop(query: str, profile: dict | None, max_steps: int,
              verbose: bool) -> tuple[str, list, list]:
    """跑 ReAct 主迴圈，回傳 (answer, trace, collected_docs)。

    collected_docs 累積每步工具回傳的『完整 docs』（非截斷）——LLM 看的仍是截斷版
    observation，這份完整資料只供 enable_critic 時當 Critic 的參考資料。
    """
    system = _build_system_prompt()
    user_ctx = f"使用者問題：{query}"
    if profile:
        user_ctx += f"\n使用者成績檔案：{json.dumps(profile, ensure_ascii=False)}"

    transcript = f"{system}\n\n{user_ctx}\n"
    trace: list[dict] = []
    collected_docs: list = []

    for step in range(max_steps):
        raw = call_llm(transcript + "\n請輸出下一步的 JSON：")
        if verbose:
            print(f"\n[ReAct step {step + 1}] LLM → {raw[:200]}")

        action = _extract_json(raw)
        if action is None:
            transcript += f"\n[assistant] {raw}\n[system] 你的輸出不是合法 JSON，請只輸出一個 JSON 物件。\n"
            continue

        if "final_answer" in action:
            return str(action["final_answer"]), trace, collected_docs

        tool_name = action.get("tool")
        args = action.get("args") or {}
        observation, docs = _run_tool(tool_name, args)
        collected_docs.extend(docs)

        trace.append({"tool": tool_name, "args": args, "result_preview": observation})
        if verbose:
            print(f"[ReAct step {step + 1}] tool={tool_name} args={args}")
            print(f"[ReAct step {step + 1}] observation → {observation[:200]}")

        transcript += (
            f"\n[assistant] {json.dumps(action, ensure_ascii=False)}"
            f"\n[observation] {observation}\n"
        )

    # 撞步數上限仍未收尾 → 用目前手上的資料強制生成一個答案（回傳字串，不無限迴圈）
    forced = call_llm(
        transcript
        + f"\n[system] 已達步數上限（{max_steps} 步）。請根據目前已查到的資料，"
          "直接輸出給使用者的最終回答（純文字，不要再呼叫工具、不要 JSON）："
    )
    answer = _clean_answer(forced) or "（已達步數上限，無法在限制內完成回答）"
    return answer, trace, collected_docs


def _collect_docs_for_critic(query: str, profile: dict | None = None,
                             max_steps: int = 10) -> list:
    """跑一次迴圈只取 collected_docs（測試/除錯用）。"""
    _, _, docs = _run_loop(query, profile, max_steps, verbose=False)
    return docs


def _apply_critic(answer: str, docs: list) -> tuple[str, bool]:
    """對 ReAct 的最終答案套用現有 Critic 幻覺複查（第三組專用）。

    重用 agent 的 _build_critic_prompt + generator 的 format_context_for_prompt，
    邏輯對齊 agent/nodes/answer.py 的 critic_node：判 has_issue 就把 ⚠️ 提醒附在答案後，
    不重新生成。回傳 (最終答案, 是否觸發警告)。

    公平性侷限：Critic 檢查的是「答案 vs. ReAct 自己撈到的 docs 有無根據」，不檢查那些
    docs 是否為官方來源。ReAct 若把非官方經驗談當根據，Critic 可能放行——這正是要觀測的。
    """
    if not answer or not docs:
        return answer, False
    context_text = format_context_for_prompt(docs)
    try:
        raw = call_llm(_build_critic_prompt(answer, context_text))
        parsed = _extract_json(raw) or {}
        has_issue = bool(parsed.get("has_issue", False))
        issue = str(parsed.get("issue_summary", "")).strip()
    except Exception as e:                       # noqa: BLE001
        print(f"[ReAct+Critic] Critic 判斷失敗（{e}），略過")
        return answer, False
    if has_issue:
        return answer + f"\n\n⚠️ 提醒：{issue or '部分內容可能未完全對應參考資料，建議自行確認。'}", True
    return answer, False


def run_react(query: str, profile: dict | None = None,
              max_steps: int = 10, verbose: bool = False,
              enable_critic: bool = False) -> dict:
    """跑一次 ReAct 迴圈。

    enable_critic=False（預設）為純 ReAct（無護欄）；True 為第三組「ReAct + Critic」：
    迴圈跑完後，用累積的完整 docs 對最終答案做一次幻覺複查。

    Returns:
        {"answer": str, "trace": [...], "critic_applied": bool, "critic_flagged": bool}
        trace / critic_* 皆為論文數據。
    """
    answer, trace, collected_docs = _run_loop(query, profile, max_steps, verbose)

    if not enable_critic:
        return {"answer": answer, "trace": trace,
                "critic_applied": False, "critic_flagged": False}

    final, flagged = _apply_critic(answer, collected_docs)
    if verbose:
        print(f"[ReAct+Critic] critic_flagged={flagged}")
    return {"answer": final, "trace": trace,
            "critic_applied": True, "critic_flagged": flagged}


def _clean_answer(text: str) -> str:
    """強制收尾時 LLM 可能仍回 JSON（即使被要求純文字）。若整段是 JSON，抽出
    final_answer / answer / thought 當答案，避免最終答案殘留原始 JSON 字串。"""
    text = (text or "").strip()
    action = _extract_json(text)
    if action is not None:
        for key in ("final_answer", "answer", "thought"):
            val = action.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return text
