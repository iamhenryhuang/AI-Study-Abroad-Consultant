import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import recommend as rec


class TestIeltsToToefl(unittest.TestCase):
    def test_known_scores(self):
        self.assertEqual(rec._ielts_to_toefl(7.0), 98)
        self.assertEqual(rec._ielts_to_toefl(8.0), 112)

    def test_out_of_range(self):
        self.assertIsNone(rec._ielts_to_toefl(3.0))
        self.assertIsNone(rec._ielts_to_toefl(None))


class TestNormalizeProfile(unittest.TestCase):
    def test_ielts_fills_toefl_when_missing(self):
        out = rec._normalize_profile({"gpa": 3.5, "ielts": 7.0})
        self.assertEqual(out["toefl"], 98)
        self.assertEqual(out["gpa"], 3.5)

    def test_explicit_toefl_wins_over_ielts(self):
        out = rec._normalize_profile({"toefl": 105, "ielts": 7.0})
        self.assertEqual(out["toefl"], 105)


class TestClassifyTier(unittest.TestCase):
    _MED = {"median_gpa": 3.8, "median_toefl": 105, "median_gre": 328}

    def test_all_above_is_safety(self):
        p = {"gpa": 3.9, "toefl": 110, "gre": 330}
        self.assertEqual(rec.classify_tier(p, self._MED), "保底")

    def test_all_below_is_reach(self):
        p = {"gpa": 3.0, "toefl": 90, "gre": 300}
        self.assertEqual(rec.classify_tier(p, self._MED), "衝刺")

    def test_mixed_is_moderate(self):
        p = {"gpa": 3.9, "toefl": 110, "gre": 300}  # 2/3 達標 → 0.67 落在適中
        self.assertEqual(rec.classify_tier(p, self._MED), "適中")

    def test_no_comparable_dim_returns_none(self):
        self.assertIsNone(rec.classify_tier({"gpa": None}, {"median_toefl": 105}))


class TestRecommend(unittest.TestCase):
    _DIST = {
        "cmu": {"school_id": "cmu", "name": "CMU", "median_gpa": 3.85, "median_toefl": 105, "median_gre": 328},
        "nyu": {"school_id": "nyu", "name": "NYU", "median_gpa": 3.75, "median_toefl": 105, "median_gre": 325},
        "ucsd": {"school_id": "ucsd", "name": "UCSD", "median_gpa": 3.93, "median_toefl": 106, "median_gre": 327},
    }

    def test_buckets_by_tier(self):
        result = rec.recommend({"gpa": 3.5, "toefl": 100, "gre": 315}, distribution=self._DIST)
        self.assertIn("衝刺", result)
        self.assertIn("適中", result)
        self.assertIn("保底", result)
        all_ids = [s["school_id"] for tier in result.values() for s in tier]
        self.assertTrue(set(all_ids) <= {"cmu", "nyu", "ucsd"})

    def test_each_tier_capped(self):
        result = rec.recommend({"gpa": 4.0, "toefl": 120, "gre": 340},
                               distribution=self._DIST, per_tier=2)
        for tier in result.values():
            self.assertLessEqual(len(tier), 2)


class _FakeCursor:
    def __init__(self, rows, capture):
        self._rows = rows
        self._capture = capture
        self.description = [("gpa",), ("decision",), ("notes",)]
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self._capture.append((sql, params))
    def fetchall(self): return self._rows


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.captured = []
        self.read_only = False
        self.closed = False
    def cursor(self): return _FakeCursor(self.rows, self.captured)
    def close(self): self.closed = True


class TestFetchNearbyCases(unittest.TestCase):
    def test_returns_rows_and_sets_readonly(self):
        conn = _FakeConn([(3.7, "accepted", "note")])
        with patch.object(rec, "get_connection", return_value=conn):
            out = rec.fetch_nearby_cases("cmu", 3.6)
        self.assertEqual(out, [{"gpa": 3.7, "decision": "accepted", "notes": "note"}])
        self.assertTrue(conn.read_only)
        self.assertTrue(conn.closed)
        sql, params = conn.captured[0]
        self.assertIn("ABS(gpa", sql)          # 有 gpa → 相近分數分支
        self.assertEqual(params[0], "cmu")

    def test_no_gpa_uses_fallback_branch(self):
        conn = _FakeConn([])
        with patch.object(rec, "get_connection", return_value=conn):
            out = rec.fetch_nearby_cases("cmu", None)
        self.assertEqual(out, [])
        sql, _ = conn.captured[0]
        self.assertNotIn("ABS(gpa", sql)       # 無 gpa → fallback 分支
        self.assertTrue(conn.closed)

    def test_no_connection_returns_empty(self):
        with patch.object(rec, "get_connection", return_value=None):
            self.assertEqual(rec.fetch_nearby_cases("cmu", 3.6), [])


if __name__ == "__main__":
    unittest.main()
