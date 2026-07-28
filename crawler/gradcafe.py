"""
GradCafe 申請結果爬蟲
透過 Inertia.js data-page JSON 取得資料，支援分頁與多關鍵字搜尋。
"""

import requests
import json
import os
import time
import sys
from bs4 import BeautifulSoup
from pathlib import Path

# 輸出 UTF-8（避免 Windows cp950 編碼錯誤）
sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://www.thegradcafe.com"
HEADERS  = {"User-Agent": "Mozilla/5.0"}

# 要搜尋的關鍵字（學校 or 科系）
QUERIES = [
    "computer science",
    "CMU",
    "Stanford",
    "MIT",
    "UCLA",
    "UC San Diego",
    "UIUC",
    "Columbia",
    "Cornell",
    "NYU",
]

OUTPUT_PATH = Path(__file__).parent / "data" / "gradcafe_results.json"


def should_run_gradcafe_for_school(
    school: str,
    conn=None,
    min_records: int = 3,
) -> tuple[bool, str]:
    """Return whether a school still needs GradCafe backfill.

    Existing official-school aliases are resolved through universities, then
    applicant_reports and user_experiences are counted together.  A caller may
    inject a connection for tests; otherwise the configured backend DB is used.
    """
    school = (school or "").strip()
    if not school:
        return False, "未提供學校名稱"

    owns_connection = conn is None
    if conn is None:
        import psycopg
        from dotenv import load_dotenv

        project_root = Path(__file__).resolve().parent.parent
        load_dotenv(project_root / "backend" / ".env")
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            return True, "資料不足：未設定 DATABASE_URL"
        conn = psycopg.connect(database_url)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, school_id
                FROM universities
                WHERE lower(school_id) = lower(%s)
                   OR lower(name) = lower(%s)
                   OR name ILIKE %s
                """,
                (school, school, f"%{school}%"),
            )
            matches = cur.fetchall()
            if not matches:
                return True, f"資料不足：找不到 {school} 的既有學校資料"

            school_ids = [str(row[1]) for row in matches if len(row) > 1 and row[1]]
            cur.execute(
                "SELECT COUNT(*) FROM applicant_reports WHERE school_id = ANY(%s)",
                (school_ids,),
            )
            report_row = cur.fetchone()
            report_count = int(report_row[0]) if report_row else 0

            cur.execute(
                "SELECT COUNT(*) FROM user_experiences WHERE apply_school ILIKE %s",
                (f"%{school}%",),
            )
            experience_row = cur.fetchone()
            experience_count = int(experience_row[0]) if experience_row else 0

            total = report_count + experience_count
            if total >= min_records:
                return False, f"既有申請經驗共 {total} 筆，資料足夠"
            return True, f"既有申請經驗僅 {total} 筆，資料不足"
    finally:
        if owns_connection and conn is not None:
            conn.close()


def fetch_page(query: str, page: int, version: str) -> dict | None:
    """
    取得單頁搜尋結果。
    - 第 1 頁：普通 GET，從 data-page 解析
    - 第 2+ 頁：帶 X-Inertia header，直接回傳 JSON
    """
    url = f"{BASE_URL}/survey/"
    params = {"q": query, "page": page}

    if page == 1:
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code != 200:
            print(f"  [錯誤] 第 1 頁回傳 {resp.status_code}")
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        app_div = soup.find(id="app")
        if not app_div or not app_div.get("data-page"):
            print("  [錯誤] 找不到 data-page")
            return None
        return json.loads(app_div["data-page"])  # 裡面包含了後續請求需要的 version
    
    else:
        inertia_headers = {
            **HEADERS,
            "X-Inertia":         "true",
            "X-Inertia-Version": version,
            "X-Requested-With":  "XMLHttpRequest",
            "Accept":            "application/json",
        }
        resp = requests.get(url, headers=inertia_headers, params=params)
        if resp.status_code != 200:
            print(f"  [錯誤] 第 {page} 頁回傳 {resp.status_code}")
            return None
        return resp.json()


def crawl_query(query: str, max_pages: int = 5) -> list[dict]:
    """爬單一關鍵字的所有分頁。"""
    print(f"\n搜尋：{query}")
    all_entries: list[dict] = []

    # 第 1 頁：同時取得 Inertia version
    page_data = fetch_page(query, page=1, version="")
    if not page_data:
        return []

    version = page_data.get("version", "")
    #print(f"  Inertia version: {version}")
    results  = page_data["props"].get("results", {})
    #print(f"  results:{results}")
    entries  = results.get("data", [])
    all_entries.extend(entries)
    print(f"  第 1 頁：{len(entries)} 筆")

    # 取得總頁數
    meta       = results.get("meta", {})
    last_page  = meta.get("last_page", 1)
    total_pages = min(last_page, max_pages)
    print(f"  總頁數：{last_page}（爬取前 {total_pages} 頁）")

    # 第 2+ 頁
    for page in range(2, total_pages + 1):
        time.sleep(1.5)
        page_data = fetch_page(query, page=page, version=version)
        if not page_data:
            break
        entries = page_data["props"].get("results", {}).get("data", [])
        all_entries.extend(entries)
        print(f"  第 {page} 頁：{len(entries)} 筆")

    print(f"  小計：{len(all_entries)} 筆")
    return all_entries


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    seen_ids: set[int]      = set()

    for query in QUERIES:
        entries = crawl_query(query, max_pages=5)
        for entry in entries:
            if entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                all_results.append(entry)
        time.sleep(2)

    print(f"\n共爬取 {len(all_results)} 筆不重複資料")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"已儲存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
