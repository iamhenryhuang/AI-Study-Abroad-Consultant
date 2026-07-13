"""
Text-to-SQL 檢索：LLM 依 schema 描述產生唯讀 SELECT 查詢，
程式端執行前先做白名單與安全性檢查，再把結果轉為結構化 dict 回傳。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection
from generator.openai_client import call_llm

# ─── Schema 描述（給 LLM 看的） ────────────────────────────────────────────────

SCHEMA_DESCRIPTION = """
資料表 universities:
  - id          INTEGER   -- 主鍵，programs.university_id 對應此欄位
  - school_id   VARCHAR   -- 學校代碼（小寫縮寫），常見值：'cmu' = Carnegie Mellon University、
                              'mit' = Massachusetts Institute of Technology、'stanford' = Stanford University、
                              'caltech' = California Institute of Technology、'gatech' = Georgia Institute of Technology。
                              注意：縮寫（如 MIT、CMU）通常不是 name 欄位的子字串，必須靠 school_id 比對，不能只用 name ILIKE。
  - name        VARCHAR   -- 學校全名
  - domain      VARCHAR   -- 學校官網網域

資料表 programs（每間學校可有多個 program，例如 'CS MS' / 'CS PhD'；透過 university_id 關聯 universities）:
  - id                  INTEGER    -- 主鍵，program_deadlines / program_scholarships / program_app_materials.program_id 對應此欄位
  - university_id       INTEGER    -- 對應 universities.id（外鍵）
  - program_code        VARCHAR    -- program 代碼，存的就是 'CS MS' / 'CS PhD' 這類簡寫。使用者說「CS MS」「CS 碩士」時用這欄過濾（program_code = 'CS MS'），不要用 program_name。
  - degree_type         VARCHAR    -- 學位別，值為 'MS' / 'PhD' / 'MEng' / 'MPS' / 'MBA'。使用者只說「碩士 / master」未指定領域時用這欄（degree_type = 'MS'）。
  - program_name        VARCHAR    -- 學程全名，存的是完整名稱如 'Master of Science in Computer Science'，不含 'CS MS' 這種簡寫，勿用它比對簡寫。
  - department          VARCHAR    -- 系所名稱
  - toefl_min           INTEGER    -- 舊版相容：原文只寫 TOEFL、無法確認考試種類/尺度時的最低分
  - toefl_ibt_min       INTEGER    -- TOEFL iBT 舊制最低 overall（0–120）
  - toefl_ibt_new_scale_min NUMERIC -- 2026-01-21 起 TOEFL iBT 新制最低 overall（1–6、每 0.5 一級）
  - toefl_section_requirements TEXT -- TOEFL 各分項成績要求（合併字串）
  - ielts_min           NUMERIC    -- 最低 IELTS 分數
  - duolingo_min        INTEGER    -- 最低 Duolingo 分數
  - language_waiver     TEXT       -- 語言測驗豁免條件說明
  - english_test_notes  TEXT       -- 英文檢定效期、接受形式、例外與特殊規定
  - gre_required        VARCHAR    -- GRE 要求：'required' / 'optional' / 'not_accepted'（非布林）
  - gre_quant_min       INTEGER    -- GRE 數學最低要求
  - gre_verbal_min      INTEGER    -- GRE 語文最低要求
  - gre_awa_min         NUMERIC    -- GRE 寫作最低要求
  - gpa_min             NUMERIC    -- 最低 GPA 要求
  - gpa_scale           VARCHAR    -- GPA 滿分制（例如 '4.0'）
  - gpa_note            TEXT       -- GPA 補充說明
  - transcript_copies   INTEGER    -- 需繳交幾份成績單
  - transcript_format   VARCHAR    -- 成績單格式（自由文字，例如 electronic / paper / WES_required；查詢用 ILIKE 模糊比對）
  - rec_letter_count    INTEGER    -- 需要幾封推薦信
  - sop_word_limit      INTEGER    -- 讀書計畫字數限制
  - sop_prompt          TEXT       -- 讀書計畫題目 / 要求說明
  - cv_required         BOOLEAN    -- 是否需要履歷 / CV
  - writing_sample_required BOOLEAN -- 是否需要寫作樣本
  - application_fee_usd INTEGER    -- 申請費（美金）
  - fee_waiver_available BOOLEAN   -- 是否提供申請費減免
  - fee_waiver_criteria TEXT       -- 申請費減免條件
  - tuition_per_year_usd INTEGER   -- 年度學費（美金）
  - tuition_note        TEXT       -- 學費補充說明
  - application_url     TEXT       -- 申請入口網址
  - application_system  VARCHAR    -- 申請系統（自由文字，例如 GradCAS / Slate / self_hosted；查詢用 ILIKE 模糊比對）
  - source_urls         TEXT[]     -- 官方資料來源網址（陣列）

資料表 program_deadlines（一個 program 可有多筆截止日；透過 program_id 關聯 programs）:
  - program_id     INTEGER    -- 對應 programs.id
  - deadline_type  VARCHAR    -- 'early' / 'regular' / 'international' / 'rolling'
  - deadline_date  DATE       -- 舊版相容欄位，等同申請截止日期
  - application_open_date  DATE -- 申請開放日期
  - application_close_date DATE -- 申請截止日期
  - decision_release_date  DATE -- 結果公布日期
  - semester       VARCHAR    -- 學期，例如 'fall_2026'
  - note           TEXT       -- 補充說明

資料表 program_scholarships（一個 program 可有多筆獎助；透過 program_id 關聯 programs）:
  - program_id     INTEGER    -- 對應 programs.id
  - name           VARCHAR    -- 獎助名稱，例如 'RA/TA funding'
  - amount_usd     INTEGER    -- 金額（美金）
  - coverage       VARCHAR    -- full_tuition / partial / stipend_only
  - eligibility    TEXT       -- 資格 / 說明
  - auto_consider  BOOLEAN    -- 是否自動被考慮（否則需另外申請）

資料表 program_app_materials（一個 program 可有多筆特殊申請材料；透過 program_id 關聯 programs）:
  - program_id     INTEGER    -- 對應 programs.id
  - material_type  VARCHAR    -- additional_essay / portfolio / video / writing_sample / other
  - requirement    TEXT       -- 材料要求說明
  - word_limit     INTEGER    -- 字數限制
  - note           TEXT       -- 補充說明

關聯方式：
  - programs JOIN universities ON programs.university_id = universities.id
  - program_deadlines JOIN programs ON program_deadlines.program_id = programs.id
  - program_scholarships JOIN programs ON program_scholarships.program_id = programs.id
  - program_app_materials JOIN programs ON program_app_materials.program_id = programs.id
查詢申請要求（GPA/TOEFL/GRE/學費/推薦信/CV 等）用 programs；查截止日用 program_deadlines；
查獎助用 program_scholarships；查特殊申請材料（portfolio/video/writing sample 等）用 program_app_materials。
"""

_ALLOWED_TABLES = {
    "universities", "programs", "program_deadlines",
    "program_scholarships", "program_app_materials",
}

_SQL_FORBIDDEN_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXECUTE|CALL)\b",
    re.IGNORECASE,
)


def _build_sql_prompt(query: str) -> str:
    return f"""你是一個 Text-to-SQL 助理，負責將使用者的自然語言問題轉換為 PostgreSQL 唯讀查詢語句。

【資料庫 Schema】
{SCHEMA_DESCRIPTION}

【規則】
1. 只能產生單一個 SELECT 查詢語句，禁止任何寫入/修改語句（INSERT/UPDATE/DELETE/DROP 等）。
2. 只能查詢 universities、programs、program_deadlines、program_scholarships、program_app_materials 這幾張表。
3. 查詢 programs 時必須 JOIN universities ON programs.university_id = universities.id
   （programs 本身沒有 school_id 或校名欄位，一定要透過此 JOIN 才能取得）；
   查截止日再 JOIN program_deadlines ON program_deadlines.program_id = programs.id，
   查獎助再 JOIN program_scholarships ON program_scholarships.program_id = programs.id，
   查特殊申請材料再 JOIN program_app_materials ON program_app_materials.program_id = programs.id。
4. 一律回傳 universities.school_id（回傳需含 school_id 別名）與 universities.name（回傳需含 university_name 別名），方便後續識別。
5. 若問題提到特定學校（如 CMU、Stanford、MIT 等），請用 WHERE 過濾，優先用 universities.school_id 精確比對（例如問題提到 MIT，用 universities.school_id = 'mit'）；只有在問題給的是完整或部分校名（而非常見縮寫）時才用 name 過濾。
   - 用 name 過濾時必須用 ILIKE 搭配前後 % 萬用字元，例如 universities.name ILIKE '%Stanford%'，不可用精確比對（ILIKE 'Stanford' 是錯的），因為 name 欄位存的是全名（如 "Stanford University"）。
   - 若不確定縮寫對應的 school_id，可以同時用 OR 比對 universities.school_id ILIKE 與 universities.name ILIKE 兩者，提高命中率。
6. 若問題提到特定學程（如「CS MS」「CS 碩士」「CS PhD」），用 programs.program_code 精確比對（例如 programs.program_code = 'CS MS'），
   絕對不要用 programs.program_name ILIKE '%CS MS%'（program_name 存的是全名 'Master of Science in Computer Science'，不含 'CS MS' 簡寫，這樣一定查不到）。
   若問題只說「碩士 / master」未指定領域，改用 programs.degree_type = 'MS'。若問題沒特別指定學程，就不要對 program 加過濾條件，回傳該校所有 program。
   比對多個 program 時一律用 IN（例如 programs.program_code IN ('CS MS', 'CS PhD')），禁止用連續 OR；
   WHERE 中若混用 AND 與 OR，OR 的部分必須加括號（AND 優先於 OR，寫 school_id = 'cmu' AND code = 'CS MS' OR code = 'CS PhD'
   會變成 (school_id='cmu' AND code='CS MS') OR (code='CS PhD')，導致撈到其他學校的資料，這是嚴重錯誤）。
7. 若問題未指定學校，回傳所有學校的相關欄位（不要用 LIMIT 過度限制筆數，最多 20 筆）。
8. 只 SELECT 與問題相關的欄位，不要 SELECT *；但以下同義問題必須使用完整欄位組，不能只挑其中一欄：
   - 問 TOEFL／托福時，一律同時 SELECT programs.toefl_min、programs.toefl_ibt_min、programs.toefl_ibt_new_scale_min、programs.toefl_section_requirements。
   - 問英文成績／英語門檻時，一律 SELECT 上述 TOEFL 欄位，以及 programs.ielts_min、programs.duolingo_min、programs.language_waiver、programs.english_test_notes。
   - 問「申請相關資料」「申請條件」「必須知道的內容」「等等」這類廣泛問題時，至少 SELECT：
     programs.program_code、programs.program_name、programs.gpa_min、programs.gpa_scale、programs.gpa_note、
     programs.toefl_min、programs.toefl_ibt_min、programs.toefl_ibt_new_scale_min、programs.ielts_min、
     programs.duolingo_min、programs.language_waiver、programs.english_test_notes、programs.toefl_section_requirements、programs.gre_required、programs.rec_letter_count、
     programs.sop_word_limit、programs.cv_required、programs.application_fee_usd、programs.fee_waiver_available、
     programs.tuition_per_year_usd、programs.application_url。
     並 LEFT JOIN program_deadlines，SELECT application_open_date、application_close_date、decision_release_date、semester。
9. 新 schema 優先：查申請截止使用 application_close_date，不要只查舊版 deadline_date；查 TOEFL 不得只查 toefl_min。
10. 範例，問題「給我 UCLA 的申請相關資料，包含 GPA、英文成績、推薦信等等」應產生等價於：
    SELECT universities.school_id, universities.name AS university_name,
           programs.program_code, programs.program_name, programs.gpa_min, programs.gpa_scale, programs.gpa_note,
           programs.toefl_min, programs.toefl_ibt_min, programs.toefl_ibt_new_scale_min,
           programs.toefl_section_requirements, programs.ielts_min, programs.duolingo_min, programs.language_waiver, programs.english_test_notes,
           programs.gre_required, programs.rec_letter_count, programs.sop_word_limit, programs.cv_required,
           programs.application_fee_usd, programs.fee_waiver_available,
           programs.tuition_per_year_usd, programs.application_url,
           program_deadlines.application_open_date, program_deadlines.application_close_date,
           program_deadlines.decision_release_date, program_deadlines.semester
    FROM programs JOIN universities ON programs.university_id = universities.id
    LEFT JOIN program_deadlines ON program_deadlines.program_id = programs.id
    WHERE universities.school_id = 'ucla'
11. 只輸出 JSON，格式如下，不要有其他文字或 markdown code fence：

{{"sql": "SELECT ... ;"}}

【使用者問題】
{query}
"""


def _parse_sql_response(raw: str) -> str | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    sql = str(parsed.get("sql", "")).strip().rstrip(";")
    return sql or None


def _is_sql_safe(sql: str) -> bool:
    """基本安全檢查：僅允許單一 SELECT 語句，禁止寫入類關鍵字與多語句注入。"""
    if not sql:
        return False
    if ";" in sql:
        return False
    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        return False
    if _SQL_FORBIDDEN_PATTERN.search(sql):
        return False

    # 粗略檢查引用的表名是否都在白名單內
    referenced_tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE))
    if not referenced_tables or not referenced_tables.issubset(_ALLOWED_TABLES):
        return False

    return True


def _execute_readonly_query(sql: str) -> list[dict]:
    conn = get_connection()
    if not conn:
        print("[SQLSearch] 無法取得資料庫連線")
        return []

    # psycopg3：在交易開始前設定 read_only，整條連線的交易都會是唯讀，
    # 與白名單/正則檢查形成雙重防護（真正防寫入仍以 _is_sql_safe 為主）。
    conn.read_only = True

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        print(f"[SQLSearch] SQL 執行失敗：{e}\nSQL: {sql}")
        return []
    finally:
        conn.close()


def get_known_school_ids() -> set[str]:
    """回傳資料庫中已收錄的 school_id 集合，供判斷「查無資料」原因用。"""
    conn = get_connection()
    if not conn:
        return set()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT school_id FROM universities")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        print(f"[SQLSearch] 查詢已收錄學校清單失敗：{e}")
        return set()
    finally:
        conn.close()


def sql_search(query: str) -> tuple[list[dict], str | None]:
    """
    對單一自然語言問題執行 text-to-SQL 檢索。

    Returns:
        (results, sql) — results 為結構化 dict 列表，sql 為實際執行的查詢語句（失敗時為 None）
    """
    raw = call_llm(_build_sql_prompt(query))
    sql = _parse_sql_response(raw)

    if not sql or not _is_sql_safe(sql):
        print(f"[SQLSearch] LLM 產生的 SQL 不合法或無法解析，略過：{raw[:200]}")
        return [], None

    print(f"[SQLSearch] 執行查詢：{sql}")
    rows = _execute_readonly_query(sql)

    for row in rows:
        if "name" in row and "university_name" not in row:
            row["university_name"] = row.pop("name")

    return rows, sql
