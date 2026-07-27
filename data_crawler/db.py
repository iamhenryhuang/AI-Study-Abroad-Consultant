"""data_crawler 的 DB 存取層。

- 連線方式沿用 backend/scripts/db/connection.py（.env 的 DATABASE_URL + psycopg3）。
- db/schema_programs.sql 是建表的唯一權威來源；MIGRATION_SQL 只做加法式升級。
- 結構化欄位無法安全正規化時，改寫入 program_evidence 保留原文上下文。
- deadlines / scholarships / app materials 使用查詢後 INSERT/UPDATE，
  不依賴額外 UNIQUE 約束。
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
-- 加法式 migration：不 DROP 任何既有資料。
--
-- 表定義不寫在這裡——由 db/schema_programs.sql（唯一權威來源）在 ensure_migrations()
-- 中先執行，本字串只補「既有 DB 的欄位升級」，兩者合起來即可讓任何狀態的 DB 就緒。
-- 舊 DB 若還是更早的 schema，下列 ALTER 會把缺的欄位/型別補齊。

ALTER TABLE web_pages       ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL;
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL;
ALTER TABLE program_deadlines     ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
ALTER TABLE programs ADD COLUMN IF NOT EXISTS toefl_ibt_new_scale_min NUMERIC(2,1);
ALTER TABLE programs ADD COLUMN IF NOT EXISTS toefl_section_requirements TEXT;
ALTER TABLE programs ADD COLUMN IF NOT EXISTS english_test_notes TEXT;
ALTER TABLE programs ALTER COLUMN application_system TYPE TEXT;
ALTER TABLE programs ALTER COLUMN program_code TYPE TEXT;
ALTER TABLE global_extractions ALTER COLUMN program_code TYPE TEXT;
ALTER TABLE review_queue ALTER COLUMN program_code TYPE TEXT;
ALTER TABLE program_app_materials ALTER COLUMN material_type TYPE TEXT;
ALTER TABLE program_evidence ALTER COLUMN field_name SET DEFAULT 'general';
UPDATE program_evidence SET field_name = 'general' WHERE field_name IS NULL;
ALTER TABLE program_evidence ALTER COLUMN field_name SET NOT NULL;
ALTER TABLE programs ALTER COLUMN transcript_format TYPE TEXT;
ALTER TABLE program_deadlines ADD COLUMN IF NOT EXISTS application_open_date DATE;
ALTER TABLE program_deadlines ADD COLUMN IF NOT EXISTS application_close_date DATE;
ALTER TABLE program_deadlines ADD COLUMN IF NOT EXISTS decision_release_date DATE;
ALTER TABLE program_scholarships  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
ALTER TABLE program_app_materials ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
ALTER TABLE program_deadlines     ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE program_scholarships  ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE program_app_materials ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

-- 回填既有資料：updated_at 為 NULL 的舊列補上 created_at
UPDATE program_deadlines     SET updated_at = created_at WHERE updated_at IS NULL;
UPDATE program_scholarships  SET updated_at = created_at WHERE updated_at IS NULL;
UPDATE program_app_materials SET updated_at = created_at WHERE updated_at IS NULL;
"""

# programs 表中允許由 LLM 抽取寫入的欄位（對應 init_db.sql）
PROGRAM_FIELDS = [
    "toefl_min", "toefl_ibt_min", "toefl_ibt_new_scale_min", "toefl_section_requirements",
    "ielts_min", "duolingo_min", "language_waiver", "english_test_notes",
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


SCHEMA_PATH = _PROJECT_ROOT / "db" / "schema_programs.sql"


def ensure_migrations(conn) -> None:
    """建表（共用 schema）+ 既有 DB 欄位升級；兩者皆冪等，不動既有資料。"""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
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


def upsert_global_extraction(conn, university_id: int, page_id: int, scope: str,
                             program_code: str, extraction: dict,
                             confidence: float | None, source_url: str) -> int:
    """保存尚未能套用到正式 program 的全校／系級結構化結果。"""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO global_extractions
                   (university_id, page_id, scope, program_code, extraction, confidence, source_url)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
               ON CONFLICT (page_id, program_code) DO UPDATE SET
                   scope = EXCLUDED.scope,
                   extraction = EXCLUDED.extraction,
                   confidence = EXCLUDED.confidence,
                   source_url = EXCLUDED.source_url,
                   updated_at = CURRENT_TIMESTAMP
               RETURNING id""",
            (university_id, page_id, scope, program_code,
             json.dumps(extraction, ensure_ascii=False), confidence, source_url),
        )
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


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
                   SET deadline_date = %s, application_open_date = %s,
                       application_close_date = %s, decision_release_date = %s,
                       note = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (item.get("application_close_date") or item.get("deadline_date"),
                 item.get("application_open_date"), item.get("application_close_date"),
                 item.get("decision_release_date"), item.get("note"), row[0]),
            )
        else:
            cur.execute(
                """INSERT INTO program_deadlines
                   (program_id, deadline_type, deadline_date, application_open_date,
                    application_close_date, decision_release_date, semester, note, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)""",
                (program_id, item.get("deadline_type"),
                 item.get("application_close_date") or item.get("deadline_date"),
                 item.get("application_open_date"), item.get("application_close_date"),
                 item.get("decision_release_date"), item.get("semester"), item.get("note")),
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


def upsert_program_evidence(conn, program_id: int, item: dict, source_url: str) -> None:
    """保存無法安全正規化的招生證據段落，供後續 RAG 使用。"""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO program_evidence
               (program_id, category, field_name, evidence_kind,
                evidence_text, source_excerpt, source_url, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (program_id, category, field_name, source_url)
               DO UPDATE SET evidence_kind = EXCLUDED.evidence_kind,
                             evidence_text = EXCLUDED.evidence_text,
                             source_excerpt = EXCLUDED.source_excerpt,
                             updated_at = CURRENT_TIMESTAMP""",
            (program_id, item.get("category") or "other", item.get("field_name") or "general",
             item.get("evidence_kind") or "context_note", item.get("evidence_text"),
             item.get("source_excerpt"), source_url),
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
