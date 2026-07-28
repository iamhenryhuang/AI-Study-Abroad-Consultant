import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import applicant_search as module


@contextmanager
def _connection():
    yield object()


class ApplicantSearchCombinedSourcesTest(unittest.TestCase):
    def test_user_submission_is_merged_before_external_reports(self):
        user_row = {
            "id": 7,
            "graduate_school": "National Chengchi University",
            "country": "Taiwan",
            "apply_school": "Stanford University",
            "apply_program": "MS in Computer Science",
            "gpa": 3.6,
            "class_rank": 15,
            "class_size": 60,
            "experience": [{"item": "TOEFL", "result": "110"}],
            "review": "Internship experience helped.",
        }
        external_row = {
            "source": "gradcafe",
            "source_url": "https://example.test/report",
            "school_id": "stanford",
            "school_raw": "Stanford University",
            "program": "Computer Science",
            "degree_level": "MS",
            "decision": "Accepted",
            "gpa": 3.8,
            "gpa_raw": None,
            "season": "Fall 2026",
            "notes": "External report",
        }

        def fake_fetch(_conn, sql, params=None):
            if "FROM user_experiences" in sql:
                self.assertIn("%stanford university%", params["school_patterns"])
                return [user_row]
            if "FROM applicant_reports" in sql:
                self.assertEqual(params["limit"], 2)
                return [external_row]
            self.fail(f"unexpected SQL: {sql}")

        with patch.object(module, "readonly_connection", _connection), \
             patch.object(module, "fetch_dicts", side_effect=fake_fetch):
            docs = module.applicant_search(
                "Stanford 申請經驗", school_id="stanford", limit=3
            )

        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["source"], "user_submission")
        self.assertEqual(docs[0]["school_id"], "stanford")
        self.assertIn("本站使用者分享", docs[0]["chunk_text"])
        self.assertIn("TOEFL：110", docs[0]["chunk_text"])
        self.assertEqual(docs[1]["source"], "gradcafe")

    def test_user_query_failure_does_not_hide_external_reports(self):
        external_row = {
            "source": "1point3",
            "source_url": "https://example.test/thread",
            "school_id": "purdue",
            "school_raw": "Purdue",
            "program": "CS",
            "degree_level": "MS",
            "decision": "Accepted",
            "gpa": 3.7,
            "gpa_raw": None,
            "season": None,
            "notes": "Report remains available",
        }

        def fake_fetch(_conn, sql, _params=None):
            if "FROM user_experiences" in sql:
                raise RuntimeError("table unavailable")
            return [external_row]

        with patch.object(module, "readonly_connection", _connection), \
             patch.object(module, "fetch_dicts", side_effect=fake_fetch):
            docs = module.applicant_search("Purdue 錄取案例", "purdue", 4)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["source"], "1point3")

    def test_explicit_user_submission_query_skips_external_reports(self):
        user_row = {
            "id": 9,
            "graduate_school": "NCCU",
            "country": "Taiwan",
            "apply_school": "Stanford University",
            "apply_program": "MSCS",
            "gpa": 3.6,
            "class_rank": None,
            "class_size": None,
            "experience": [],
            "review": "Shared review",
        }

        def fake_fetch(_conn, sql, _params=None):
            if "FROM applicant_reports" in sql:
                self.fail("explicit user-submission query must not search external reports")
            return [user_row]

        with patch.object(module, "readonly_connection", _connection), \
             patch.object(module, "fetch_dicts", side_effect=fake_fetch):
            docs = module.applicant_search(
                "Stanford 有哪些使用者分享經驗？", "stanford", 5
            )

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["source"], "user_submission")


if __name__ == "__main__":
    unittest.main()
