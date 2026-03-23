import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime

from url_crawler import crawl_all_schools
from score import (
    classify_url_with_browser,
    print_result,
    THRESHOLDS,
    NUM_THREADS,
)
from setting.root_url import SCHOOLS
from save_result import save_single_result, save_school_results   # ← 新增


# ──────────────────────────────────────────────
# Worker：自己建立 playwright，不依賴外部實例
# ──────────────────────────────────────────────

def worker(school_urls_batch: list) -> list:
    """
    school_urls_batch: list of (school_id, url)
    每個 thread 自己啟動 sync_playwright，完全不共享。
    """
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        page = context.new_page()

        for school_id, url in school_urls_batch:
            result = classify_url_with_browser(
                page, url, thresholds=THRESHOLDS, school_id=school_id
            )
            print_result(result)
            save_single_result(result)   # ← 每抓完一頁立即寫檔，不等全部完成
            results.append(result)

        context.close()
        browser.close()

    return results


# ──────────────────────────────────────────────
# 主程式：crawler → scorer 串接
# ──────────────────────────────────────────────

def main():
    print(f"\n=== Pipeline: Crawler → Scorer  threads={NUM_THREADS} ===\n")

    # Step 1：爬取所有學校的 URL
    print("📡 Step 1: 爬取學校 URL...\n")
    target_schools = crawl_all_schools(SCHOOLS, max_depth=10)

    # 展平成 (school_id, url) 列表
    flat = [
        (school["school_id"], url)
        for school in target_schools
        for url in school["urls"]
    ]
    print(f"\n✅ 共收集 {len(flat)} 個 URL，準備分類...\n")

    # Step 2：分批分類（每個 thread 自己建立 playwright）
    print("🔍 Step 2: 分類 URL...\n")
    chunks = [flat[i::NUM_THREADS] for i in range(NUM_THREADS)]

    all_results = []
    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = {executor.submit(worker, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            all_results.extend(future.result())

    # Step 3：輸出完整原始結果（保留，供除錯用）
    print(f"\n✅ 全部完成！共處理 {len(all_results)} 個 URL")

    output_path = "results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"📦 原始結果已儲存至 {output_path}")

    # Step 4：補存（處理 worker 內 save_single_result 遺漏的邊角情況）
    print("💾 Step 4: 確認學校資料已完整寫入...\n")
    save_school_results(all_results)


if __name__ == "__main__":
    main()