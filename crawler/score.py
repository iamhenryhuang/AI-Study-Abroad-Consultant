import json
import os
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from url_crawler import crawl_all_schools
from setting.parameter import THRESHOLDS, PAGE_HINTS, MINUS_KEYWORDS, URL_PATH_HINTS


MAX_FULL_TEXT_CHARS = 200_000
MAX_RETRY           = 2
RETRY_WAIT_MS       = 2000
NUM_THREADS         = 4          # ← 依機器調整

H3_WEIGHT_H3    = 3
H3_WEIGHT_MINUS = 1.5
EMPHASIS_MAX_BONUS = 2
DENSITY_MIN_COUNT  = 3
DENSITY_BONUS      = 1
DENSITY_MAX_BONUS  = 2
NAV_WEIGHT         = 0.3




# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def normalize_spaces(text):
    return " ".join(text.split()) if text else ""

def parse_url_path(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).path.lower()
    except Exception:
        return url.lower()

def score_url_path(url: str) -> dict:
    path = parse_url_path(url)
    bonuses = {pt: 0 for pt in PAGE_HINTS}
    for page_type, hints in URL_PATH_HINTS.items():
        for segment, pts in hints:
            if segment in path:
                bonuses[page_type] += pts
    return bonuses


# ──────────────────────────────────────────────
# 擷取網頁全文（雜訊已在 JS 層清除）
# ──────────────────────────────────────────────

def extract_page_content_with_js(page, url, extra_wait_ms=3000):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(extra_wait_ms)
        page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); }")
        page.wait_for_timeout(1500)
        page.evaluate("() => { window.scrollTo(0, 0); }")
        page.wait_for_timeout(500)

        title    = page.title() or ""
        h1_list  = [normalize_spaces(x) for x in page.locator("h1").all_inner_texts() if normalize_spaces(x)]
        h2_list  = [normalize_spaces(x) for x in page.locator("h2").all_inner_texts() if normalize_spaces(x)]
        h3_list  = [normalize_spaces(x) for x in page.locator("h3").all_inner_texts() if normalize_spaces(x)]

        visible_text  = normalize_spaces(page.evaluate("""
            () => {
                function isVisible(el) {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                }
                function walk(el) {
                    let text = '';
                    for (const child of el.childNodes) {
                        if (child.nodeType === Node.TEXT_NODE) {
                            text += ' ' + child.textContent;
                        } else if (child.nodeType === Node.ELEMENT_NODE && isVisible(child)) {
                            text += ' ' + walk(child);
                        }
                    }
                    return text;
                }
                return walk(document.body);
            }
        """) or "")

        nav_text      = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('nav, [role="navigation"], header'))
                       .map(el => el.innerText || el.textContent || '').join(' ')
        """) or "")

        alt_texts     = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('img[alt]'))
                       .map(img => img.getAttribute('alt') || '').join(' ')
        """) or "")

        emphasis_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('strong, em, b'))
                       .map(el => el.innerText || el.textContent || '').join(' ')
        """) or "")

        meta_desc     = normalize_spaces(page.evaluate("""
            () => { const el = document.querySelector('meta[name="description"]');
                    return el ? el.getAttribute('content') || '' : ''; }
        """) or "")

        li_texts      = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('li'))
                       .map(li => li.innerText || li.textContent || '').join(' ')
        """) or "")

        table_texts   = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('td, th'))
                       .map(el => el.innerText || el.textContent || '').join(' ')
        """) or "")

        shadow_texts  = normalize_spaces(page.evaluate("""
            () => {
                function extractShadow(root) {
                    let text = '';
                    root.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot) text += ' ' + extractShadow(el.shadowRoot);
                    });
                    if (root !== document) text += ' ' + (root.textContent || '');
                    return text;
                }
                return extractShadow(document);
            }
        """) or "")

        iframe_texts = ""
        try:
            iframe_texts = normalize_spaces(page.evaluate("""
                () => {
                    let text = '';
                    document.querySelectorAll('iframe').forEach(iframe => {
                        try {
                            const body = iframe.contentDocument && iframe.contentDocument.body;
                            if (body) text += ' ' + (body.innerText || body.textContent || '');
                        } catch(e) {}
                    });
                    return text;
                }
            """) or "")
        except Exception:
            pass

        full_text = normalize_spaces(" ".join([
            visible_text, meta_desc, alt_texts,
            li_texts, table_texts, shadow_texts, iframe_texts,
        ]))
        if len(full_text) > MAX_FULL_TEXT_CHARS:
            full_text = full_text[:MAX_FULL_TEXT_CHARS]

        return {
            "title": title, "h1_list": h1_list, "h2_list": h2_list, "h3_list": h3_list,
            "full_text": full_text, "nav_text": nav_text, "emphasis_text": emphasis_text,
            "error": None,
        }

    except Exception as e:
        return {
            "title": "", "h1_list": [], "h2_list": [], "h3_list": [],
            "full_text": "", "nav_text": "", "emphasis_text": "", "error": str(e),
        }


# ──────────────────────────────────────────────
# 評分
# ──────────────────────────────────────────────

def score_page(title, h1_list, h2_list, h3_list,
               full_text, nav_text, emphasis_text, url):
    content     = full_text.lower()
    title_lower = title.lower()
    h1_content  = " ".join(h1_list).lower()
    h2_content  = " ".join(h2_list).lower()
    h3_content  = " ".join(h3_list).lower()
    nav_content = nav_text.lower()
    em_content  = emphasis_text.lower()
    url_bonuses = score_url_path(url)
    scores = {}

    for page_type, keywords in PAGE_HINTS.items():
        score          = url_bonuses.get(page_type, 0)
        emphasis_bonus = 0
        density_bonus  = 0

        for kw in keywords:
            if kw in content:
                score += 1
                count = content.count(kw)
                if count >= DENSITY_MIN_COUNT:
                    density_bonus = min(density_bonus + DENSITY_BONUS, DENSITY_MAX_BONUS)
            if kw in em_content and emphasis_bonus < EMPHASIS_MAX_BONUS:
                emphasis_bonus += 1
            if kw in nav_content:
                score += 1 * NAV_WEIGHT
            if kw in h2_content:
                score += 2.5 + (1 if page_type == "faq" else 0)
            if kw in h3_content:
                score += H3_WEIGHT_H3
            if kw in h1_content:
                score += 4.5 + (1.5 if page_type == "faq" else 0)
            if kw in title_lower:
                score += 8.5 + (2 if page_type == "faq" else 0)

        score += emphasis_bonus + density_bonus

        for kw in MINUS_KEYWORDS:
            if kw in content:      score -= 0.5
            if kw in nav_content:  score -= 0.5 * NAV_WEIGHT
            if kw in h2_content:   score -= 4
            if kw in h3_content:   score -= H3_WEIGHT_MINUS
            if kw in h1_content:   score -= 5
            if kw in title_lower:  score -= 8

        scores[page_type] = round(score, 2)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_type, best_score = sorted_scores[0]
    return scores, best_type, best_score


# ──────────────────────────────────────────────
# 分類（帶重試）
# ──────────────────────────────────────────────

def classify_url_with_browser(page, url, thresholds=THRESHOLDS, school_id=""):
    last_error = None
    extracted  = None

    for attempt in range(1, MAX_RETRY + 2):
        if attempt > 1:
            print(f"   ↺ retry {attempt-1}/{MAX_RETRY} [{school_id}] {url}")
            page.wait_for_timeout(RETRY_WAIT_MS)
        extracted = extract_page_content_with_js(page, url)
        if not extracted["error"]:
            break
        last_error = extracted["error"]

    if extracted["error"]:
        return {
            "school_id": school_id, "url": url, "error": last_error,
            "type": "error", "score": 0, "best_type": None, "best_score": 0,
            "matched_types": [], "scores": {}, "title": "",
            "h1_list": [], "h2_list": [], "h3_list": [],
            "text_preview": "", "timestamp": datetime.now().isoformat(),
        }

    title         = extracted["title"]
    h1_list       = extracted["h1_list"]
    h2_list       = extracted["h2_list"]
    h3_list       = extracted["h3_list"]
    full_text     = extracted["full_text"]
    nav_text      = extracted["nav_text"]
    emphasis_text = extracted["emphasis_text"]

    scores, best_type, best_score = score_page(
        title, h1_list, h2_list, h3_list, full_text, nav_text, emphasis_text, url
    )

    matched_types = [
        {"type": pt, "score": sc}
        for pt, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        if sc >= thresholds.get(pt, 10)
    ]

    if not matched_types:
        final_type, final_score = "other", best_score
    elif len(matched_types) == 1:
        final_type, final_score = matched_types[0]["type"], matched_types[0]["score"]
    else:
        final_type, final_score = "multiple", best_score

    return {
        "school_id": school_id, "url": url,
        "type": final_type, "score": final_score,
        "best_type": best_type, "best_score": best_score,
        "matched_types": matched_types, "scores": scores,
        "title": title, "h1_list": h1_list, "h2_list": h2_list, "h3_list": h3_list,
        "full_text": full_text,  
        "text_preview": full_text[:1000], "error": None,
        "timestamp": datetime.now().isoformat(),
    }


# ──────────────────────────────────────────────
# 簡短 print
# ──────────────────────────────────────────────

def print_result(result, thresholds=THRESHOLDS):
    sid   = result.get("school_id", "")
    url   = result["url"]
    title = result.get("title", "")

    # 第一行：school / url / title
    print(f"[{sid}] {url}  |  {title}")

    if result.get("error"):
        print(f"  ERROR: {result['error']}")
        return

    # 第二行：h1 / h2 / h3（各取前 3 個）
    h1 = " / ".join(result["h1_list"][:3])
    h2 = " / ".join(result["h2_list"][:3])
    h3 = " / ".join(result["h3_list"][:3])
    heads = []
    if h1: heads.append(f"h1:{h1}")
    if h2: heads.append(f"h2:{h2}")
    if h3: heads.append(f"h3:{h3}")
    if heads:
        print("  " + "  |  ".join(heads))

    # 第三行：最終分類 + 各類別分數
    scores_str = "  ".join(
        f"{pt}={sc}" for pt, sc in
        sorted(result["scores"].items(), key=lambda x: x[1], reverse=True)
    )
    print(f"  → {result['type']} (best:{result['best_type']}={result['best_score']})  [{scores_str}]")
    print()


# ──────────────────────────────────────────────
# 單一 thread 的工作單元
# ──────────────────────────────────────────────

def worker(school_urls_batch, playwright_instance):
    """
    school_urls_batch: list of (school_id, url)
    每條 thread 建立自己的 context + page，互不干擾。
    """
    browser = playwright_instance.chromium.launch(headless=True)
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
    page    = context.new_page()
    results = []

    for school_id, url in school_urls_batch:
        result = classify_url_with_browser(page, url, thresholds=THRESHOLDS, school_id=school_id)
        print_result(result)
        results.append(result)

    context.close()
    browser.close()
    return results


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

def main():
    print(f"\n=== Batch Classification  threads={NUM_THREADS} ===\n")

    target_schools = crawl_all_schools(SCHOOLS, max_depth=10)

    # 展平成 (school_id, url) 列表
    flat = [
        (school["school_id"], url)
        for school in target_schools
        for url in school["urls"]
    ]

    # 切分成 NUM_THREADS 份
    chunks = [flat[i::NUM_THREADS] for i in range(NUM_THREADS)]

    all_results = []

    with sync_playwright() as p:
        with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
            futures = {executor.submit(worker, chunk, p): i for i, chunk in enumerate(chunks)}
            for future in as_completed(futures):
                all_results.extend(future.result())

    print(f"\n✅ 全部完成！共處理 {len(all_results)} 個 URL")


# if __name__ == "__main__":
#     main()