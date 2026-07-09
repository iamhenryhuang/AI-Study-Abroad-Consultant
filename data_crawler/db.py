"""DB 存取層。

- 連線方式沿用 backend/scripts/db/connection.py 的慣例（.env 的 DATABASE_URL + psycopg3）
- migration（冪等）：新增 review_queue 表；替 program_deadlines /
  program_scholarships / program_app_materials 補 updated_at 欄位
- 寫入策略（v4 第三節 3-1）：三張子表用「查詢後決定 INSERT/UPDATE」，
  不改 UNIQUE 約束、不用 ON CONFLICT
"""
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / "backend" / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

MIGRATION_SQL = """
-- 加法式 migration：只建缺少的表、補缺欄位，不 DROP 任何既有資料
-- （programs 家族表定義同 db/init_db.sql；既有 DB 若還是舊 schema 也能直接升級）
CREATE TABLE IF NOT EXISTS programs (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    degree_type   VARCHAR(20),
    program_code  VARCHAR(50) NOT NULL,
    program_name  VARCHAR(200),
    department    VARCHAR(100),
    toefl_min          INTEGER,
    toefl_ibt_min      INTEGER,
    ielts_min          NUMERIC(3,1),
    duolingo_min       INTEGER,
    language_waiver    TEXT,
    gre_required       VARCHAR(20),
    gre_quant_min      INTEGER,
    gre_verbal_min     INTEGER,
    gre_awa_min        NUMERIC(2,1),
    gpa_min            NUMERIC(3,2),
    gpa_scale          VARCHAR(10),
    gpa_note           TEXT,
    transcript_copies        INTEGER,
    transcript_format        VARCHAR(50),
    rec_letter_count         INTEGER,
    sop_word_limit           INTEGER,
    sop_prompt               TEXT,
    cv_required              BOOLEAN,
    writing_sample_required  BOOLEAN,
    application_fee_usd      INTEGER,
    fee_waiver_available     BOOLEAN,
    fee_waiver_criteria      TEXT,
    tuition_per_year_usd  INTEGER,
    tuition_note          TEXT,
    application_url       TEXT,
    application_system    VARCHAR(50),
    source_urls           TEXT[],
    extraction_confidence NUMERIC(3,2),
    last_extracted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (university_id, program_code)
);
CREATE INDEX IF NOT EXISTS idx_programs_university ON programs(university_id);
CREATE INDEX IF NOT EXISTS idx_programs_degree     ON programs(degree_type);

CREATE TABLE IF NOT EXISTS program_deadlines (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    deadline_type  VARCHAR(30),
    deadline_date  DATE,
    semester       VARCHAR(20),
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_deadlines_program ON program_deadlines(program_id);

CREATE TABLE IF NOT EXISTS program_scholarships (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    name           VARCHAR(200),
    amount_usd     INTEGER,
    coverage       VARCHAR(100),
    eligibility    TEXT,
    auto_consider  BOOLEAN,
    source_url     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scholarships_program ON program_scholarships(program_id);

CREATE TABLE IF NOT EXISTS program_app_materials (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    material_type  VARCHAR(50),
    requirement    TEXT,
    word_limit     INTEGER,
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_materials_program ON program_app_materials(program_id);

ALTER TABLE web_pages       ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL;
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS review_queue (
    id           SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id      INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    program_code VARCHAR(50),
    field_name   VARCHAR(100),
    field_value  TEXT,
    reason       VARCHAR(50),
    source_excerpt TEXT,
    status       VARCHAR(20) DEFAULT 'pending',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE program_deadlines     ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
ALTER TABLE program_scholarships  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
ALTER TABLE program_app_materials ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
"""

# programs 表中允許由 LLM 抽取寫入的欄位（對應 init_db.sql）
PROGRAM_FIELDS = [
    "toefl_min", "toefl_ibt_min", "ielts_min", "duolingo_min", "language_waiver",
    "gre_required", "gre_quant_min", "gre_verbal_min", "gre_awa_min",
    "gpa_min", "gpa_scale", "gpa_note",
    "transcript_copies", "transcript_format", "rec_letter_count",
    "sop_word_limit", "sop_prompt", "cv_required", "writing_sample_required",
    "application_fee_usd", "fee_waiver_available", "fee_waiver_criteria",
    "tuition_per_year_usd", "tuition_note",
    "application_url", "application_system",
]


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("未在 .env 設定 DATABASE_URL")
    return psycopg.connect(DATABASE_URL)


def ensure_migrations(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
    conn.commit()


def get_or_create_university(conn, school_id: str, name: str | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM universities WHERE school_id = %s", (school_id,))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO universities (school_id, name) VALUES (%s, %s) RETURNING id",
            (school_id, name or school_id),
        )
        uid = cur.fetchone()[0]
    conn.commit()
    return uid


def school_recently_extracted(conn, university_id: int, hours: int) -> bool:
    """SKIP_RECENT：改查 DB 的 last_extracted_at（而非檔案 mtime）。"""
    if hours <= 0:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM programs
               WHERE university_id = %s
                 AND last_extracted_at > NOW() - make_interval(hours => %s)
               LIMIT 1""",
            (university_id, hours),
        )
        return cur.fetchone() is not None


def list_known_program_codes(conn, university_id: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT program_code FROM programs WHERE university_id = %s", (university_id,))
        return [r[0] for r in cur.fetchall()]


def upsert_web_page(conn, university_id: int, url: str, passed_types: list,
                    raw_text: str, program_id: int | None = None) -> int:
    """web_pages.url 有 UNIQUE 約束，可直接 ON CONFLICT。"""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO web_pages (university_id, program_id, url, passed_types, raw_text, char_count)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (url) DO UPDATE SET
                   university_id = EXCLUDED.university_id,
                   program_id    = COALESCE(EXCLUDED.program_id, web_pages.program_id),
                   passed_types  = EXCLUDED.passed_types,
                   raw_text      = EXCLUDED.raw_text,
                   char_count    = EXCLUDED.char_count
               RETURNING id""",
            (university_id, program_id, url, json.dumps(passed_types), raw_text, len(raw_text or "")),
        )
        page_id = cur.fetchone()[0]
    conn.commit()
    return page_id


def upsert_program(conn, university_id: int, program_code: str,
                   meta: dict, fields: dict, source_url: str,
                   confidence: float | None) -> int:
    """programs 有 UNIQUE(university_id, program_code)；欄位採「非 null 才覆蓋」。"""
    set_fields = {k: v for k, v in fields.items() if k in PROGRAM_FIELDS and v is not None}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, source_urls FROM programs WHERE university_id = %s AND program_code = %s",
            (university_id, program_code),
        )
        row = cur.fetchone()

        if row:
            program_id, source_urls = row
            urls = list(source_urls or [])
            if source_url and source_url not in urls:
                urls.append(source_url)
            assignments = ", ".join(f"{k} = %s" for k in set_fields)
            params = list(set_fields.values())
            sql = f"""UPDATE programs SET
                          {assignments + ',' if assignments else ''}
                          degree_type  = COALESCE(%s, degree_type),
                          program_name = COALESCE(%s, program_name),
                          department   = COALESCE(%s, department),
                          source_urls  = %s,
                          extraction_confidence = %s,
                          last_extracted_at = CURRENT_TIMESTAMP
                      WHERE id = %s"""
            params += [meta.get("degree_type"), meta.get("program_name"),
                       meta.get("department"), urls, confidence, program_id]
            cur.execute(sql, params)
        else:
            cols = ["university_id", "program_code", "degree_type", "program_name",
                    "department", "source_urls", "extraction_confidence"] + list(set_fields)
            vals = [university_id, program_code, meta.get("degree_type"),
                    meta.get("program_name"), meta.get("department"),
                    [source_url] if source_url else [], confidence] + list(set_fields.values())
            placeholders = ", ".join(["%s"] * len(vals))
            cur.execute(
                f"INSERT INTO programs ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            program_id = cur.fetchone()[0]
    conn.commit()
    return program_id


def upsert_deadline(conn, program_id: int, item: dict) -> None:
    """查 (program_id, deadline_type, semester)：有 → UPDATE，無 → INSERT。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM program_deadlines
               WHERE program_id = %s
                 AND deadline_type IS NOT DISTINCT FROM %s
                 AND semester IS NOT DISTINCT FROM %s""",
            (program_id, item.get("deadline_type"), item.get("semester")),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE program_deadlines
                   SET deadline_date = %s, note = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (item.get("deadline_date"), item.get("note"), row[0]),
            )
        else:
            cur.execute(
                """INSERT INTO program_deadlines
                   (program_id, deadline_type, deadline_date, semester, note, updated_at)
                   VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (program_id, item.get("deadline_type"), item.get("deadline_date"),
                 item.get("semester"), item.get("note")),
            )
    conn.commit()


def upsert_scholarship(conn, program_id: int, item: dict, source_url: str) -> None:
    """查 (program_id, name)：有 → UPDATE，無 → INSERT。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM program_scholarships
               WHERE program_id = %s AND name IS NOT DISTINCT FROM %s""",
            (program_id, item.get("name")),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE program_scholarships
                   SET amount_usd = %s, coverage = %s, eligibility = %s,
                       auto_consider = %s, source_url = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (item.get("amount_usd"), item.get("coverage"), item.get("eligibility"),
                 item.get("auto_consider"), source_url, row[0]),
            )
        else:
            cur.execute(
                """INSERT INTO program_scholarships
                   (program_id, name, amount_usd, coverage, eligibility, auto_consider, source_url, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (program_id, item.get("name"), item.get("amount_usd"), item.get("coverage"),
                 item.get("eligibility"), item.get("auto_consider"), source_url),
            )
    conn.commit()


def upsert_app_material(conn, program_id: int, item: dict) -> None:
    """查 (program_id, material_type)：有 → UPDATE，無 → INSERT。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM program_app_materials
               WHERE program_id = %s AND material_type IS NOT DISTINCT FROM %s""",
            (program_id, item.get("material_type")),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE program_app_materials
                   SET requirement = %s, word_limit = %s, note = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (item.get("requirement"), item.get("word_limit"), item.get("note"), row[0]),
            )
        else:
            cur.execute(
                """INSERT INTO program_app_materials
                   (program_id, material_type, requirement, word_limit, note, updated_at)
                   VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (program_id, item.get("material_type"), item.get("requirement"),
                 item.get("word_limit"), item.get("note")),
            )
    conn.commit()


def insert_review_item(conn, university_id: int, page_id: int | None, item: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO review_queue
               (university_id, page_id, program_code, field_name, field_value, reason, source_excerpt)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (university_id, page_id, item.get("program_code"), item.get("field_name"),
             str(item.get("field_value"))[:2000] if item.get("field_value") is not None else None,
             item.get("reason"), item.get("source_excerpt")),
        )
    conn.commit()


def replace_chunks(conn, university_id: int, page_id: int, school_id: str,
                   source_url: str, passed_types: list, chunks: list[dict],
                   program_id: int | None = None) -> None:
    """document_chunks 有 UNIQUE(page_id, chunk_index)：先刪舊再插入。"""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM document_chunks WHERE page_id = %s", (page_id,))
        for i, ch in enumerate(chunks):
            cur.execute(
                """INSERT INTO document_chunks
                   (university_id, page_id, program_id, school_id, source_url,
                    passed_types, chunk_index, chunk_text, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (university_id, page_id, program_id, school_id, source_url,
                 json.dumps(passed_types), i, ch["text"], ch.get("embedding")),
            )
    conn.commit()
