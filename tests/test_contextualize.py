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
