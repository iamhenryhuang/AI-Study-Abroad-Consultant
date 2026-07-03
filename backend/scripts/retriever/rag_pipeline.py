# SQL 檢索 → 生成回答，或 Agentic RAG：LangGraph 流程

import sys
import argparse
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from retriever.sql_search import sql_search
from retriever.agent import run_agent
from generator.openai_client import generate_answer


def run_rag_pipeline(query: str) -> bool:
    """執行單次 text-to-SQL 檢索 + 生成回答（不經過 LangGraph agent）。"""
    print(f"\n開始執行 RAG 流程")
    print(f"   問題：'{query}'")

    print(f"  [RAG] 正在查詢資料庫...")
    results, sql = sql_search(query)

    if not results:
        print("未能查詢到相關資訊。")
        return False

    print(f"  [RAG] SQL: {sql}")
    print(f"  [RAG] 查詢到 {len(results)} 筆資料")

    print(f"  [RAG] 正在使用 OpenAI 生成回答...")
    answer = generate_answer(query, results)

    if answer:
        print("\n" + "=" * 30 + " OpenAI 回答 " + "=" * 30)
        print(answer)
        print("=" * 73 + "\n")
        return True
    else:
        print("生成回答失敗。")
        return False


def run_agent_pipeline(query: str, max_steps: int = 5, verbose: bool = True) -> bool:
    """執行 Agentic RAG 流程（LangGraph）。"""
    answer = run_agent(query, max_steps=max_steps, verbose=verbose)
    if answer:
        print("\n" + "=" * 30 + " LangGraph Agent 回答 " + "=" * 30)
        print(answer)
        print("=" * 73 + "\n")
        return True
    else:
        print("生成回答失敗。")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="執行 RAG 流程")
    parser.add_argument("query", nargs="?", help="使用者問題")
    parser.add_argument("--agent", action="store_true", help="使用 Agentic RAG（LangGraph）")
    parser.add_argument("--max-steps", type=int, default=5, help="Agent 最大迭代次數")
    args = parser.parse_args()

    q = args.query
    if not q:
        q = input("請輸入問題: ").strip()

    if q:
        if args.agent:
            run_agent_pipeline(q, max_steps=args.max_steps)
        else:
            run_rag_pipeline(q)
    else:
        print("未輸入問題。")
