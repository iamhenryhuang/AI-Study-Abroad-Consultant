"""Command-line entry point for ``python -m retriever.agent``."""

import argparse
import io
import sys

from .runtime import run_agent


def main() -> int:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    parser = argparse.ArgumentParser(description="Agentic RAG（LangGraph）")
    parser.add_argument("query", nargs="?", help="使用者問題")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="安全閥（實際 recursion_limit = max_steps * 4）",
    )
    args = parser.parse_args()

    query = args.query or input("請輸入問題：").strip()
    if not query:
        print("未輸入問題。")
        return 1

    answer = run_agent(query, max_steps=args.max_steps, verbose=True)
    if not answer:
        print("生成回答失敗。")
        return 1

    print("\n" + "=" * 30 + " 最終回答 " + "=" * 30)
    print(answer)
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
