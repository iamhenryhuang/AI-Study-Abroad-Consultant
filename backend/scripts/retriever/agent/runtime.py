"""Public runtime API for executing the retriever agent."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

# 明確載入 backend/.env：無參數的 load_dotenv() 只找 CWD 的 .env（專案根沒有
# 這個檔），先前能運作是仰賴其他模組先被 import 的副作用。
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_BACKEND_ROOT / ".env")

from .graph import _get_graph
from .state import (
    AgentCancelledError,
    clear_execution_context,
    create_initial_state,
    set_execution_context,
)


def run_agent(
    query: str,
    max_steps: int = 10,
    verbose: bool = True,
    on_event: Optional[Callable[[dict], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str | None:
    """Execute the Agentic RAG workflow and return its final answer."""
    set_execution_context(on_event, cancel_event)

    try:
        if verbose:
            print(f"\n{'=' * 60}")
            print("   Agentic RAG 啟動")
            print(f"   Agentic RAG 啟動時間 : {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   問題：{query}")
            print(f"{'=' * 60}")

        result_state = _get_graph().invoke(
            create_initial_state(query),
            {"recursion_limit": max_steps * 4},
        )

        if verbose:
            total_docs = len(result_state.get("verified_docs", []))
            print(f"\n{'=' * 60}")
            print(f"完成！彙整 {total_docs} 筆去重資料")
            print(f"{'=' * 60}")
            print(f"   Agentic RAG 結束時間 : {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return result_state.get("final_answer") or None

    except AgentCancelledError:
        print("[Agent] 收到取消信號，提前結束。")
        return None

    finally:
        clear_execution_context()
