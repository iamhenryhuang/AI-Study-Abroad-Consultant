"""CLI 入口：python -m retriever.react_baseline "問題"

跑一次 ReAct 對照組並印出最終答案 + 工具呼叫軌跡（軌跡是論文數據）。
刻意獨立於 backend/scripts/run.py，不動現有入口。
"""
import argparse
import io
import json
import sys

from .react_agent import run_react


def main() -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="ReAct baseline（論文對照組）：LLM 自由調度工具，無 Verifier/Critic 護欄")
    parser.add_argument("query", nargs="?", help="使用者問題")
    parser.add_argument("--profile", default=None,
                        help='選校用成績檔案 JSON，例如 \'{"gpa":3.5,"toefl":100}\'')
    parser.add_argument("--max-steps", type=int, default=10,
                        help="ReAct 迴圈最大步數（防兜圈子；預設 10）")
    args = parser.parse_args()

    query = args.query or input("請輸入問題：").strip()
    if not query:
        print("未輸入問題。")
        return 1

    profile = None
    if args.profile:
        try:
            profile = json.loads(args.profile)
        except json.JSONDecodeError as e:
            print(f"--profile 不是合法 JSON：{e}")
            return 1

    result = run_react(query, profile=profile, max_steps=args.max_steps, verbose=True)

    print("\n" + "=" * 28 + " 工具呼叫軌跡 " + "=" * 28)
    if result["trace"]:
        for i, step in enumerate(result["trace"], 1):
            print(f"{i}. {step['tool']}  args={json.dumps(step['args'], ensure_ascii=False)}")
    else:
        print("（未呼叫任何工具，LLM 直接作答）")
    print(f"共 {len(result['trace'])} 次工具呼叫")

    print("\n" + "=" * 30 + " 最終回答 " + "=" * 30)
    print(result["answer"])
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
