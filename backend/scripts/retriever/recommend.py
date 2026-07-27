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
