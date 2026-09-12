"""掃 DB 自動生 A（結構化查詢）/ E（未收錄學校，考誠實拒答）評估題庫。

設計（見專案 brainstorming 討論）：
  - 純模板套字，零 LLM 成本、可重現、題目中立。
  - A 類：對收錄的每校每個「有值」的結構化欄位各生一題，expected 直接取 DB 欄位值 → 可自動判分。
  - E 類：取「applicant_reports 有回報、但官方資料未收錄」的真學校，考系統會不會把經驗談誤當官方收錄。
  - 純函式（build_*）與 DB 存取（fetch_*）分離，前者可單元測試、後者實跑時才連 DB。
  - 不生 deadline 題（各校多筆、語意有歧義，留給之後人工標的 B 類）。

用法：
    python eval/generate_questions.py               # 生題並寫入 eval/questions.json
    python eval/generate_questions.py --out x.json  # 自訂輸出路徑
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_SCRIPTS = Path(__file__).resolve().parent.parent / "backend" / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

_OUT_DEFAULT = Path(__file__).resolve().parent / "questions.json"

# 收錄的 5 校（有官方結構化資料）
COVERED_SCHOOLS = {"cmu", "gatech", "purdue", "stanford", "ucla"}

# E 類取幾個未收錄學校（依 applicant_reports 回報數，取常見留學熱門校）
# 20 校 × 3 題 = 60 題：有效樣本從 8 校擴到 20 校，讓「誠實拒答 vs 幻覺」的結論更穩健。
NOT_COVERED_LIMIT = 20

# A 類：欄位 → (英文題目模板, 人類可讀標籤)。只對「該欄位有值」的校生題。
_STRUCTURED_FIELDS: dict[str, str] = {
    "gpa_min": "What is the minimum GPA requirement for {school}'s CS master's program?",
    "toefl_ibt_min": "What is the minimum TOEFL iBT score for {school}'s CS master's program?",
    "ielts_min": "What is the minimum IELTS score for {school}'s CS master's program?",
    "gre_required": "Is GRE required for {school}'s CS master's program?",
    "rec_letter_count": "How many recommendation letters does {school}'s CS master's program require?",
    "tuition_per_year_usd": "What is the annual tuition (USD) for {school}'s CS master's program?",
    "application_fee_usd": "What is the application fee (USD) for {school}'s CS master's program?",
}

# E 類：未收錄學校套的模板（每校生這幾題）
_NOT_COVERED_TEMPLATES = {
    "toefl": "What is the minimum TOEFL score for {school}'s CS master's program?",
    "gpa": "What is the minimum GPA for {school}'s CS master's program?",
    "deadline": "What is the application deadline for {school}'s CS master's program?",
}


# ─── 純函式：套模板生題（可單元測試） ─────────────────────────────────────────

def build_structured_questions(rows: list[dict]) -> list[dict]:
    """吃 program 列（每列含 school_id + 各結構化欄位），對有值欄位各生一題。

    自動去重：同 (school_id, field) 只留一題——programs 表可能有重複列（如 purdue）。
    """
    seen: set[tuple[str, str]] = set()
    questions: list[dict] = []
    for row in rows:
        school_id = row.get("school_id")
        if not school_id:
            continue
        school_name = row.get("university_name") or school_id.upper()
        for field, template in _STRUCTURED_FIELDS.items():
            value = row.get(field)
            if value is None:                       # 該校此欄位無值 → 不生題（避免 null 標答）
                continue
            key = (school_id, field)
            if key in seen:                          # 去重
                continue
            seen.add(key)
            questions.append({
                "id": f"A-{school_id}-{field}",
                "type": "A_structured",
                "question": template.format(school=school_name),
                "school_id": school_id,
                "field": field,
                "expected": _normalize(value),
                "expect_refusal": False,
            })
    return questions


def build_not_covered_questions(school_ids: list[str]) -> list[dict]:
    """對未收錄學校生 E 類題：標答是「應誠實告知未收錄」（expected=None, expect_refusal=True）。"""
    questions: list[dict] = []
    for school_id in school_ids:
        for tag, template in _NOT_COVERED_TEMPLATES.items():
            questions.append({
                "id": f"E-{school_id}-{tag}",
                "type": "E_not_covered",
                "question": template.format(school=school_id.upper()),
                "school_id": school_id,
                "field": None,
                "expected": None,
                "expect_refusal": True,
            })
    return questions


def _normalize(value):
    """把 DB 型別（Decimal 等）轉成 JSON 可序列化的原生型別。"""
    from decimal import Decimal
    if isinstance(value, Decimal):
        # 整數值的 Decimal 轉 int，其餘轉 float，避免 "3.50" 這種字串化差異
        return int(value) if value == value.to_integral_value() else float(value)
    return value


# ─── DB 存取：實跑時才連（不在單元測試裡跑） ──────────────────────────────────

def fetch_program_rows() -> list[dict]:
    """撈收錄各校的 program 結構化欄位（DISTINCT 去掉 programs 表的重複列）。"""
    from db.connection import get_connection
    conn = get_connection()
    if not conn:
        raise RuntimeError("無法連線資料庫，請確認 db 容器已啟動且 .env 的 DATABASE_URL 正確")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT
                    u.school_id, u.name AS university_name,
                    p.gpa_min, p.toefl_ibt_min, p.ielts_min, p.gre_required,
                    p.rec_letter_count, p.tuition_per_year_usd, p.application_fee_usd
                FROM programs p
                JOIN universities u ON p.university_id = u.id
                ORDER BY u.school_id
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_not_covered_schools(limit: int = NOT_COVERED_LIMIT) -> list[str]:
    """從 applicant_reports 取「有回報、但不在收錄 5 校」的 school_id，依回報數排序取前 limit 個。"""
    from db.connection import get_connection
    conn = get_connection()
    if not conn:
        raise RuntimeError("無法連線資料庫，請確認 db 容器已啟動且 .env 的 DATABASE_URL 正確")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT school_id
                FROM applicant_reports
                WHERE school_id IS NOT NULL
                GROUP BY school_id
                ORDER BY count(*) DESC
                """
            )
            all_ids = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    not_covered = [s for s in all_ids if s not in COVERED_SCHOOLS]
    return not_covered[:limit]


def generate(out_path: Path = _OUT_DEFAULT) -> dict:
    """實跑：連 DB 生 A/E 題庫，寫 JSON，回傳統計摘要。"""
    program_rows = fetch_program_rows()
    not_covered = fetch_not_covered_schools()

    a_questions = build_structured_questions(program_rows)
    e_questions = build_not_covered_questions(not_covered)
    all_questions = a_questions + e_questions

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(all_questions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "total": len(all_questions),
        "A_structured": len(a_questions),
        "E_not_covered": len(e_questions),
        "not_covered_schools": not_covered,
        "out_path": str(out_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生 A/E 評估題庫")
    parser.add_argument("--out", default=str(_OUT_DEFAULT), help="輸出 JSON 路徑")
    args = parser.parse_args()

    summary = generate(Path(args.out))
    print(f"已寫入 {summary['out_path']}")
    print(f"總題數：{summary['total']}"
          f"（A 結構化 {summary['A_structured']} / E 未收錄 {summary['E_not_covered']}）")
    print(f"E 類未收錄學校：{', '.join(summary['not_covered_schools'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
