"""
pipeline步驟
url.py
for迴圈讀取root url檔案  a = {school_id list[url,url,url]}:
    建立school_id_url.json
    for 讀取root url in a， root:
        多worker工作抓取root的所有url，存入school_id_url.json

    1.完成單一學校抓取，代表school_id_url.json寫完
    2.發送訊號給score.py，處理這個檔案的所有url計分，抓取資料

score.py
接收school_id完成訊號
讀取school_id_url.json檔案
for 讀取url :
    score(url,高分存入school_id_data.json)

1.評分全部讀取完成，寫好school_id_data.json
2.呼叫clean data清洗資料
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from url_crawler import crawl_one_school
from score import score_one_school
from save_result import save_school_results
from clean_json_data import clean

from setting.root_url import SCHOOLS
from setting.parameter import CONFIG

PATH_DATA = Path(__file__).resolve().parent / "data"
PATH_URL  = Path(__file__).resolve().parent / "url_result"

# ──────────────────────────────────────────────
# 多校管理設定
# ──────────────────────────────────────────────

# 指定要執行的學校 ID；留空 [] 表示執行 SCHOOLS 中的全部學校
FORCE_UPDATE: list = ["northeastern", "bu","cornell","umn"]

# ── 增量跳過開關 ─────────────────────────────────────────────
# SKIP_RECENT_ENABLED = True  → 開啟：若資料檔在 SKIP_RECENT_HOURS 小時內已更新則跳過
# SKIP_RECENT_ENABLED = False → 關閉：每次都重新跑，忽略檔案新舊
SKIP_RECENT_ENABLED: bool = False
SKIP_RECENT_HOURS:   int  = 24     # 開啟時才有效


# ──────────────────────────────────────────────
# 增量輔助
# ──────────────────────────────────────────────

def school_data_is_recent(school_id: str,
                          output_dir: Path = PATH_DATA,
                          hours: int = SKIP_RECENT_HOURS) -> bool:
    """
    Return True if the school's data file exists and was last modified
    within `hours` hours.  Returns False on any error so the school is
    never silently skipped due to an OS exception.
    """
    if not SKIP_RECENT_ENABLED or hours <= 0:
        return False
    data_file = output_dir / f"{school_id}_data.json"
    try:
        if not data_file.exists():
            return False
        age_hours = (time.time() - os.path.getmtime(str(data_file))) / 3600
        return age_hours < hours
    except Exception:
        return False


def count_records(school_id: str, output_dir: Path = PATH_DATA) -> str:
    """Return record count from saved JSON, or '--' if unavailable."""
    data_file = output_dir / f"{school_id}_data.json"
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            return str(len(json.load(f)))
    except Exception:
        return "--"


def file_age_str(school_id: str, output_dir: Path = PATH_DATA) -> str:
    """Return file age as '1.2h' string, or '--' if file absent."""
    data_file = output_dir / f"{school_id}_data.json"
    try:
        if not data_file.exists():
            return "--"
        age_hours = (time.time() - os.path.getmtime(str(data_file))) / 3600
        return f"{age_hours:.1f}h"
    except Exception:
        return "--"


# ──────────────────────────────────────────────
# 每間學校的步驟
# ──────────────────────────────────────────────

def crawl_school(school) -> tuple:
    """
    Run the full pipeline for one school.
    Returns (school_id, status) where status is 'done' | 'skipped' | 'failed'.
    """
    school_id = school.get("school_id")

    # 增量模式：跳過近期已有資料的學校
    if school_data_is_recent(school_id):
        print(f"[SKIP]  {school_id} — data is recent (<{SKIP_RECENT_HOURS}h old), skipping")
        return school_id, "skipped"

    print(f"[START] {school_id}")
    try:
        # 第一步驟 爬完url存入json檔案
        crawl_one_school(school, CONFIG.MAX_DEPTH, PATH_URL)

        # 第二步驟 評分
        result = score_one_school(school_id, PATH_URL)

        # 第三步驟 儲存
        save_school_results(result, school_id, PATH_DATA)

        # 第四步驟 清洗
        clean()

        print(f"[DONE]  {school_id}")
        return school_id, "done"

    except Exception as e:
        print(f"[ERROR] {school_id}: {e}")
        return school_id, "failed"


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

def main():
    started_at = datetime.now()

    # 決定要執行哪些學校
    if FORCE_UPDATE:
        target_schools = [s for s in SCHOOLS if s.get("school_id") in FORCE_UPDATE]
    else:
        target_schools = list(SCHOOLS)

    print(f"\n{'='*60}")
    print(f"  MULTI-SCHOOL CRAWLER  —  {len(target_schools)} schools")
    skip_status = f"ON ({SKIP_RECENT_HOURS}h)" if SKIP_RECENT_ENABLED else "OFF"
    print(f"  SKIP_RECENT = {skip_status}")
    if FORCE_UPDATE:
        print(f"  FORCE_UPDATE = {FORCE_UPDATE}")
    else:
        print(f"  FORCE_UPDATE = [] (all schools)")
    print(f"{'='*60}\n")

    max_workers = min(3, len(target_schools))

    # 收集執行結果
    results_map: dict = {}   # school_id -> (status, records, age)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(crawl_school, school): school
            for school in target_schools
        }

        for future in as_completed(futures):
            school = futures[future]
            school_id = school.get("school_id")
            try:
                sid, status = future.result()
                results_map[sid] = status
                print(f"  → [{status.upper()}] {sid}")
            except Exception as e:
                results_map[school_id] = "failed"
                print(f"  → [FAILED] {school_id}: {e}")

    # ── 摘要表 ────────────────────────────────────────────────────────────
    elapsed = (datetime.now() - started_at).total_seconds()
    print(f"\n{'='*62}")
    print(f"  CRAWLER SUMMARY  (finished {datetime.now().strftime('%Y-%m-%d %H:%M')},"
          f" elapsed {elapsed:.0f}s)")
    print(f"{'='*62}")
    print(f"  {'School':<16} {'Status':<10} {'Records':>8}  {'File Age':>9}")
    print(f"  {'-'*16} {'-'*10} {'-'*8}  {'-'*9}")

    counts = {"done": 0, "skipped": 0, "failed": 0}
    for school in target_schools:
        sid    = school.get("school_id")
        status = results_map.get(sid, "?")
        rec    = count_records(sid)
        age    = file_age_str(sid)
        print(f"  {sid:<16} {status:<10} {rec:>8}  {age:>9}")
        if status in counts:
            counts[status] += 1

    print(f"  {'-'*50}")
    print(f"  Total: {len(target_schools)} schools  |  "
          f"{counts['done']} done  |  "
          f"{counts['skipped']} skipped  |  "
          f"{counts['failed']} failed")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
