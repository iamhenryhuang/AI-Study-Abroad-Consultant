"""把承接前文的跟隨問題改寫成獨立問題（multi-turn chat 的前置步驟）。

放在 agent 之前：agent 本體仍只吃單一 query，多輪上下文在此收斂成一句
獨立問題。LLM 失敗時降級回原始 query，不讓改寫擋住對話。
"""
from __future__ import annotations

from generator.client import call_llm


def _build_prompt(query: str, history: list[dict]) -> str:
    lines = []
    for m in history:
        role = "使用者" if m.get("role") == "user" else "助理"
        lines.append(f"{role}：{m.get('content', '')}")
    convo = "\n".join(lines)
    return f"""你是一個問題改寫助理。根據以下對話歷史，把使用者最新的「跟隨問題」改寫成一個不需要上下文也能獨立理解的完整問題。

規則：
- 把代名詞（它、那個、這所學校…）換成歷史中明確的對象。
- 若最新問題本身已是獨立問題，原樣輸出即可。
- 只輸出改寫後的問題，不要任何解釋或前綴。

【對話歷史】
{convo}

【最新跟隨問題】
{query}

【改寫後的獨立問題】"""


def contextualize_query(query: str, history: list[dict]) -> str:
    """有歷史時用 LLM 把跟隨問題改寫成獨立問題；無歷史直接回傳原 query。

    history: [{"role": "user"|"assistant", "content": str}, ...]
    改寫結果為空、或 LLM 呼叫失敗時，降級回原始 query。
    """
    if not history:
        return query
    try:
        rewritten = call_llm(_build_prompt(query, history))
        return rewritten.strip() or query
    except Exception as e:
        print(f"[contextualize] 改寫失敗，改用原始問題：{e}")
        return query
