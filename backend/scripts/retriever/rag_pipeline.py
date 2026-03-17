"""
rag_pipeline.py — RAG 完整流程（v2）

檢索 → 重排序 → 生成回答
或
Agentic RAG：LangGraph Planner-Tool Loop

v2 改動：
  - 回傳結果包含 source_url，讓 Gemini 可以在回答中引用網頁來源
  - run_rag_pipeline 新增 school_id 參數，支援指定學校限定搜尋
"""

import sys
import argparse
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from retriever.search import search_core
from retriever.agent import run_agent
from generator.gemini import generate_answer



def run_rag_pipeline(
    query: str,
    top_k: int = 7,
    school_id: str | None = None,
) -> bool:
    """
    執行完整的 RAG 流程。

    Args:
        query:           使用者提出的問題。
        top_k:           檢索的數量。
        school_id:       限定搜尋的學校（e.g. 'cmu', 'caltech'），None 表示全部。
    """
    print(f"\n開始執行 RAG 流程")
    print(f"   問題：'{query}'")
    if school_id:
        print(f"過濾學校：{school_id}")

    # 1. 檢索與重排序
    print(f"  [RAG] 正在檢索相關資料...")
    results = search_core(
        query, top_k=top_k, use_rerank=True, school_id=school_id
    )

    if not results:
        print("未能檢索到相關資訊。")
        return False

    print(f"  [RAG] 檢索到 {len(results)} 筆資料")

    # 2. 生成回答
    print(f"  [RAG] 正在使用 Gemini 生成回答...")
    answer = generate_answer(query, results)

    if answer:
        print("\n" + "=" * 30 + " Gemini 回答 " + "=" * 30)
        print(answer)
        print("=" * 73 + "\n")
        return True
    else:
        print("生成回答失敗。")
        return False


def run_agent_pipeline(
    query: str,
    max_steps: int = 5,
    verbose: bool = True,
) -> bool:
    """
    執行 Agentic RAG 流程（LangGraph Planner-Tool Loop）。

    Args:
        query:     使用者問題
        max_steps: 最大搜尋迭代次數
        verbose:   是否印出每步驟的推理過程
    """
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
    parser.add_argument("--agent", action="store_true", help="使用 Agentic RAG（LangGraph Loop）")
    parser.add_argument("--max-steps", type=int, default=5, help="Agent 最大迭代次數")
    parser.add_argument("--school", type=str, default=None, help="限定學校 e.g. cmu, caltech")
    parser.add_argument("--top-k", type=int, default=7, help="檢索數量")
    args = parser.parse_args()

    q = args.query
    if not q:
        q = input("請輸入問題: ").strip()

    if q:
        if args.agent:
            run_agent_pipeline(q, max_steps=args.max_steps)
        else:
            run_rag_pipeline(
                q,
                top_k=args.top_k,
                school_id=args.school,
            )
    else:
        print("未輸入問題。")
