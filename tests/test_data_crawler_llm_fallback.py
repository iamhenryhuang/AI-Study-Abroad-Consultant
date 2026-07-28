import unittest
from unittest.mock import patch

from data_crawler import llm, nodes_page


class _FailingCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        raise RuntimeError(
            '429 insufficient_quota: You exceeded your current quota; '
            'check your plan and billing details'
        )


class _FakeClient:
    def __init__(self):
        self.chat = type('Chat', (), {})()
        self.chat.completions = _FailingCompletions()


class LlmCircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        self.old_reason = llm._unavailable_reason
        llm._unavailable_reason = None

    def tearDown(self):
        llm._unavailable_reason = self.old_reason

    def test_permanent_quota_error_fails_fast_for_following_calls(self):
        client = _FakeClient()
        with patch.object(llm, 'get_openai_client', return_value=client):
            with self.assertRaises(llm.LLMUnavailableError):
                llm.call_llm_json('first')
            with self.assertRaises(llm.LLMUnavailableError):
                llm.call_llm_json('second')
        self.assertEqual(client.chat.completions.calls, 1)


class PageFallbackTest(unittest.TestCase):
    def test_purdue_application_page_has_deterministic_fallback(self):
        result = nodes_page._common_requirements_override(
            'https://www.cs.purdue.edu/graduate/admission/requirements.html',
            'Graduate application requirements include TOEFL for international applicants.',
        )
        self.assertEqual(result[0], 'admissions')

    def test_unavailable_provider_skips_semantic_repair(self):
        with patch.object(nodes_page, 'llm_is_unavailable', return_value=True):
            self.assertEqual(
                nodes_page.extraction_quality_check({}),
                'finalize_page',
            )


if __name__ == '__main__':
    unittest.main()
