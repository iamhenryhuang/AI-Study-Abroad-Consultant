# Task 1 Brief — 後端 contextualize_query（查詢改寫）

這是「多輪 AI 諮詢聊天頁」功能的第一個 task。你只需完成這個 task，不要碰其他檔案。
以下就是你的需求規格，數值與程式碼請照抄使用。

## Global Constraints（適用所有 task）

- `call_llm(prompt, model_name=DEFAULT_MODEL, temperature=0.0) -> str`，來自 `generator.client`。
- 後端測試框架：unittest，執行 `python -m unittest discover tests -p "test_x.py" -v`。
- 測試檔開頭需：`sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))`。
- Agent 本體（`retriever/agent/`）、hybrid search、DB schema 全部不動。
- 環境：Windows。用 Bash 工具跑指令時若 `git` 找不到，改用 PowerShell 工具跑 git。Python 用 miniconda（`python` 直接可用）。

## Files
- Create: `backend/scripts/retriever/contextualize.py`
- Test: `tests/test_contextualize.py`

## Interface（後續 task 依賴）
- `contextualize_query(query: str, history: list[dict]) -> str`
  - `history` 為 `[{"role": "user"|"assistant", "content": str}, ...]`
  - 無歷史（空 list）→ 原樣回傳 query 且**不呼叫 LLM**
  - LLM 改寫結果為空字串、或呼叫拋例外 → 降級回原始 query

## Step 1: 寫失敗測試 `tests/test_contextualize.py`

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import contextualize


class TestContextualizeQuery(unittest.TestCase):
    def test_no_history_returns_query_unchanged_without_llm(self):
        with patch.object(contextualize, "call_llm") as mock_llm:
            out = contextualize.contextualize_query("CMU 截止日是什麼？", [])
        self.assertEqual(out, "CMU 截止日是什麼？")
        mock_llm.assert_not_called()

    def test_with_history_returns_rewritten_query(self):
        history = [
            {"role": "user", "content": "CMU 的 GPA 門檻？"},
            {"role": "assistant", "content": "最低 3.0"},
        ]
        with patch.object(contextualize, "call_llm",
                          return_value="CMU 的申請截止日是什麼？") as mock_llm:
            out = contextualize.contextualize_query("那它的截止日呢？", history)
        self.assertEqual(out, "CMU 的申請截止日是什麼？")
        mock_llm.assert_called_once()

    def test_blank_rewrite_falls_back_to_original(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        with patch.object(contextualize, "call_llm", return_value="   "):
            out = contextualize.contextualize_query("那截止日呢？", history)
        self.assertEqual(out, "那截止日呢？")

    def test_llm_failure_degrades_to_original_query(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        with patch.object(contextualize, "call_llm", side_effect=RuntimeError("boom")):
            out = contextualize.contextualize_query("那截止日呢？", history)
        self.assertEqual(out, "那截止日呢？")


if __name__ == "__main__":
    unittest.main()
```

## Step 2: 確認測試失敗
Run: `python -m unittest discover tests -p "test_contextualize.py" -v`
Expected: FAIL/ERROR `ModuleNotFoundError: No module named 'retriever.contextualize'`

## Step 3: 寫實作 `backend/scripts/retriever/contextualize.py`

```python
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
```

## Step 4: 確認測試通過
Run: `python -m unittest discover tests -p "test_contextualize.py" -v`
Expected: PASS（4 tests OK）

## Step 5: Commit
```bash
git add backend/scripts/retriever/contextualize.py tests/test_contextualize.py
git commit -m "feat: add query contextualization for multi-turn chat"
```
（若 Bash 的 git 不可用，改用 PowerShell 執行相同 git 指令。）
