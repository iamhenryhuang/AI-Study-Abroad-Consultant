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
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from concurrent.futures import ThreadPoolExecutor, as_completed

from url_crawler import crawl_one_school
from score import score_one_school
from save_result import save_school_results
from clean_json_data import clean

from setting.root_url import SCHOOLS
from setting.parameter import CONFIG

update_table = ["ucsd","mit","uci","caltech","gatech","WashU","standford","ucla","utoronto","perdure"]

# 每間學校的步驟
def crawl_school(school):
    school_id = school.get("school_id")
    print(f"[START] {school_id}")
    # 第一步驟 爬完url存入json檔案
    crawl_one_school(school, CONFIG.MAX_DEPTH)
    
    # 第二步驟 評分
    result = score_one_school(school_id)
    
    # 第三步驟 儲存
    save_school_results(result,school_id)
    
    # 第四步驟 清洗
    clean()
    print(f"[DONE]  {school_id}")
    return school_id

def main():
    target_schools = [
        school for school in SCHOOLS
        if school.get("school_id") in update_table
    ]

    max_workers = 3  # 或直接填固定數字，例如 4

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(crawl_school, school): school
            for school in target_schools
        }

        for future in as_completed(futures):
            school = futures[future]
            try:
                result = future.result()
                print(f"[OK] {result} 完成")
            except Exception as e:
                print(f"[ERROR] {school.get('school_id')} 發生錯誤: {e}")

if __name__ == "__main__":
    main()
        