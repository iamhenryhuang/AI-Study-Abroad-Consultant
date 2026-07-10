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
    """檢查資料是否已寫入資料庫。"""
    if not DATABASE_URL:
        print("錯誤: 未設定 DATABASE_URL（.env）。")
        return False
    try:
        conn = get_connection()
        if not conn:
            return False
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM universities")
            print(f"\nuniversities 筆數: {cur.fetchone()[0]}")
            cur.execute("SELECT school_id, name, domain FROM universities ORDER BY id")
            for sid, name, domain in cur.fetchall():
                print(f"   - {sid}: {name} ({domain})")

            cur.execute("SELECT COUNT(*) FROM programs")
            print(f"\nprograms 筆數: {cur.fetchone()[0]}")
            cur.execute("""
                SELECT u.school_id, p.gpa_min, p.toefl_min, p.ielts_min, p.gre_required,
                       (SELECT MIN(d.deadline_date) FROM program_deadlines d WHERE d.program_id = p.id)
                FROM programs p
                JOIN universities u ON p.university_id = u.id
                ORDER BY u.school_id
            """)
            for sid, gpa, toefl, ielts, gre_req, deadline in cur.fetchall():
                print(f"   [{sid}] GPA>={gpa} TOEFL>={toefl} IELTS>={ielts} "
                      f"GRE={gre_req} deadline={deadline}")

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
