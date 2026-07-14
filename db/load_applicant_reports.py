"""清洗並載入社群申請回報資料到 applicant_reports 表。

來源（皆為經驗類、非官方資料）：
  db/data/gradcafe_results.json —— GradCafe（英文，結構化）
  db/data/1point3.json          —— 一畝三分地（簡體中文，半結構化）

執行：
  python db/load_applicant_reports.py            # 需先跑過 migration 建表
  python db/load_applicant_reports.py --migrate  # 順便先套用 migration SQL

清洗策略見各 _clean_* 函式與 db/migrations/001_applicant_reports.sql 註解。
重跑安全：以 (source, source_url) 為 UNIQUE，ON CONFLICT 更新既有列。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SCRIPTS = _PROJECT_ROOT / "backend" / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

from db.connection import get_connection

_DATA_DIR = Path(__file__).resolve().parent / "data"
GRADCAFE_PATH = _DATA_DIR / "gradcafe_results.json"
ONEPOINT3_PATH = _DATA_DIR / "1point3.json"
MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / "001_applicant_reports.sql"

# 複用 agent 的學校別名表，把原始校名對應到 DB 的 school_id（mit/cmu...）
from retriever.agent.state import _SCHOOL_ALIASES


# ─── 共用清洗工具 ──────────────────────────────────────────────────────────────

# 別名用子字串比對會誤中的已知案例：校名含關鍵字但其實是別校，直接排除
#   'columbia' 會把 University of British Columbia（UBC）誤歸到 columbia
_ALIAS_FALSE_POSITIVES = [
    ("columbia", "british columbia"),   # UBC 不是 Columbia University
]


def _match_school_id(school_raw: str) -> str | None:
    """用別名表把原始校名對應到 school_id；對不到回傳 None（保留 school_raw 即可）。"""
    if not school_raw or not _SCHOOL_ALIASES:
        return None
    text = school_raw.lower()
    for sid, aliases in _SCHOOL_ALIASES.items():
        for alias in aliases:
            if alias not in text:
                continue
            # 排除已知的子字串誤中（如 UBC 誤中 columbia）
            if any(alias == bad_sid_alias and marker in text
                   for bad_sid_alias, marker in _ALIAS_FALSE_POSITIVES):
                continue
            return sid
    return None


def _parse_gpa(raw: str | None) -> float | None:
    """從原始 GPA 字串抽出「像 4.0 制」的數值（0–4.3）；抽不到回傳 None。

    涵蓋的格式：
      '3.63'                → 3.63
      '3.98||3.83'          → 取落在 0–4.3 的段（可能多段，取最後一個合理值）
      '75||3.1||20/47'      → 75 是百分制略過、20/47 是排名略過，取 3.1
      '3.69/5||3.5'         → '3.69/5' 是 5 分制略過（含 /），取 3.5
      '9.05' / '6.74'       → 超出 4.3，視為非 4.0 制 → None
    """
    if not raw:
        return None
    candidate: float | None = None
    for seg in str(raw).split("||"):
        seg = seg.strip()
        # 含 '/' 的段是排名(20/47)或非4.0制(3.69/5)，一律略過
        if "/" in seg:
            continue
        m = re.match(r"^(\d+(?:\.\d+)?)$", seg)
        if not m:
            continue
        val = float(m.group(1))
        if 0 < val <= 4.3:          # 落在 4.0 制合理範圍
            candidate = val         # 多段皆合理時取最後一段（通常較精確的 major GPA）
    return candidate


# ─── gradcafe 清洗 ────────────────────────────────────────────────────────────

_GRADCAFE_DECISION = {
    "Accepted": "accepted",
    "Rejected": "rejected",
    "Wait listed": "waitlisted",
    "Interview": "interview",
    "Other": "other",
}
_GRADCAFE_LEVEL = {
    "Masters": "masters", "PhD": "phd", "MFA": "mfa",
    "PsyD": "psyd", "EdD": "edd", "Other": "other",
}


def _clean_gradcafe(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        school_raw = (r.get("school") or "").strip()
        if not school_raw:
            continue
        ugpa = r.get("ugpa")

        # 身分別（International/American，971/995 筆有值）與 GRE 三科（~70 筆）
        # 併進 notes——這些對「錄取者背景」分析很有價值，不該在清洗時丟掉。
        extras = []
        status = (str(r.get("status") or "")).strip()
        if status:
            extras.append(f"身分:{status}")
        def _gre_part(prefix: str, val) -> str:
            s = str(val or "").strip()
            return f"{prefix}{s}" if s and s not in ("0", "0.0", "0.00") else ""

        gre = " ".join(p for p in (
            _gre_part("Q", r.get("greq")),
            _gre_part("V", r.get("grev")),
            _gre_part("W", r.get("grew")),
        ) if p)
        if gre:
            extras.append(f"GRE:{gre}")
        base_notes = (r.get("notes") or "").strip()
        notes = " | ".join(filter(None, [" ".join(extras) or None, base_notes])) or None

        out.append({
            "source":       "gradcafe",
            "source_url":   f"gradcafe:{r.get('id')}",  # 無真實 URL，用穩定 id 當去重鍵
            "school_raw":   school_raw,
            "school_id":    _match_school_id(school_raw),
            "program":      (r.get("program") or "").strip() or None,
            "degree_level": _GRADCAFE_LEVEL.get(r.get("level"), None),
            "decision":     _GRADCAFE_DECISION.get(r.get("decision"), "other"),
            "gpa":          _parse_gpa(ugpa),
            "gpa_raw":      ugpa or None,
            "season":       (r.get("season") or "").strip() or None,
            "notes":        notes,
        })
    return out


# ─── 1point3 清洗 ─────────────────────────────────────────────────────────────

_ONEPOINT3_RESULT = {
    "Offer":  "offer",
    "AD无奖": "ad_no_fund",
    "AD小奖": "ad_small_fund",
    "Reject": "rejected",
    "Waiting": "waitlisted",
}

# 論壇爬蟲雜訊：整行/片段移除
_NOISE_PATTERNS = [
    re.compile(r"[.．・]*\s*(?:Waral|check 1point3acres|1point3acres|acres)\b.*", re.IGNORECASE),
    re.compile(r"\.\s*[Χχ█]+"),
    re.compile(r"\bWaral\s+d[иeи]+"),
]


def _clean_content(text: str | None) -> str | None:
    """移除 1point3 content 的論壇爬蟲雜訊，壓縮空白。空內容回 None。"""
    if not text:
        return None
    for pat in _NOISE_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"[?？]{2,}", "", text)     # 亂碼 '??'
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _clean_1point3(data: dict) -> list[dict]:
    out = []
    for school_key, posts in data.items():
        for p in posts:
            url = (p.get("url") or "").strip()
            school_raw = (p.get("school") or school_key or "").strip()
            if not school_raw:
                continue
            gpa_raw = p.get("gpa") or None
            # notes：標題 + 清洗後短內文（多數貼文內文極短，合併成一段可讀摘要）
            title = (p.get("title") or "").strip()
            content = _clean_content(p.get("content"))
            extras = []
            if (p.get("major") or "").strip():
                extras.append(f"專業:{p['major'].strip()}")
            if (p.get("undergrad_school") or "").strip():
                extras.append(f"本科:{p['undergrad_school'].strip()}")
            if (p.get("toefl") or "").strip():
                extras.append(f"TOEFL:{p['toefl'].strip()}")
            if (p.get("gre") or "").strip():
                extras.append(f"GRE:{p['gre'].strip()}")
            notes = " | ".join(filter(None, [title, " ".join(extras) or None, content])) or None

            out.append({
                "source":       "1point3",
                "source_url":   url or f"1point3:{school_key}:{title[:40]}",
                "school_raw":   school_raw,
                "school_id":    _match_school_id(school_raw),
                "program":      (p.get("major") or "").strip() or None,
                "degree_level": None,   # 論壇不區分 masters/phd，留空
                "decision":     _ONEPOINT3_RESULT.get(p.get("result"), "other"),
                "gpa":          _parse_gpa(gpa_raw),
                "gpa_raw":      gpa_raw,
                "season":       None,
                "notes":        notes,
            })
    return out


# ─── 載入 ─────────────────────────────────────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO applicant_reports
        (source, source_url, school_raw, school_id, program, degree_level,
         decision, gpa, gpa_raw, season, notes)
    VALUES
        (%(source)s, %(source_url)s, %(school_raw)s, %(school_id)s, %(program)s,
         %(degree_level)s, %(decision)s, %(gpa)s, %(gpa_raw)s, %(season)s, %(notes)s)
    ON CONFLICT (source, source_url) DO UPDATE SET
        school_raw = EXCLUDED.school_raw, school_id = EXCLUDED.school_id,
        program = EXCLUDED.program, degree_level = EXCLUDED.degree_level,
        decision = EXCLUDED.decision, gpa = EXCLUDED.gpa, gpa_raw = EXCLUDED.gpa_raw,
        season = EXCLUDED.season, notes = EXCLUDED.notes
"""


def load(apply_migration: bool = False) -> bool:
    conn = get_connection()
    if not conn:
        print("請在 backend/.env 設定 DATABASE_URL。")
        return False

    try:
        with conn.cursor() as cur:
            if apply_migration:
                if not MIGRATION_PATH.is_file():
                    print(f"找不到 migration：{MIGRATION_PATH}")
                    return False
                cur.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
                print("已套用 migration 001_applicant_reports.sql")

            records: list[dict] = []
            if GRADCAFE_PATH.is_file():
                records += _clean_gradcafe(json.loads(GRADCAFE_PATH.read_text(encoding="utf-8")))
            if ONEPOINT3_PATH.is_file():
                records += _clean_1point3(json.loads(ONEPOINT3_PATH.read_text(encoding="utf-8")))

            if not records:
                print("找不到任何來源資料（db/data/ 下的 json）。")
                return False

            for rec in records:
                cur.execute(_INSERT_SQL, rec)

        conn.commit()
        matched = sum(1 for r in records if r["school_id"])
        with_gpa = sum(1 for r in records if r["gpa"] is not None)
        print(f"已載入 {len(records)} 筆申請回報"
              f"（對應到已知 school_id：{matched}，解析出 GPA：{with_gpa}）。")
        return True
    except Exception as e:
        conn.rollback()
        print(f"載入失敗: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    apply_mig = "--migrate" in sys.argv
    sys.exit(0 if load(apply_migration=apply_mig) else 1)
