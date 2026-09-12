"""eval/generate_questions.py 的純函式測試（不連 DB）。

只測「把 DB 撈好的列 → 套模板 → 生題 → 去重 → 標型別」這段純邏輯。
DB 存取（fetch_*）刻意隔離在薄函式，不在此測。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import generate_questions as gq


class TestStructuredQuestions(unittest.TestCase):
    def _one_school_row(self, **over):
        row = {
            "school_id": "stanford", "university_name": "Stanford University",
            "gpa_min": 3.50, "toefl_ibt_min": 100, "ielts_min": 7.0,
            "gre_required": "not_accepted", "rec_letter_count": 3,
            "tuition_per_year_usd": 58000, "application_fee_usd": 125,
        }
        row.update(over)
        return row

    def test_generates_one_question_per_populated_field(self):
        qs = gq.build_structured_questions([self._one_school_row()])
        fields = {q["field"] for q in qs}
        # 7 個有值欄位各一題
        self.assertEqual(fields, {
            "gpa_min", "toefl_ibt_min", "ielts_min", "gre_required",
            "rec_letter_count", "tuition_per_year_usd", "application_fee_usd",
        })

    def test_question_carries_expected_value_from_db(self):
        qs = gq.build_structured_questions([self._one_school_row()])
        by_field = {q["field"]: q for q in qs}
        self.assertEqual(by_field["gpa_min"]["expected"], 3.50)
        self.assertEqual(by_field["toefl_ibt_min"]["expected"], 100)
        self.assertEqual(by_field["gre_required"]["expected"], "not_accepted")

    def test_all_structured_are_type_A_and_not_refusal(self):
        qs = gq.build_structured_questions([self._one_school_row()])
        for q in qs:
            self.assertEqual(q["type"], "A_structured")
            self.assertFalse(q["expect_refusal"])
            self.assertIn("stanford", q["id"])
            self.assertTrue(q["question"])           # 題目文字非空

    def test_null_fields_produce_no_question(self):
        """欄位為 None（如 purdue 無學費）→ 不生該題，避免標答是 null。"""
        row = self._one_school_row(tuition_per_year_usd=None, application_fee_usd=None)
        qs = gq.build_structured_questions([row])
        fields = {q["field"] for q in qs}
        self.assertNotIn("tuition_per_year_usd", fields)
        self.assertNotIn("application_fee_usd", fields)

    def test_duplicate_rows_deduped(self):
        """purdue 在 programs 表有重複列 → 同校同欄位只生一題。"""
        dup = self._one_school_row(school_id="purdue")
        qs = gq.build_structured_questions([dup, dup])
        ids = [q["id"] for q in qs]
        self.assertEqual(len(ids), len(set(ids)), f"有重複題 id: {ids}")

    def test_ids_are_unique_across_schools(self):
        qs = gq.build_structured_questions([
            self._one_school_row(school_id="stanford"),
            self._one_school_row(school_id="cmu"),
        ])
        ids = [q["id"] for q in qs]
        self.assertEqual(len(ids), len(set(ids)))


class TestNotCoveredQuestions(unittest.TestCase):
    def test_generates_refusal_questions(self):
        qs = gq.build_not_covered_questions(["mit", "nyu"])
        self.assertTrue(qs)
        for q in qs:
            self.assertEqual(q["type"], "E_not_covered")
            self.assertTrue(q["expect_refusal"])
            self.assertIsNone(q["expected"])
            self.assertIn(q["school_id"], ("mit", "nyu"))

    def test_not_covered_ids_unique(self):
        qs = gq.build_not_covered_questions(["mit", "nyu", "uiuc"])
        ids = [q["id"] for q in qs]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
