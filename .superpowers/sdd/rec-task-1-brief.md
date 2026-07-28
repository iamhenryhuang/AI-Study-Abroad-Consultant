# Task 1 Brief — recommend.py 推薦核心模組

「上傳成績→推薦學校」功能的第一個 task：一個新模組 + 測試，自成一體。以下是你的需求規格，程式碼請照抄。

## Global Constraints
- 資料源 `crawler/data/1point3_distribution.json`：只有 GPA/GRE/TOEFL 中位數，無 IELTS。
- 分級邏輯為純函式（distribution 可注入），不碰 DB；DB 案例查詢獨立成 `fetch_nearby_cases`。
- 測試：unittest，`python -m unittest discover tests -p "test_recommend.py" -v`。測試檔開頭需 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))`。
- 環境：Windows。Bash 的 git 不可用時改用 PowerShell。只新增這兩個檔案，不動別的。

## Files
- Create: `backend/scripts/retriever/recommend.py`
- Test: `tests/test_recommend.py`

## Interface（後續 task 依賴）
- `_ielts_to_toefl(ielts) -> int | None`
- `_normalize_profile(profile: dict) -> dict`（回 {gpa,toefl,gre}；無 toefl 有 ielts 時換算補上）
- `classify_tier(profile: dict, medians: dict) -> str | None`（"衝刺"/"適中"/"保底"/None）
- `recommend(profile, distribution=None, per_tier=3) -> dict`（回 {"衝刺":[...],"適中":[...],"保底":[...]}）
- `fetch_nearby_cases(school_id, gpa, limit=3) -> list[dict]`

## Step 1: 寫失敗測試 `tests/test_recommend.py`

```python
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


if __name__ == "__main__":
    unittest.main()
```

## Step 2: 確認測試失敗
Run: `python -m unittest discover tests -p "test_recommend.py" -v`
Expected: FAIL/ERROR `ModuleNotFoundError: No module named 'retriever.recommend'`

## Step 3: 寫實作 `backend/scripts/retriever/recommend.py`

```python
"""上傳成績 → 推薦學校：依 1point3 錄取中位數把各校分成衝刺/適中/保底，
並從 applicant_reports 撈相近分數的真實錄取案例佐證。

分級邏輯為純函式（distribution 可注入）；DB 案例查詢獨立成 fetch_nearby_cases。
參考 backup/AI-Study-Abroad-Consultant-1.0.0 的舊推薦邏輯。
"""
from __future__ import annotations

import json
from pathlib import Path

from db.connection import get_connection

_DIST_PATH = Path(__file__).resolve().parents[3] / "crawler" / "data" / "1point3_distribution.json"

# ETS IELTS→TOEFL concordance 代表值
_IELTS_TOEFL = {9.0: 119, 8.5: 116, 8.0: 112, 7.5: 105, 7.0: 98, 6.5: 86, 6.0: 69}


def _ielts_to_toefl(ielts) -> int | None:
    if ielts is None:
        return None
    try:
        key = round(float(ielts) * 2) / 2      # 對齊到 0.5 級距
    except (TypeError, ValueError):
        return None
    return _IELTS_TOEFL.get(key)


def _normalize_profile(profile: dict) -> dict:
    """回傳 {gpa, toefl, gre}；無 toefl 但有 ielts 時換算補上。"""
    gpa   = profile.get("gpa")
    toefl = profile.get("toefl")
    gre   = profile.get("gre")
    if toefl is None and profile.get("ielts") is not None:
        toefl = _ielts_to_toefl(profile.get("ielts"))
    return {"gpa": gpa, "toefl": toefl, "gre": gre}


def classify_tier(profile: dict, medians: dict) -> str | None:
    """依達標比例分級：普遍達標→保底、約半→適中、普遍未達→衝刺；無可比維度→None。"""
    dims = []
    for key in ("gpa", "toefl", "gre"):
        u, m = profile.get(key), medians.get(f"median_{key}")
        if u is not None and m is not None:
            dims.append((u, m))
    if not dims:
        return None
    meets = sum(1 for u, m in dims if u >= m)
    ratio = meets / len(dims)
    if ratio >= 0.7:
        return "保底"
    if ratio >= 0.35:
        return "適中"
    return "衝刺"


def _load_distribution() -> dict:
    """讀 distribution json → {school_id: {school_id, name, median_gpa, median_toefl, median_gre}}。"""
    try:
        raw = json.loads(_DIST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Recommend] 讀取 distribution 失敗：{e}")
        return {}
    out = {}
    for name, info in raw.get("university_admissions_data", {}).items():
        sid = info.get("school_id")
        if not sid:
            continue
        out[sid] = {
            "school_id":     sid,
            "name":          name,
            "median_gpa":    info.get("median_gpa"),
            "median_toefl":  info.get("median_toefl"),
            "median_gre":    info.get("median_gre"),
        }
    return out


def _comparison(profile: dict, med: dict) -> list[str]:
    """產生「你的分數 vs 中位數」對比字串列表。"""
    parts = []
    for key, label in (("gpa", "GPA"), ("toefl", "TOEFL"), ("gre", "GRE")):
        u, m = profile.get(key), med.get(f"median_{key}")
        if u is not None and m is not None:
            parts.append(f"{label} 你 {u} / 中位 {m}")
    return parts


def recommend(profile: dict, distribution: dict | None = None, per_tier: int = 3) -> dict:
    """把各校分級，每檔取至多 per_tier 所。distribution 可注入供測試。"""
    norm = _normalize_profile(profile)
    dist = distribution if distribution is not None else _load_distribution()
    tiers: dict[str, list] = {"衝刺": [], "適中": [], "保底": []}
    for sid, med in dist.items():
        tier = classify_tier(norm, med)
        if tier is None:
            continue
        tiers[tier].append({
            "school_id":  sid,
            "name":       med.get("name", sid.upper()),
            "medians":    med,
            "comparison": _comparison(norm, med),
        })
    for tier in tiers:
        tiers[tier] = tiers[tier][:per_tier]
    return tiers


def fetch_nearby_cases(school_id: str, gpa, limit: int = 3) -> list[dict]:
    """查 applicant_reports 中該校、GPA 相近的錄取案例（±0.2）。失敗回 []。"""
    conn = get_connection()
    if not conn:
        return []
    try:
        conn.read_only = True
        with conn.cursor() as cur:
            if gpa is not None:
                cur.execute(
                    "SELECT gpa, decision, notes FROM applicant_reports "
                    "WHERE school_id=%s AND decision IN ('accepted','offer','ad_no_fund','ad_small_fund') "
                    "AND gpa IS NOT NULL AND ABS(gpa - %s) <= 0.2 "
                    "ORDER BY ABS(gpa - %s) LIMIT %s",
                    (school_id, gpa, gpa, limit),
                )
            else:
                cur.execute(
                    "SELECT gpa, decision, notes FROM applicant_reports "
                    "WHERE school_id=%s AND decision IN ('accepted','offer','ad_no_fund','ad_small_fund') "
                    "ORDER BY id DESC LIMIT %s",
                    (school_id, limit),
                )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        print(f"[Recommend] 案例查詢失敗（{school_id}）：{e}")
        return []
    finally:
        conn.close()
    return rows
```

## Step 4: 確認測試通過
Run: `python -m unittest discover tests -p "test_recommend.py" -v`
Expected: PASS（10 tests OK）

## Step 5: Commit
```bash
git add backend/scripts/retriever/recommend.py tests/test_recommend.py
git commit -m "feat: add school recommendation tiering module"
```
（Bash git 不可用時改用 PowerShell。）
