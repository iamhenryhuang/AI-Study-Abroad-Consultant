import psycopg

from .connection import DATABASE_URL, BACKEND_ROOT, PROJECT_ROOT, get_connection


# ── setup_db ─────────────────────────────────────────────────

def setup_db():
    """檢查連線並在需要時建立目標資料庫。"""
    from urllib.parse import urlparse, urlunparse
    url = DATABASE_URL
    if not url:
        print("錯誤: .env 中未設定 DATABASE_URL。")
        return False

    # 從 URL 中解析目標資料庫名稱
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")  # e.g. 'study_abroad_rag'
    if not db_name:
        print("錯誤: DATABASE_URL 中找不到資料庫名稱。")
        return False

    # 建立連往 postgres（預設維護庫）的 URL
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


# ── import_json ──────────────────────────────────────────────

def import_json(data_dirname: str = "crawler/school_data"):
    """
    依 db/init_db.sql 建表，並啟動 embedder/pipeline.py 的 run_pipeline。

    1. 建立/重置資料表（執行 init_db.sql）
    2. 呼叫 pipeline.run_pipeline()，資料來源預設為 crawler/school_data/
    """
    conn = get_connection()
    if not conn:
        print("請在 .env 設定 DATABASE_URL。")
        return False
    try:
        # 1. 建表：psycopg3 不支援 execute() 執行多條 SQL，逐一執行各 statement
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
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"建表失敗: {e}")
        return False

    # 2. 執行 pipeline（切片 + 向量化 + 寫入）
    import sys
    if str(BACKEND_ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT / "scripts"))

    from embedder.pipeline import run_pipeline
    return run_pipeline(data_dirname)


# ── verify ───────────────────────────────────────────────────

def verify():
    """檢查資料是否已寫入資料庫（v2 schema）。"""
    if not DATABASE_URL:
        print("錯誤: 未設定 DATABASE_URL（.env）。")
        return False
    try:
        conn = get_connection()
        if not conn:
            return False
        with conn.cursor() as cur:
            # universities
            cur.execute("SELECT COUNT(*) FROM universities")
            print(f"\nuniversities 筆數: {cur.fetchone()[0]}")
            cur.execute("SELECT school_id, name, domain FROM universities ORDER BY id")
            for sid, name, domain in cur.fetchall():
                print(f"   - {sid}: {name} ({domain})")

            # web_pages
            cur.execute("SELECT COUNT(*) FROM web_pages")
            print(f"\nweb_pages 筆數: {cur.fetchone()[0]}")
            cur.execute("""
                SELECT u.school_id, COUNT(*)
                FROM web_pages wp
                JOIN universities u ON wp.university_id = u.id
                GROUP BY u.school_id
                ORDER BY u.school_id
            """)
            for sid, cnt in cur.fetchall():
                print(f"   [{sid}] {cnt} 頁")

            # document_chunks
            cur.execute("SELECT COUNT(*) FROM document_chunks")
            print(f"\ndocument_chunks 筆數: {cur.fetchone()[0]}")
            cur.execute("""
                SELECT dc.school_id, pt->>'type' AS ptype, COUNT(*) AS cnt
                FROM document_chunks dc,
                     jsonb_array_elements(dc.passed_types) AS pt
                GROUP BY dc.school_id, pt->>'type'
                ORDER BY dc.school_id, pt->>'type'
            """)
            for sid, ptype, cnt in cur.fetchall():
                print(f"   [{sid}][{ptype}] {cnt} chunks")

        conn.close()
        print("\n驗證通過：資料已存在於資料庫。")
        return True
    except psycopg.ProgrammingError as e:
        print(f"資料表不存在或結構錯誤: {e}")
        print("請先執行: python scripts/run.py import")
        return False
    except Exception as e:
        print(f"連線或查詢錯誤: {e}")
        return False
