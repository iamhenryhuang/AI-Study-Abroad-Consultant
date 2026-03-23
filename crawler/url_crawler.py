"""
crawler.py  —  多學校多 root 遞迴爬取，輸出格式相容 classifier.py 的 schools 變數

使用方式：
    python crawler.py
"""

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

def normalize_url(url: str) -> str:
    try:
        p    = urlparse(url)
        path = p.path.rstrip("/") or "/"
        return urlunparse((
            p.scheme.lower(), p.netloc.lower(),
            path, "", "" if CONFIG.STRIP_QUERY else p.query, ""
        ))
    except Exception:
        return url


def get_root_info(url: str) -> dict:
    p = urlparse(url)
    return {"netloc": p.netloc.lower(), "root_path": p.path}


def is_same_root(url: str, root_info: dict) -> bool:
    try:
        p = urlparse(url)
        return (
            p.netloc.lower() == root_info["netloc"]
            and p.path.rstrip("/").startswith(root_info["root_path"].rstrip("/"))
        )
    except Exception:
        return False


def has_ignored_extension(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in IGNORED_EXTENSIONS)


# ══════════════════════════════════════════════
# 過濾
# ══════════════════════════════════════════════

def filter_url(url: str) -> tuple[bool, str]:
    if not url.startswith(("http://", "https://")):
        return False, "non-http"
    if has_ignored_extension(url):
        return False, "ext"
    full_low = url.lower()
    for frag in BLACKLIST_PATH_FRAGMENTS:
        if frag in full_low:
            return False, f"black:{frag}"
    return True, "keep"


# ══════════════════════════════════════════════
# 連結擷取
# ══════════════════════════════════════════════

def extract_links(page, current_url: str, root_info: dict) -> dict:
    raw_links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
                   .map(a => a.href)
                   .filter(h => h && h.length > 0)
    """) or []

    keep     = set()
    drop     = {}
    external = set()

    for raw in raw_links:
        try:
            normalized = normalize_url(urljoin(current_url, raw))
        except Exception:
            continue

        if not is_same_root(normalized, root_info):
            external.add(normalized)
            continue

        ok, reason = filter_url(normalized)
        if ok:
            keep.add(normalized)
        else:
            drop[normalized] = reason

    return {"keep": keep, "drop": drop, "external": external}


# ══════════════════════════════════════════════
# 單頁爬取
# ══════════════════════════════════════════════

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
        page.wait_for_timeout(600)
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

def crawl_all_schools(schools: list, max_depth: int = CONFIG.MAX_DEPTH) -> list:
    """
    輸入：SCHOOLS 變數
    輸出：[{"school_id": "...", "urls": [...]}, ...]
    與 classifier.py 的 schools / school_2 / school_3 格式完全相同
    """
    result_schools = []

    for school in schools:
        school_id = school["school_id"]
        roots     = school["roots"]

        print(f"\n{'='*60}")
        print(f"  SCHOOL: {school_id}  ({len(roots)} roots)")
        print(f"{'='*60}")

        # 收集這所學校所有 root 爬到的 URL（去重）
        all_urls: set[str] = set()

        for root_url in roots:
            tree = crawl_tree(root_url, max_depth=max_depth)
            all_urls.update(tree["all_internal_urls"])

            s = tree["stats"]
            print(f"  ✅  root 完成: {root_url}")
            print(f"      爬取={s['total_crawled']}  丟棄={s['total_dropped']}  錯誤={s['error_count']}")

        result_schools.append({
            "school_id": school_id,
            "urls": sorted(all_urls),
        })

        print(f"\n  → {school_id} 共收集 {len(all_urls)} 個 URL")

    return result_schools


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

    result_schools = crawl_all_schools(CONFIG.SCHOOLS, max_depth=max_depth)

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


#if __name__ == "__main__":
#    main()


