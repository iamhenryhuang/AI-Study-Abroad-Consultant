"""
從 db/schools_data.json 直接寫入 universities / program_requirements。
執行：python db/load_schools.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SCRIPTS = _PROJECT_ROOT / "backend" / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

from db.connection import get_connection

DATA_PATH = Path(__file__).resolve().parent / "schools_data.json"


def load() -> bool:
    schools = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    conn = get_connection()
    if not conn:
        print("請在 backend/.env 設定 DATABASE_URL。")
        return False

    try:
        with conn.cursor() as cur:
            for s in schools:
                cur.execute(
                    """
                    INSERT INTO universities (school_id, name, domain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (school_id) DO UPDATE
                        SET name = EXCLUDED.name, domain = EXCLUDED.domain
                    """,
                    (s["school_id"], s["name"], s["domain"]),
                )
                cur.execute(
                    """
                    INSERT INTO program_requirements (
                        university_id, school_id, min_gpa, toefl_min, ielts_min,
                        gre_required, gre_min_total, gre_min_quant, gre_min_verbal,
                        deadline_fall, priority_deadline,
                        tuition_per_year, funding_available, funding_note,
                        requires_sop, num_recommendation_letters, requires_resume,
                        source_url
                    )
                    VALUES (
                        (SELECT id FROM universities WHERE school_id = %s),
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (school_id) DO UPDATE SET
                        min_gpa = EXCLUDED.min_gpa,
                        toefl_min = EXCLUDED.toefl_min,
                        ielts_min = EXCLUDED.ielts_min,
                        gre_required = EXCLUDED.gre_required,
                        gre_min_total = EXCLUDED.gre_min_total,
                        gre_min_quant = EXCLUDED.gre_min_quant,
                        gre_min_verbal = EXCLUDED.gre_min_verbal,
                        deadline_fall = EXCLUDED.deadline_fall,
                        priority_deadline = EXCLUDED.priority_deadline,
                        tuition_per_year = EXCLUDED.tuition_per_year,
                        funding_available = EXCLUDED.funding_available,
                        funding_note = EXCLUDED.funding_note,
                        requires_sop = EXCLUDED.requires_sop,
                        num_recommendation_letters = EXCLUDED.num_recommendation_letters,
                        requires_resume = EXCLUDED.requires_resume,
                        source_url = EXCLUDED.source_url,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        s["school_id"], s["school_id"], s["min_gpa"], s["toefl_min"],
                        s["ielts_min"], s["gre_required"], s["gre_min_total"],
                        s["gre_min_quant"], s["gre_min_verbal"], s["deadline_fall"],
                        s["priority_deadline"],
                        s.get("tuition_per_year"), s.get("funding_available", False), s.get("funding_note"),
                        s.get("requires_sop", True), s.get("num_recommendation_letters"), s.get("requires_resume", True),
                        s["source_url"],
                    ),
                )

        conn.commit()
        print(f"已寫入 {len(schools)} 所學校的資料。")
        return True
    except Exception as e:
        conn.rollback()
        print(f"寫入失敗: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(0 if load() else 1)
