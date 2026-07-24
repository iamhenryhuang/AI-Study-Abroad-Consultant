"""隔離實測背景補爬：真的爬 GradCafe + 清洗 + upsert，並驗證去重。
用 ucdavis（目前 0 筆）當測試對象。DB 需在跑。
"""
import sys, time
sys.path.insert(0, "backend/scripts")

from db.connection import get_connection
from retriever.experience_crawl import (
    _crawl_and_load, _recently_crawled, _school_to_gradcafe_query,
)

SCHOOL = "ucdavis"

def count(school_id):
    conn = get_connection(); conn.read_only = True
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM applicant_reports WHERE school_id=%s", (school_id,))
        n = cur.fetchone()[0]
    conn.close(); return n

print(f"GradCafe 查詢字串：{_school_to_gradcafe_query(SCHOOL)!r}")
print(f"補爬前 {SCHOOL} 筆數：{count(SCHOOL)}")

print("\n=== 同步執行 _crawl_and_load（會真的爬 5 頁，約需十幾秒）===")
_crawl_and_load(SCHOOL)          # 同步、直接看 [ExpCrawl] 輸出

print(f"\n補爬後 {SCHOOL} 筆數：{count(SCHOOL)}")
print(f"去重檢查 _recently_crawled({SCHOOL})：{_recently_crawled(SCHOOL)}（剛爬完應為 True）")
