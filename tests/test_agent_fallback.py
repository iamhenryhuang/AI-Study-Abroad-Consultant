import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import sql_search as sql_module
from retriever import rag_pipeline
from generator import client as generator_client
from retriever.agent.nodes.answer import _extractive_fallback_answer
from retriever.agent.nodes.decompose import _fallback_intent


class DeterministicIntentFallbackTest(unittest.TestCase):
    def test_professor_list_intent(self):
        parsed = _fallback_intent("Stanford 有哪些 AI 教授？")
        self.assertEqual(parsed["professor_list_query"], {"school_id": "stanford"})
        self.assertFalse(parsed["needs_sql_search"])

    def test_experience_intent(self):
        parsed = _fallback_intent("Purdue CS 錄取者通常有什麼 GPA 背景？")
        self.assertTrue(parsed["needs_experience"])
        self.assertFalse(parsed["needs_sql_search"])

    def test_user_shared_experience_intent(self):
        parsed = _fallback_intent("Stanford 有哪些使用者分享經驗？")
        self.assertTrue(parsed["needs_experience"])
        self.assertFalse(parsed["needs_sql_search"])

    def test_recommendation_profile(self):
        parsed = _fallback_intent("GPA 3.8 TOEFL 105 GRE 325，請推薦學校")
        self.assertTrue(parsed["wants_recommendation"])
        self.assertEqual(parsed["profile"], {"gpa": 3.8, "toefl": 105, "gre": 325})


class RetrievalFallbackTest(unittest.TestCase):
    def test_text_to_sql_provider_failure_returns_empty_for_fts_route(self):
        with patch.object(sql_module, "call_llm", side_effect=RuntimeError("quota")):
            rows, sql = sql_module.sql_search("Purdue TOEFL")
        self.assertEqual(rows, [])
        self.assertIsNone(sql)

    def test_extractive_answer_contains_grounded_source(self):
        answer = _extractive_fallback_answer("q", [{
            "school_id": "purdue",
            "chunk_text": "TOEFL recommended score is 100.",
            "source_url": "https://example.edu/english",
        }])
        self.assertIn("TOEFL recommended score is 100", answer)
        self.assertIn("https://example.edu/english", answer)

    def test_legacy_rag_uses_school_scoped_fulltext_and_extractive_fallback(self):
        docs = [{
            "school_id": "purdue",
            "chunk_text": "TOEFL recommended score is 100.",
            "source_url": "https://example.edu/english",
        }]
        with (
            patch.object(rag_pipeline, "sql_search", return_value=([], None)),
            patch.object(rag_pipeline, "fulltext_search", return_value=docs) as search,
            patch.object(rag_pipeline, "generate_answer", return_value=None),
        ):
            self.assertTrue(rag_pipeline.run_rag_pipeline("Purdue TOEFL requirement"))
        search.assert_called_once_with(
            "Purdue TOEFL requirement", school_id="purdue", limit=5
        )

    def test_backend_llm_quota_error_is_short_and_only_calls_provider_once(self):
        class Completions:
            calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                raise RuntimeError("429 insufficient_quota: exceeded your current quota")

        completions = Completions()
        fake_client = type("Client", (), {
            "chat": type("Chat", (), {"completions": completions})()
        })()
        old_client = generator_client._client
        old_reason = generator_client._llm_unavailable_reason
        generator_client._client = fake_client
        generator_client._llm_unavailable_reason = None
        try:
            with self.assertRaises(generator_client.LLMUnavailableError) as first:
                generator_client.call_llm("first")
            with self.assertRaises(generator_client.LLMUnavailableError):
                generator_client.call_llm("second")
            self.assertNotIn("docs/guides", str(first.exception))
            self.assertEqual(completions.calls, 1)
        finally:
            generator_client._client = old_client
            generator_client._llm_unavailable_reason = old_reason


if __name__ == "__main__":
    unittest.main()
