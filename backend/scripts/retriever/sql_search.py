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
  - school_id   VARCHAR   -- 學校代碼（小寫縮寫），常見值：'cmu' = Carnegie Mellon University、
                              'mit' = Massachusetts Institute of Technology、'stanford' = Stanford University、
                              'caltech' = California Institute of Technology、'gatech' = Georgia Institute of Technology。
                              注意：縮寫（如 MIT、CMU）通常不是 name 欄位的子字串，必須靠 school_id 比對，不能只用 name ILIKE。
  - name        VARCHAR   -- 學校全名
  - domain      VARCHAR   -- 學校官網網域

資料表 program_requirements（每間學校一筆，CS 碩士申請要求）:
  - school_id         VARCHAR    -- 對應 universities.school_id
  - program_name      VARCHAR    -- 學程名稱
  - min_gpa            NUMERIC    -- 最低 GPA 要求（4.0 制）
  - gpa_scale           NUMERIC    -- GPA 滿分制（通常 4.0）
  - toefl_min           INTEGER    -- 最低 TOEFL iBT 分數（滿分 120）
  - ielts_min           NUMERIC    -- 最低 IELTS 分數（滿分 9.0）
  - duolingo_min        INTEGER    -- 最低 Duolingo 分數（滿分 160）
  - english_waiver_note TEXT       -- 英語測驗免試條件說明
  - gre_required        BOOLEAN    -- 是否需要 GRE
  - gre_min_total       INTEGER    -- GRE 總分最低要求（滿分 340）
  - gre_min_quant       INTEGER    -- GRE 數學最低要求（滿分 170）
  - gre_min_verbal      INTEGER    -- GRE 語文最低要求（滿分 170）
  - deadline_fall        DATE       -- 秋季入學申請截止日
  - deadline_spring      DATE       -- 春季入學申請截止日
  - priority_deadline    DATE       -- 優先申請截止日
  - tuition_per_year      INTEGER    -- 年度學費（美金）
  - funding_available     BOOLEAN    -- 是否有獎學金 / RA / TA 機會
  - funding_note          TEXT       -- 獎助學金說明（申請方式、覆蓋範圍等）
  - requires_sop          BOOLEAN    -- 是否需要 Statement of Purpose
  - num_recommendation_letters INTEGER -- 需要幾封推薦信
  - requires_resume       BOOLEAN    -- 是否需要履歷 / CV
  - source_url           TEXT       -- 官方資料來源網址

兩表可用 school_id 關聯（JOIN universities ON program_requirements.school_id = universities.school_id）。
"""

_ALLOWED_TABLES = {"universities", "program_requirements"}

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
2. 只能查詢 universities、program_requirements 這兩張表。
3. 需要學校名稱時，JOIN universities 取得 name 欄位（回傳需含 university_name 別名）。
4. 一律回傳 program_requirements.school_id 欄位方便後續識別。
5. 若問題提到特定學校（如 CMU、Stanford、MIT 等），請用 WHERE 過濾，優先用 school_id 精確比對（例如問題提到 MIT，用 school_id = 'mit'）；只有在問題給的是完整或部分校名（而非常見縮寫）時才用 name 過濾。
   - 用 name 過濾時必須用 ILIKE 搭配前後 % 萬用字元，例如 ILIKE '%Stanford%'，不可用精確比對（ILIKE 'Stanford' 是錯的），因為 name 欄位存的是全名（如 "Stanford University"）。
   - 若不確定縮寫對應的 school_id，可以同時用 OR 比對 school_id ILIKE 與 name ILIKE 兩者，提高命中率。
6. 若問題未指定學校，回傳所有學校的相關欄位（不要用 LIMIT 過度限制筆數，最多 20 筆）。
7. 只 SELECT 與問題相關的欄位，不要 SELECT *。
8. 只輸出 JSON，格式如下，不要有其他文字或 markdown code fence：

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

    try:
        conn.read_only = True
    except Exception:
        pass

    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
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
