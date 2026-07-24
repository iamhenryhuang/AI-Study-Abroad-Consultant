"""
crawler.py  —  多學校多 root 遞迴爬取，輸出格式相容 classifier.py 的 schools 變數

使用方式：
    python crawler.py
"""
import os
import re
import sys
import json
import threading
from collections import deque
from urllib.parse import urlparse, urljoin, urlunparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
# setting
from setting.parameter import CONFIG,USER_AGENT
from setting.blacklist import IGNORED_EXTENSIONS,BLACKLIST_PATH_FRAGMENTS


# ══════════════════════════════════════════════
# URL 工具
# ══════════════════════════════════════════════

# input = url = "HTTPS://Example.COM/docs/page///?id=123&lang=zh#section1"
# output = "https://example.com/docs/page"
def normalize_url(url: str) -> str:
    try:
        # urlparse用來拆解url的各部分，讓各部分能夠直接取用(p.path,p.seheme)
        # or用來避免空字串，原本的docs/page/// -> docs/page
        # 如果本來就只有/，會變成""
        p    = urlparse(url)
        path = p.path.rstrip("/") or "/"
        return urlunparse((
            p.scheme.lower(), p.netloc.lower(),
            # 如果strip query = T，刪除參數變成""
            path, "", "" if CONFIG.STRIP_QUERY else p.query, ""
        ))
    except Exception:
        return url


# 取得網域與path的組合
def get_root_info(url: str) -> dict:
    p = urlparse(url)
    return {"netloc": p.netloc.lower(), "root_path": p.path}

# 判斷是否在同個root下，不在則不抓
def is_same_root(url: str, root_info: dict) -> bool:
    try:
        p = urlparse(url)
        return (
            p.netloc.lower() == root_info["netloc"]
            and p.path.rstrip("/").startswith(root_info["root_path"].rstrip("/"))
        )
    except Exception:
        return False

# 回傳 true 是不需要的url
def has_ignored_extension(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    # 只要 path 以 任一個 IGNORED_EXTENSIONS 裡的副檔名結尾，就回傳 True
    return any(path.endswith(ext) for ext in IGNORED_EXTENSIONS)


# ══════════════════════════════════════════════
# 過濾
# ══════════════════════════════════════════════

# 過濾掉帶有黑名單的url
def filter_url(url: str) -> tuple[bool, str]:
    if not url.startswith(("http://", "https://")):
        return False, "non-http"
    # 過濾PDF等等檔案
    if has_ignored_extension(url):
        return False, "ext"
    full_low = url.lower()
    #print(full_low)
    for frag in BLACKLIST_PATH_FRAGMENTS:
        if frag in full_low:
            return False, f"black:{frag}"
    return True, "keep"


# ══════════════════════════════════════════════
# 連結擷取
# ══════════════════════════════════════════════

# 擷取某個url下的所有url，並進行過濾，放入stack作為下一輪的url
def extract_links(page, current_url: str, root_info: dict) -> dict:
    # page.evaluate(...) → 在「瀏覽器頁面裡執行 JavaScript」
    raw_links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
                   .map(a => a.href)
                   .filter(h => h && h.length > 0)
    """) or []
    '''
    # test
    print("raw link",len(raw_links))
    for r in raw_links:
        print(r)
    '''
    keep     = set()
    drop     = {}
    external = set()

    # 判斷哪一些能夠進入stack
    for raw in raw_links:
        # 第一步:進行格式化，轉小寫、去除參數
        try:
            normalized = normalize_url(urljoin(current_url, raw))
        except Exception:
            continue

        # 第二步:判斷是否在同個root下，否則加入外部連結
        if not is_same_root(normalized, root_info):
            external.add(normalized)
            continue

        ok, reason = filter_url(normalized)
        if ok:
            keep.add(normalized)
        else:
            drop[normalized] = reason

    '''
    # test
    print("\nafter fikter keep link",len(keep))
    for r in keep:
        print(r)
    print("\nafter fikter external link",len(external))
    for r in external:
        print(r)
    print("\nafter fikter drop link",len(drop))
    print(drop)
    print("\n")
    '''
    return {"keep": keep, "drop": drop, "external": external}


# ══════════════════════════════════════════════
# 單頁爬取
# ══════════════════════════════════════════════

# page 為爬蟲框架的內容，用來進行爬取
def crawl_page(page, url: str, root_info: dict) -> dict:
    result = {
        "url": url, "title": "",
        "keep": set(), "drop": {}, "external": set(),
        "error": None,
    }
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(CONFIG.RENDER_WAIT_MS)
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        page.evaluate("() => window.scrollTo(0, 0)")
        result["title"] = page.title() or ""
        links = extract_links(page, url, root_info)
        result.update(links)
    except Exception as e:
        result["error"] = str(e)
    return result


# ══════════════════════════════════════════════
# 封鎖不必要資源（加速）
# ══════════════════════════════════════════════

def block_resources(context):
    context.route("**/*", lambda route: (
        route.abort()
        if route.request.resource_type in {"image", "media", "font", "stylesheet"}
        else route.continue_()
    ))


# ══════════════════════════════════════════════
# Worker
# ══════════════════════════════════════════════

def run_worker(task_chunk: list, root_info: dict, worker_id: int, print_lock: threading.Lock) -> list:
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        block_resources(context)
        page = context.new_page()

        for idx, (url, depth, parent) in enumerate(task_chunk):
            result = crawl_page(page, url, root_info)
            results.append((url, depth, parent, result))

            indent = "  " * depth
            with print_lock:
                status = f"⚠️  {result['error'][:60]}" if result["error"] else f'"{result["title"][:50]}"'
                print(f"{indent}[w{worker_id}|d={depth}|#{idx+1}] {url}  →  {status}")

            if idx < len(task_chunk) - 1:
                page.wait_for_timeout(CONFIG.CRAWL_DELAY_MS)

        context.close()
        browser.close()
    return results


# ══════════════════════════════════════════════
# BFS（單一 root）
# ══════════════════════════════════════════════

def crawl_tree(root_url: str, max_depth: int = CONFIG.MAX_DEPTH) -> dict:
    root_url  = normalize_url(root_url)
    root_info = get_root_info(root_url)

    visited       = set()
    pages         = {}
    all_external  = set()
    all_drop      = {}
    print_lock    = threading.Lock()
    total_crawled = 0

    visited.add(root_url)
    current_layer = [(root_url, 0, None)]

    print(f"\n  🌐 root: {root_url}  (depth={max_depth}, max_pages={CONFIG.MAX_PAGES}, workers={CONFIG.NUM_WORKERS})")

    for depth in range(max_depth + 1):
        if not current_layer:
            break
        if CONFIG.MAX_PAGES > 0 and total_crawled >= CONFIG.MAX_PAGES:
            print(f"  ⚠️  已達頁數上限 {CONFIG.MAX_PAGES}")
            break

        remaining     = CONFIG.MAX_PAGES - total_crawled if CONFIG.MAX_PAGES > 0 else len(current_layer)
        current_layer = current_layer[:remaining]

        print(f"\n  [depth={depth}] {len(current_layer)} 頁待爬")

        chunks = [current_layer[i::CONFIG.NUM_WORKERS] for i in range(CONFIG.NUM_WORKERS)]
        chunks = [c for c in chunks if c]

        layer_results = []
        with ThreadPoolExecutor(max_workers=CONFIG.NUM_WORKERS) as executor:
            futures = {
                executor.submit(run_worker, chunk, root_info, wid, print_lock): wid
                for wid, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                try:
                    layer_results.extend(future.result())
                except Exception as e:
                    with print_lock:
                        print(f"  ⚠️  Worker 例外: {e}")

        # ── Drop reason 統計（每個 depth layer 結束後印出）────────────────
        layer_drop_reasons: dict = {}
        for _, _, _, r in layer_results:
            for reason in r["drop"].values():
                layer_drop_reasons[reason] = layer_drop_reasons.get(reason, 0) + 1

        if layer_drop_reasons:
            total_d = sum(layer_drop_reasons.values())
            top = sorted(layer_drop_reasons.items(), key=lambda x: x[1], reverse=True)
            parts = "  |  ".join(f"{reason}={cnt}" for reason, cnt in top[:10])
            print(f"\n  [depth={depth} drop] 共 {total_d} 個 URL 被丟棄：")
            print(f"    {parts}")
            # 若有超過 10 種原因，補印其餘
            if len(top) > 10:
                rest = "  ".join(f"{r}={c}" for r, c in top[10:])
                print(f"    (其他) {rest}")
        # ─────────────────────────────────────────────────────────────

        next_layer = []
        for url, dep, parent, result in layer_results:
            total_crawled += 1
            pages[url] = {
                "depth":    dep,
                "title":    result["title"],
                "parent":   parent,
                "children": [],
                "external": sorted(result["external"]),
                "error":    result["error"],
            }
            all_external.update(result["external"])
            all_drop.update(result["drop"])

            for child_url in sorted(result["keep"]):
                if child_url not in visited:
                    visited.add(child_url)
                    if dep + 1 <= max_depth:
                        next_layer.append((child_url, dep + 1, url))
                    pages[url]["children"].append(child_url)
                else:
                    if child_url not in pages[url]["children"]:
                        pages[url]["children"].append(child_url)

        current_layer = next_layer
    '''
    # test
    print(root_url)
    print("\nkeep",len(pages))
    for a in pages:
        print(a)
    print("\nexternal",len(all_external))
    for a in all_external:
        print(a)
    print("\ndrop",len(all_drop))
    for a in all_drop:
        print(a)
    '''
    return {
        "root":              root_url,
        "pages":             pages,
        "all_internal_urls": sorted(pages.keys()),
        "all_external_urls": sorted(all_external),
        "all_dropped_urls":  all_drop,
        "stats": {
            "total_crawled":  total_crawled,
            "total_dropped":  len(all_drop),
            "error_count":    sum(1 for d in pages.values() if d["error"]),
            "external_count": len(all_external),
            "max_depth":      max((d["depth"] for d in pages.values()), default=0),
            "crawled_at":     datetime.now().isoformat(),
        },
    }


# ══════════════════════════════════════════════
# ★ 多學校爬取：回傳 classifier.py 的 schools 格式
# ══════════════════════════════════════════════

# 改這裡，不要傳入school list，傳入單一學校
# 回傳單一學校結果，並存入json
# 呼叫下一個階段評分
def crawl_one_school(one_school: list, max_depth: int = CONFIG.MAX_DEPTH, output_dir: str = "url_result") -> str:
    """
    輸入：SCHOOLS 變數
    輸出：{"school_id": "...", "urls": [...]}
    結果存到 output_dir/{school_id_slug}/ 資料夾
      - {school_id}_keep.json
      - {school_id}_drop.json
    """

    school_id = one_school["school_id"]
    roots     = one_school["roots"]

    print(f"\n{'='*60}")
    print(f"  SCHOOL: {school_id}  ({len(roots)} roots)")
    print(f"{'='*60}")

    keep_urls: set[str] = set()
    ex_urls:   set[str] = set()
    drop_urls: set[str] = set()

    for root_url in roots:
        tree = crawl_tree(root_url, max_depth=max_depth)
        keep_urls.update(tree["all_internal_urls"])
        ex_urls.update(tree["all_external_urls"])
        drop_urls.update(tree["all_dropped_urls"])

        s = tree["stats"]
        print(f"  ✅  root 完成: {root_url}")
        print(f"  爬取={s['total_crawled']}  丟棄={s['total_dropped']}  錯誤={s['error_count']}")

    print(f"\n  → {school_id} 共收集 {len(keep_urls)} 個 URL")

    # ── 存檔 ────────────────────────────────────────────────
    # 用 school_id 當資料夾名稱（把特殊字元換成底線，避免路徑問題）
    folder_name = re.sub(r'[^\w\-]', '_', school_id)
    school_dir  = os.path.join(output_dir, folder_name)
    os.makedirs(school_dir, exist_ok=True)

    keep_payload = {
        "school_id": school_id,
        "urls": sorted(keep_urls),
    }
    drop_payload = {
        "school_id": school_id,
        "urls": sorted(ex_urls | drop_urls),   # external + dropped 合併
    }

    keep_path = os.path.join(school_dir, f"{school_id}_keep.json")
    drop_path = os.path.join(school_dir, f"{school_id}_drop.json")

    with open(keep_path, "w", encoding="utf-8") as f:
        json.dump(keep_payload, f, ensure_ascii=False, indent=2)

    with open(drop_path, "w", encoding="utf-8") as f:
        json.dump(drop_payload, f, ensure_ascii=False, indent=2)

    print(f"  💾  已存至 {school_dir}/")
    # ────────────────────────────────────────────────────────

    return "one school finish"


# ══════════════════════════════════════════════
# 輸出
# ══════════════════════════════════════════════

def export_as_python(result_schools: list, path: str = "crawled_schools.py"):
    """輸出可直接貼入 classifier.py 的 Python 程式碼"""
    lines = ["# Auto-generated by crawler.py\n",
             "# 可直接貼入 classifier.py 取代 schools 變數\n\n",
             "schools = [\n"]
    for school in result_schools:
        lines.append(f'    {{\n')
        lines.append(f'        "school_id": "{school["school_id"]}",\n')
        lines.append(f'        "urls": [\n')
        for url in school["urls"]:
            lines.append(f'            "{url}",\n')
        lines.append(f'        ]\n')
        lines.append(f'    }},\n')
    lines.append("]\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"\n📄 Python 格式 → {path}")


def export_as_json(result_schools: list, path: str = "crawled_schools.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result_schools, f, ensure_ascii=False, indent=2)
    print(f"📦 JSON 格式   → {path}")


# ══════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════

def main():
    max_depth = int(sys.argv[1]) if len(sys.argv) > 1 else CONFIG.MAX_DEPTH

    print("=" * 60)
    print("CRAWLER  —  多學校多 root，輸出 classifier.py 格式")
    print("=" * 60)
    print(f"學校數量: {len(CONFIG.SCHOOLS)}  最大深度: {max_depth}")

    result_schools = crawl_one_schools(CONFIG.SCHOOLS, max_depth=max_depth)

    print(result_schools)

    # 印出摘要
    print(f"\n{'='*60}")
    print("最終結果摘要")
    print(f"{'='*60}")
    for school in result_schools:
        print(f"  {school['school_id']:15s}  {len(school['urls']):4d} URLs")

    #export_as_python(result_schools)
    #export_as_json(result_schools)

    # 同時印出可直接複製的 Python 片段
    #print("★ 可直接複製貼入 classifier.py 的內容：")
    #print("=" * 60)
    #print("schools = [")
    #for school in result_schools:
    #    print(f'    {{')
    #    print(f'        "school_id": "{school["school_id"]}",')
    #    print(f'        "urls": [')
    #    for url in school["urls"]:
    #        print(f'            "{url}",')
    #    print(f'        ]')
    #    print(f'    }},')
    #print("]")

    print("\n✅ 完成！")


if __name__ == "__main__":
#    main()
    input = "https://cse.ucsd.edu/graduate"
    input = normalize_url(input)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        block_resources(context)
        page = context.new_page()
        crawl_tree(input,5)



