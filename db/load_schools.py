"""
從 db/data/schools_data.json 寫入 universities / programs / program_deadlines /
program_scholarships / program_app_materials。

JSON 結構與欄位名稱對應 db/init_db.sql（與 data_crawler 寫入的 schema 一致）：
  university → programs[]（每個 program 內含 deadlines[] / scholarships[] / app_materials[]）

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

DATA_PATH = Path(__file__).resolve().parent / "data" / "schools_data.json"

_PROGRAM_FIELDS = [
    "degree_type", "program_name", "department",
    "toefl_min", "toefl_ibt_min", "ielts_min", "duolingo_min", "language_waiver",
    "gre_required", "gre_quant_min", "gre_verbal_min", "gre_awa_min",
    "gpa_min", "gpa_scale", "gpa_note",
    "transcript_copies", "transcript_format", "rec_letter_count",
    "sop_word_limit", "sop_prompt", "cv_required", "writing_sample_required",
    "application_fee_usd", "fee_waiver_available", "fee_waiver_criteria",
    "tuition_per_year_usd", "tuition_note",
    "application_url", "application_system",
]


def load() -> bool:
    schools = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    conn = get_connection()
    if not conn:
        print("請在 backend/.env 設定 DATABASE_URL。")
        return False

    try:
        with conn.cursor() as cur:
            for school in schools:
                u = school["university"]

                # 1) universities
                cur.execute(
                    """
                    INSERT INTO universities (school_id, name, domain)
                    VALUES (%(school_id)s, %(name)s, %(domain)s)
                    ON CONFLICT (school_id) DO UPDATE
                        SET name = EXCLUDED.name, domain = EXCLUDED.domain
                    RETURNING id
                    """,
                    u,
                )
                university_id = cur.fetchone()[0]

                for p in school["programs"]:
                    fields = {k: p.get(k) for k in _PROGRAM_FIELDS}
                    source_urls = p.get("source_urls", [])

                    # 2) programs
                    cur.execute(
                        f"""
                        INSERT INTO programs (
                            university_id, program_code, {', '.join(fields)}, source_urls
                        )
                        VALUES (
                            %(university_id)s, %(program_code)s,
                            {', '.join(f'%({k})s' for k in fields)},
                            %(source_urls)s
                        )
                        ON CONFLICT (university_id, program_code) DO UPDATE SET
                            {', '.join(f'{k} = EXCLUDED.{k}' for k in fields)},
                            source_urls       = EXCLUDED.source_urls,
                            last_extracted_at = CURRENT_TIMESTAMP
                        RETURNING id
                        """,
                        {**fields, "university_id": university_id,
                         "program_code": p["program_code"], "source_urls": source_urls},
                    )
                    program_id = cur.fetchone()[0]

                    # 3) program_deadlines（重跑先清空該 program 的舊資料再寫入）
                    cur.execute("DELETE FROM program_deadlines WHERE program_id = %s", (program_id,))
                    for d in p.get("deadlines", []):
                        cur.execute(
                            """
                            INSERT INTO program_deadlines (program_id, deadline_type, deadline_date, semester, note)
                            VALUES (%(program_id)s, %(deadline_type)s, %(deadline_date)s, %(semester)s, %(note)s)
                            """,
                            {"deadline_type": None, "semester": None, "note": None, **d, "program_id": program_id},
                        )

                    # 4) program_scholarships
                    cur.execute("DELETE FROM program_scholarships WHERE program_id = %s", (program_id,))
                    for s in p.get("scholarships", []):
                        cur.execute(
                            """
                            INSERT INTO program_scholarships
                                (program_id, name, amount_usd, coverage, eligibility, auto_consider, source_url)
                            VALUES
                                (%(program_id)s, %(name)s, %(amount_usd)s, %(coverage)s,
                                 %(eligibility)s, %(auto_consider)s, %(source_url)s)
                            """,
                            {"amount_usd": None, "coverage": None, "source_url": None, **s, "program_id": program_id},
                        )

                    # 5) program_app_materials
                    cur.execute("DELETE FROM program_app_materials WHERE program_id = %s", (program_id,))
                    for m in p.get("app_materials", []):
                        cur.execute(
                            """
                            INSERT INTO program_app_materials
                                (program_id, material_type, requirement, word_limit, note)
                            VALUES
                                (%(program_id)s, %(material_type)s, %(requirement)s, %(word_limit)s, %(note)s)
                            """,
                            {"word_limit": None, "note": None, **m, "program_id": program_id},
                        )

        conn.commit()
        print(f"已寫入 {len(schools)} 所學校的資料（programs + 子表）。")
        return True
    except Exception as e:
        conn.rollback()
        print(f"寫入失敗: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(0 if load() else 1)
