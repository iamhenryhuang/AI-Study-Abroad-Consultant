"""eval/run_eval.py 判分純函式測試（不連 DB、不打 LLM）。

只測「系統答案字串 + 題目標答 → 判對錯/判拒答」這段純邏輯。
兩系統包裝與實跑不在此測。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import run_eval as re_


def _q(field, expected):
    return {"type": "A_structured", "field": field, "expected": expected}


class TestScoreStructuredNumeric(unittest.TestCase):
    def test_gpa_exact_appears(self):
        self.assertTrue(re_.score_structured("最低 GPA 是 3.5。", _q("gpa_min", 3.5)))

    def test_gpa_trailing_zero_form(self):
        """標答 3.5，答案寫成 3.50 也算對。"""
        self.assertTrue(re_.score_structured("Minimum GPA: 3.50", _q("gpa_min", 3.5)))

    def test_gpa_wrong_number(self):
        self.assertFalse(re_.score_structured("最低 GPA 是 3.0。", _q("gpa_min", 3.5)))

    def test_toefl_integer(self):
        self.assertTrue(re_.score_structured("TOEFL iBT 最低 100 分", _q("toefl_ibt_min", 100)))

    def test_toefl_absent(self):
        self.assertFalse(re_.score_structured("查無資料", _q("toefl_ibt_min", 100)))

    def test_rec_letter_count(self):
        self.assertTrue(re_.score_structured("需要 3 封推薦信", _q("rec_letter_count", 3)))

    def test_tuition_with_thousands_separator(self):
        """學費 58000，答案寫 $58,000 也算對（忽略千分位逗號）。"""
        self.assertTrue(re_.score_structured("年學費約 $58,000 美金",
                                             _q("tuition_per_year_usd", 58000)))

    def test_number_not_matched_as_substring_of_bigger(self):
        """標答 100 不應被 '1000' 誤判命中。"""
        self.assertFalse(re_.score_structured("學費 1000000", _q("toefl_ibt_min", 100)))

    def test_number_followed_by_sentence_period(self):
        """句尾標點：'...is 100.' 的 100 應算命中（先前 regex 邊界誤擋）。"""
        self.assertTrue(re_.score_structured("The minimum TOEFL is 100.",
                                             _q("toefl_ibt_min", 100)))

    def test_integer_expected_accepts_point_zero_form(self):
        """IELTS 標答 7，答案寫 7.0 應算對（等值）。"""
        self.assertTrue(re_.score_structured("The minimum IELTS score is 7.0.",
                                             _q("ielts_min", 7)))

    def test_integer_7_not_matched_by_75(self):
        """標答 7 不應被 '7.5' 誤判命中（7 後接 .5 是更長小數）。"""
        self.assertFalse(re_.score_structured("IELTS 要 7.5 分", _q("ielts_min", 7)))

    def test_integer_expected_accepts_two_decimal_form(self):
        """GPA 標答 3（整數），答案寫 3.00 應算對（gatech 真實情況）。"""
        self.assertTrue(re_.score_structured("最低 GPA 要求為 3.00，滿分 4.0",
                                             _q("gpa_min", 3)))

    def test_rec_letters_chinese_word(self):
        """推薦信 3 封，答案寫「三封」應算對。"""
        self.assertTrue(re_.score_structured("需要三封推薦信。", _q("rec_letter_count", 3)))

    def test_rec_letters_english_word(self):
        """推薦信 3 封，答案寫 'three' 應算對。"""
        self.assertTrue(re_.score_structured("requires three recommendation letters",
                                             _q("rec_letter_count", 3)))

    def test_word_number_wrong_count(self):
        """標答 3 封，答案說『兩封』不應算對。"""
        self.assertFalse(re_.score_structured("需要兩封推薦信。", _q("rec_letter_count", 3)))


class TestScoreStructuredGre(unittest.TestCase):
    def test_gre_not_accepted_matches_not_required(self):
        self.assertTrue(re_.score_structured("GRE is not required / not accepted.",
                                             _q("gre_required", "not_accepted")))

    def test_gre_not_accepted_chinese(self):
        self.assertTrue(re_.score_structured("此項目不接受 GRE 成績。",
                                             _q("gre_required", "not_accepted")))

    def test_gre_not_accepted_but_answer_says_required_is_wrong(self):
        self.assertFalse(re_.score_structured("GRE is required.",
                                              _q("gre_required", "not_accepted")))

    def test_gre_optional(self):
        self.assertTrue(re_.score_structured("GRE 為選擇性繳交（optional）。",
                                             _q("gre_required", "optional")))


class TestScoreNotCovered(unittest.TestCase):
    def test_honest_refusal(self):
        out = re_.score_not_covered("抱歉，本系統未收錄 MIT 的資料，建議查官網。")
        self.assertTrue(out["refused"])
        self.assertFalse(out["leaked_number"])

    def test_refusal_english(self):
        out = re_.score_not_covered("I could not find any information on this school in our database.")
        self.assertTrue(out["refused"])

    def test_leaked_number_counts_as_not_refused(self):
        """未收錄校卻報出具體分數 → 幻覺，不算拒答。"""
        out = re_.score_not_covered("MIT 的最低 TOEFL 是 100 分。")
        self.assertTrue(out["leaked_number"])
        self.assertFalse(out["refused"])

    def test_gpa_shaped_number_leak(self):
        out = re_.score_not_covered("The minimum GPA for MIT is 3.5.")
        self.assertTrue(out["leaked_number"])
        self.assertFalse(out["refused"])


if __name__ == "__main__":
    unittest.main()
