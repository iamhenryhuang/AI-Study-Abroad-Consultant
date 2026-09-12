"""ReAct baseline（論文對照組）。

與現有 LangGraph 狀態機（retriever/agent/）並存、互不干擾：
重用同一批檢索函式，但改用「LLM 自由決定調度」的 ReAct 迴圈，
且刻意不含 Verifier / Critic 護欄，作為「結構化 pipeline vs. 自主推理」的對照。

入口：
    from retriever.react_baseline.react_agent import run_react
    python -m retriever.react_baseline "問題"
"""

from .react_agent import run_react

__all__ = ["run_react"]
