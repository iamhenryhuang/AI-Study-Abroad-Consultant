import json

import psycopg

from .connection import DATABASE_URL, PROJECT_ROOT, get_connection


# ── setup_db ─────────────────────────────────────────────────

def setup_db():
    """檢查連線並在需要時建立目標資料庫。"""
    from urllib.parse import urlparse, urlunparse
    url = DATABASE_URL
    if not url:
        print("錯誤: .env 中未設定 DATABASE_URL。")
        return False

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        print("錯誤: DATABASE_URL 中找不到資料庫名稱。")
        return False

    postgres_url = urlunparse(parsed._replace(path="/postgres"))
    try:
        print(f"連線至: {postgres_url.split('@')[-1]}")
        conn = psycopg.connect(postgres_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                print(f"建立資料庫 {db_name} ...")
                cur.execute(f"CREATE DATABASE \"{db_name}\"")
                print(f"已建立 {db_name}。")
            else:
                print(f"資料庫 {db_name} 已存在。")
        conn.close()
        print("連線測試通過。")
        return True
    except Exception as e:
        print(f"失敗: {e}")
        return False


# ── init_schema ──────────────────────────────────────────────

def init_schema():
    """依 db/init_db.sql 重置所有資料表（universities / programs 及其子表、web_pages / document_chunks / review_queue）。"""
    conn = get_connection()
    if not conn:
        print("請在 .env 設定 DATABASE_URL。")
        return False
    try:
        sql_path = PROJECT_ROOT / "db" / "init_db.sql"
        if not sql_path.is_file():
            print(f"找不到 {sql_path}")
            return False
        statements = [
            s.strip()
            for s in sql_path.read_text(encoding="utf-8").split(";")
            if s.strip()
        ]
        with conn.cursor() as cur:
            for stmt in statements:
                try:
                    cur.execute(stmt)
                except Exception as e:
                    conn.rollback()
                    conn.close()
                    print(f"建表失敗，SQL：\n{stmt[:200]}\n錯誤：{e}")
                    return False
        conn.commit()
        conn.close()
        print("已依 init_db.sql 建立/重置資料表。")
        return True
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"建表失敗: {e}")
        return False


def init_experience_schema():
    """冪等建立使用者申請經驗表，不影響既有資料。"""
    try:
        from .experiences import ensure_experience_schema
        ensure_experience_schema()
        print("已建立或確認 user_experiences 資料表。")
        return True
    except Exception as e:
        print(f"申請經驗表初始化失敗: {e}")
        return False


def clear_crawler_data():
    """清除所有爬蟲 DB 資料、checkpoints 與生成 JSON；保留 schema/使用者經驗。"""
    conn = get_connection()
    if not conn:
        print("錯誤: 無法連線資料庫，未刪除任何資料或檔案。")
        return False

    try:
        with conn.cursor() as cur:
            # universities CASCADE 會清除 programs、子表、web_pages、chunks、review。
            cur.execute("TRUNCATE TABLE universities RESTART IDENTITY CASCADE")
            checkpoint_tables = []
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cur.fetchone()[0] is not None:
                    checkpoint_tables.append(table)
            if checkpoint_tables:
                # table 名稱來自上方固定白名單，不接受外部輸入。
                cur.execute(f"TRUNCATE TABLE {', '.join(checkpoint_tables)}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"清除資料庫失敗，未刪除 JSON：{e}")
        return False
    conn.close()

    crawler_dir = PROJECT_ROOT / "data_crawler"
    patterns = (
        crawler_dir / "output" / "*.json",
        crawler_dir / "url" / "all_url" / "*.json",
        crawler_dir / "url" / "keep" / "*.json",
        crawler_dir / "url" / "drop" / "*.json",
    )
    removed = []
    try:
        for pattern in patterns:
            for path in pattern.parent.glob(pattern.name):
                path.unlink()
                removed.append(path.relative_to(PROJECT_ROOT).as_posix())
    except Exception as e:
        print(f"資料庫已清空，但部分 JSON 刪除失敗：{e}")
        return False

    print("已清除所有爬蟲資料與 LangGraph checkpoints。")
    print(f"已刪除 {len(removed)} 個爬蟲 JSON 檔案。")
    return True


def load_schools():
    """建表後灌入 db/data/schools_data.json 的學校資料。"""
    if not init_schema():
        return False

    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from db.load_schools import load
    return load()


# ── verify ───────────────────────────────────────────────────

def verify():
    """輸出 crawler DB 的全局結構化資料與稽核摘要（不印完整 raw_text/chunks）。"""
    if not DATABASE_URL:
        print("錯誤: 未設定 DATABASE_URL（.env）。")
        return False
    try:
        conn = get_connection()
        if not conn:
            return False
        with conn.cursor() as cur:
            def show(label: str, sql: str):
                cur.execute(sql)
                columns = [desc.name for desc in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                print(f"\n{'=' * 20} {label} ({len(rows)}) {'=' * 20}")
                print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))

            show("UNIVERSITIES", """
                SELECT id, school_id, name, domain, created_at
                FROM universities ORDER BY school_id
            """)
            show("PROGRAMS - ALL FIELDS", """
                SELECT u.school_id, p.*
                FROM programs p
                JOIN universities u ON u.id = p.university_id
                ORDER BY u.school_id, p.program_code
            """)
            show("GLOBAL EXTRACTIONS - INCLUDING SCHOOLS WITHOUT PROGRAMS", """
                SELECT u.school_id,
                       CASE WHEN EXISTS (
                           SELECT 1 FROM programs p WHERE p.university_id = ge.university_id
                       ) THEN true ELSE false END AS has_program,
                       ge.id, ge.scope, ge.program_code, ge.source_url,
                       ge.confidence, ge.extraction, ge.created_at, ge.updated_at
                FROM global_extractions ge
                JOIN universities u ON u.id = ge.university_id
                ORDER BY u.school_id, ge.scope, ge.id
            """)
            show("PROGRAM DEADLINES", """
                SELECT u.school_id, p.program_code, d.*
                FROM program_deadlines d
                JOIN programs p ON p.id = d.program_id
                JOIN universities u ON u.id = p.university_id
                ORDER BY u.school_id, p.program_code, d.semester, d.deadline_type
            """)
            show("PROGRAM SCHOLARSHIPS", """
                SELECT u.school_id, p.program_code, s.*
                FROM program_scholarships s
                JOIN programs p ON p.id = s.program_id
                JOIN universities u ON u.id = p.university_id
                ORDER BY u.school_id, p.program_code, s.name
            """)
            show("PROGRAM APPLICATION MATERIALS", """
                SELECT u.school_id, p.program_code, m.*
                FROM program_app_materials m
                JOIN programs p ON p.id = m.program_id
                JOIN universities u ON u.id = p.university_id
                ORDER BY u.school_id, p.program_code, m.material_type
            """)
            show("WEB PAGES - AUDIT", """
                SELECT u.school_id, w.id, w.program_id, w.url, w.passed_types,
                       w.char_count, left(w.raw_text, 300) AS raw_text_preview,
                       w.created_at
                FROM web_pages w
                LEFT JOIN universities u ON u.id = w.university_id
                ORDER BY u.school_id, w.id
            """)
            show("DOCUMENT CHUNKS - SUMMARY", """
                SELECT school_id, count(*) AS chunk_count,
                       count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded_count,
                       min(created_at) AS first_created_at,
                       max(created_at) AS last_created_at
                FROM document_chunks GROUP BY school_id ORDER BY school_id
            """)
            show("REVIEW QUEUE", """
                SELECT u.school_id, rq.id, rq.page_id, rq.program_code,
                       rq.field_name, rq.field_value, rq.reason,
                       rq.source_excerpt, rq.status, rq.created_at
                FROM review_queue rq
                LEFT JOIN universities u ON u.id = rq.university_id
                ORDER BY u.school_id, rq.id
            """)

        conn.close()
        print("\n驗證通過：資料已存在於資料庫。")
        return True
    except psycopg.ProgrammingError as e:
        print(f"資料表不存在或結構錯誤: {e}")
        print("請先執行: python backend/scripts/run.py init-all")
        return False
    except Exception as e:
        print(f"連線或查詢錯誤: {e}")
        return False
