"""評測 runner：吃 eval/questions.json，對每題各跑「現有 agent」與「ReAct baseline」，
自動判分並輸出逐題結果 + 兩系統指標摘要（論文主結果表）。

只算零爭議、全自動的指標：
  - A 類答對率（數值/語義包含比對，比對 DB 標答）
  - E 類誠實拒答率（未收錄校該不該說「沒收錄」）
  - E 類「亂報數字率」＝幻覺代理（未拒答又報出具體分數）
  - 平均工具呼叫次數、平均延遲

刻意不算「一般幻覺率」、不套 Critic（見 brainstorming 討論：跨系統套 Critic 參考資料不對等、
且球員兼裁判）。幻覺用 E 類的 leaked_number 當乾淨代理。

判分（score_*）為純函式可單元測試；兩系統包裝（run_*_case）實跑時才連 DB / 打 LLM。

用法：
    python eval/run_eval.py --limit 6                 # 先小跑前 6 題驗證
    python eval/run_eval.py --system react            # 只跑 ReAct
    python eval/run_eval.py                            # 跑全部、兩系統
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

_BACKEND_SCRIPTS = Path(__file__).resolve().parent.parent / "backend" / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

_QUESTIONS_DEFAULT = Path(__file__).resolve().parent / "questions.json"
_RESULTS_DEFAULT = Path(__file__).resolve().parent / "results.json"

# TOEFL/GPA 形狀的數字（用來偵測 E 類「亂報分數」）：GPA 0–4 一位小數，或 TOEFL 60–120 整數
_SCORE_SHAPED = re.compile(r"\b(?:[0-3]\.\d{1,2}|4\.0{1,2}|(?:6[0-9]|[7-9][0-9]|1[01][0-9]|120))\b")

# E 類「誠實拒答」的訊號詞（中英）
_REFUSAL_MARKERS = (
    "未收錄", "沒有收錄", "無法提供", "查無", "找不到", "沒有相關資料", "不在收錄",
    "not covered", "not in our database", "could not find", "couldn't find",
    "no information", "not available", "don't have", "do not have", "unable to",
)

# 小整數的國字 / 英文單字寫法（推薦信封數等常這樣寫）。只涵蓋 1–5 這種小數字。
_SMALL_INT_WORDS: dict[int, tuple[str, ...]] = {
    1: ("一", "one"), 2: ("兩", "二", "two"), 3: ("三", "three"),
    4: ("四", "four"), 5: ("五", "five"),
}

# gre_required 各值對應的答案語義關鍵字
_GRE_MARKERS = {
    "not_accepted": ("not accepted", "not required", "no gre", "不接受", "不需要", "不用", "無需"),
    "optional":     ("optional", "選擇性", "選填", "可選", "非必"),
    "required":     ("required", "必須", "必要", "需要繳交", "需提交"),
}


# ─── 判分純函式 ────────────────────────────────────────────────────────────────

def _number_in_text(target: float, text: str) -> bool:
    """target 這個數字是否以「完整數值 token」出現在 text（忽略千分位逗號）。

    容忍等值的不同寫法：整數 7 也接受 '7.0'；小數 3.5 也接受 '3.50'。
    邊界規則：前不接數字，後不接數字；後面若接小數點，該小數點後不可再是數字
    （才不會把 '100.' 句尾誤擋、也不會讓 3.5 命中 3.55、100 命中 1000）。
    """
    clean = text.replace(",", "")
    if float(target).is_integer():
        n = int(target)
        candidates = {str(n), f"{n}.0", f"{n}.00"}    # 3 也接受 3.0 / 3.00；100 接受 100.0
        # 小整數（如推薦信封數）常被寫成國字/英文單字（「三封」/「three」）。
        # 先在完整文字上比對這些字詞，命中就算對（不受下方 token 邊界規則限制）。
        word = _SMALL_INT_WORDS.get(n)
        if word:
            low = text.lower()
            if any(w in low or w in text for w in word):
                return True
    else:
        s = ("%f" % target).rstrip("0").rstrip(".")   # 3.50 → "3.5"
        candidates = {s, s + "0"}                       # 也接受 3.50
    for cand in candidates:
        # 前面不可緊接數字或小數點；後面不可緊接數字，且不可「小數點+數字」（延續成更長小數）
        if re.search(rf"(?<![\d.]){re.escape(cand)}(?!\d)(?!\.\d)", clean):
            return True
    return False


def score_structured(answer: str, question: dict) -> bool:
    """A 類是否答對：數值欄位比數字包含；gre_required 比語義關鍵字。"""
    if not answer:
        return False
    field = question.get("field")
    expected = question.get("expected")

    if field == "gre_required":
        markers = _GRE_MARKERS.get(str(expected), ())
        low = answer.lower()
        return any(m in low or m in answer for m in markers)

    # 其餘皆為數值欄位
    try:
        return _number_in_text(float(expected), answer)
    except (TypeError, ValueError):
        # 非數值、非 gre 的意外欄位：退回字串包含
        return str(expected).lower() in answer.lower()


def score_not_covered(answer: str) -> dict:
    """E 類：{refused, leaked_number}。報出具體分數即視為未拒答（幻覺代理）。"""
    if not answer:
        # 空答案：既沒拒答宣告、也沒亂報 → 保守記為未拒答、無洩漏
        return {"refused": False, "leaked_number": False}
    leaked = bool(_SCORE_SHAPED.search(answer.replace(",", "")))
    low = answer.lower()
    said_refusal = any(m in low or m in answer for m in _REFUSAL_MARKERS)
    # 亂報數字優先：報了分數就算幻覺，不算誠實拒答（即使句中也有「建議查官網」）
    refused = said_refusal and not leaked
    return {"refused": refused, "leaked_number": leaked}


def score_one(answer: str, question: dict) -> dict:
    """依題型判分，回統一結果欄位。"""
    if question["type"] == "A_structured":
        return {"correct": score_structured(answer, question), "refused": None,
                "leaked_number": None}
    if question["type"] == "E_not_covered":
        nc = score_not_covered(answer)
        return {"correct": nc["refused"], "refused": nc["refused"],
                "leaked_number": nc["leaked_number"]}
    return {"correct": None, "refused": None, "leaked_number": None}


# ─── 兩系統包裝（實跑時才 import，避免單元測試載入重模型/連 DB） ────────────────

def run_agent_case(query: str, max_steps: int = 10) -> dict:
    """跑現有 LangGraph agent。用 on_event 攔 tool_call 事件數當工具呼叫次數。"""
    from retriever.agent.runtime import run_agent

    tool_calls = 0

    def _collect(ev: dict):
        nonlocal tool_calls
        if ev.get("type") == "tool_call":
            tool_calls += 1

    t0 = time.perf_counter()
    answer = run_agent(query, max_steps=max_steps, verbose=False, on_event=_collect)
    latency = time.perf_counter() - t0
    return {"answer": answer or "", "tool_calls": tool_calls, "latency_s": round(latency, 3)}


def run_react_case(query: str, max_steps: int = 10) -> dict:
    """跑 ReAct baseline。工具呼叫次數直接讀 trace 長度。"""
    from retriever.react_baseline.react_agent import run_react

    t0 = time.perf_counter()
    result = run_react(query, max_steps=max_steps, verbose=False)
    latency = time.perf_counter() - t0
    return {"answer": result["answer"], "tool_calls": len(result["trace"]),
            "latency_s": round(latency, 3)}


def run_react_critic_case(query: str, max_steps: int = 10) -> dict:
    """跑第三組 ReAct + Critic（ablation：純 ReAct 加回事後 Critic 幻覺複查）。"""
    from retriever.react_baseline.react_agent import run_react

    t0 = time.perf_counter()
    result = run_react(query, max_steps=max_steps, verbose=False, enable_critic=True)
    latency = time.perf_counter() - t0
    return {"answer": result["answer"], "tool_calls": len(result["trace"]),
            "latency_s": round(latency, 3)}


_RUNNERS = {"agent": run_agent_case, "react": run_react_case,
            "react_critic": run_react_critic_case}


# ─── 主流程 ────────────────────────────────────────────────────────────────────

def _summarize(rows: list[dict]) -> dict:
    """把逐題結果彙整成每系統的指標摘要。"""
    summary: dict[str, dict] = {}
    for system in {r["system"] for r in rows}:
        srows = [r for r in rows if r["system"] == system]
        a = [r for r in srows if r["type"] == "A_structured"]
        e = [r for r in srows if r["type"] == "E_not_covered"]
        summary[system] = {
            "n_questions": len(srows),
            "A_accuracy": round(sum(r["correct"] for r in a) / len(a), 3) if a else None,
            "E_refusal_rate": round(sum(r["refused"] for r in e) / len(e), 3) if e else None,
            "E_leaked_number_rate": round(sum(r["leaked_number"] for r in e) / len(e), 3) if e else None,
            "avg_tool_calls": round(sum(r["tool_calls"] for r in srows) / len(srows), 2) if srows else None,
            "avg_latency_s": round(sum(r["latency_s"] for r in srows) / len(srows), 2) if srows else None,
        }
    return summary


def run_eval(questions: list[dict], systems: list[str], max_steps: int = 10,
             verbose: bool = True) -> dict:
    rows: list[dict] = []
    for i, q in enumerate(questions, 1):
        for system in systems:
            if verbose:
                print(f"[{i}/{len(questions)}] {system:5s} | {q['id']}")
            case = _RUNNERS[system](q["question"], max_steps=max_steps)
            scored = score_one(case["answer"], q)
            rows.append({
                "system": system, "question_id": q["id"], "type": q["type"],
                "question": q["question"], "answer": case["answer"],
                "expected": q.get("expected"),
                "correct": scored["correct"], "refused": scored["refused"],
                "leaked_number": scored["leaked_number"],
                "tool_calls": case["tool_calls"], "latency_s": case["latency_s"],
            })
    return {"rows": rows, "summary": _summarize(rows)}


def _print_summary(summary: dict) -> None:
    print("\n" + "=" * 70)
    print("指標摘要")
    print("=" * 70)
    hdr = f"{'system':8s} {'A答對率':>8s} {'E拒答率':>8s} {'E亂報率':>8s} {'工具/題':>8s} {'延遲s':>8s}"
    print(hdr)
    for system, m in summary.items():
        print(f"{system:8s} {str(m['A_accuracy']):>8s} {str(m['E_refusal_rate']):>8s} "
              f"{str(m['E_leaked_number_rate']):>8s} {str(m['avg_tool_calls']):>8s} "
              f"{str(m['avg_latency_s']):>8s}")
    print("=" * 70)


def main() -> int:
    import io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="評測 runner：agent vs. ReAct baseline")
    parser.add_argument("--questions", default=str(_QUESTIONS_DEFAULT))
    parser.add_argument("--out", default=str(_RESULTS_DEFAULT))
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 題（先小跑用）")
    parser.add_argument("--system",
                        choices=["agent", "react", "react_critic", "both", "all"],
                        default="both",
                        help="both=agent+react；all=三組全跑；或指定單一系統")
    parser.add_argument("--max-steps", type=int, default=10)
    args = parser.parse_args()

    questions = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[:args.limit]
    if args.system == "both":
        systems = ["agent", "react"]
    elif args.system == "all":
        systems = ["agent", "react", "react_critic"]
    else:
        systems = [args.system]

    print(f"題數：{len(questions)}｜系統：{', '.join(systems)}")
    result = run_eval(questions, systems, max_steps=args.max_steps)

    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n逐題結果已寫入 {args.out}")
    _print_summary(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
