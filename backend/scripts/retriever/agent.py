"""Backward-compatible script entry point for the retriever agent package."""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from retriever.agent import AgentCancelledError, AgentState, run_agent

__all__ = ["AgentCancelledError", "AgentState", "run_agent"]


if __name__ == "__main__":
    from retriever.agent.__main__ import main

    raise SystemExit(main())
