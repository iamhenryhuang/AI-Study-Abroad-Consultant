import json
import os
import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from url_c_fast import crawl_all_schools

SCHOOLS = [
    {
        "school_id": "ucla",
        "roots": [
            "https://grad.ucla.edu/admissions",
            "https://grad.ucla.edu/funding",
        ],
    },
    {
        "school_id": "ucsd",
        "roots": [
            "https://cse.ucsd.edu/graduate/admissions",
        ],
    },
    {
        "school_id": "WashU",
        "roots": [
         "https://engineering.washu.edu/academics/graduate-admissions/",
        ],
    },
]
SCHOOLS_2 = [
    {
        "school_id": "standford",
        "roots": [
            "https://www.cs.stanford.edu/admissions",
        ],
    },
    {
        "school_id": "cmu",
        "roots": [
            "https://www.cs.cmu.edu/academics/graduate-admissions",
        ],
    },
    {
        "school_id": "gatech",
        "roots": [
         "https://grad.gatech.edu/admissions",
         "https://www.cc.gatech.edu/ms-computer-science-admissions-faq",
         #"https://omscs.gatech.edu/",
        ],
    },
]

# ── 各類別獨立門檻，自行調整 ──────────────────────
THRESHOLDS = {
    "admissions": 25,
    "program":    15,
    "tuition":    10,
    "faq":        13,
}
# ─────────────────────────────────────────────────

PAGE_HINTS = {
    "admissions": [
        "admission", "admissions", "apply", "application",
        "deadline", "deadlines", "requirement", "requirements",
        "eligibility", "how to apply", "gre", "toefl", "ielts",
        "PREREQUISITES","PREREQUISITE","prepare","enroll","enrollment"
        "Criteria"
    ],
    "program": [
        "master of science", "ms in computer science",
        "program overview", "curriculum", "degree requirements",
        "course", "program","degree"
    ],
    "tuition": [
        "tuition", "fees", "cost", "costs", "scholarship",
        "financial aid", "financial"
    ],
    "faq": [
        "frequently asked questions", "faqs","fellowship",
        "questions","question","apply"
    ],
}

minus_keywords = [
    "news", "events", "faculty", "life", "funding",
    "research","alumni", "undergraduate", "undergraduates",
    "awards", "award", "stories", "clubs", "club","facilities"
    "staff","music","humanities","life","arts","art","architecture","nursing",
    "public-affairs","health","social","Biology","Medicine",
]

URL_PATH_HINTS = {
    "admissions": [
        ("admissions", 2), ("admission", 2), ("apply", 3),
        ("application", 4), ("how-to-apply", 5), ("how_to_apply", 5),
        ("checklist", 4), ("requirements", 5), ("eligibility", 4),
        ("program",3),("undergradute",-5),("phd",-3)
    ],
    "program": [
        ("grad_cs", 5), ("graduate", 3), ("ms-program", 5),
        ("ms_program", 5), ("degree", 4), ("curriculum", 4),
        ("degree-programs", 5), ("degree_programs", 5),("undergradute",-5)
    ],
    "tuition": [
        ("tuition", 10), ("financial", 10), ("financial-assistance", 5),
        ("financial_assistance", 5), ("cost", 5), ("fees", 5),
        ("scholarship", 10), ("fellowships", 10),
    ],
    "faq": [
        ("faq", 7), ("faqs", 7), ("faq-applicants", 6),
        ("frequently-asked", 7), ("frequently_asked", 6),
    ],
}

MAX_FULL_TEXT_CHARS = 200_000
MAX_RETRY = 2
RETRY_WAIT_MS = 2000

H3_WEIGHT_H3    = 3
H3_WEIGHT_MINUS = 1.5

EMPHASIS_MAX_BONUS = 2

DENSITY_MIN_COUNT = 3
DENSITY_BONUS     = 1
DENSITY_MAX_BONUS = 2

NAV_WEIGHT = 0.3


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def normalize_spaces(text):
    if not text:
        return ""
    return " ".join(text.split())


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
# 擷取網頁全文
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

        title = page.title() or ""

        h1_list = [normalize_spaces(x) for x in page.locator("h1").all_inner_texts() if normalize_spaces(x)]
        h2_list = [normalize_spaces(x) for x in page.locator("h2").all_inner_texts() if normalize_spaces(x)]
        h3_list = [normalize_spaces(x) for x in page.locator("h3").all_inner_texts() if normalize_spaces(x)]

        visible_text = normalize_spaces(page.evaluate("""
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

        nav_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('nav, [role="navigation"], header'))
                       .map(el => el.innerText || el.textContent || '')
                       .join(' ')
        """) or "")

        alt_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('img[alt]'))
                       .map(img => img.getAttribute('alt') || '')
                       .join(' ')
        """) or "")

        emphasis_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('strong, em, b'))
                       .map(el => el.innerText || el.textContent || '')
                       .join(' ')
        """) or "")

        meta_desc = normalize_spaces(page.evaluate("""
            () => {
                const el = document.querySelector('meta[name="description"]');
                return el ? el.getAttribute('content') || '' : '';
            }
        """) or "")

        li_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('li'))
                       .map(li => li.innerText || li.textContent || '')
                       .join(' ')
        """) or "")

        table_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('td, th'))
                       .map(el => el.innerText || el.textContent || '')
                       .join(' ')
        """) or "")

        shadow_texts = normalize_spaces(page.evaluate("""
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
            visible_text,
            meta_desc,
            alt_texts,
            li_texts,
            table_texts,
            shadow_texts,
            iframe_texts,
        ]))

        if len(full_text) > MAX_FULL_TEXT_CHARS:
            full_text = full_text[:MAX_FULL_TEXT_CHARS]

        return {
            "title": title,
            "h1_list": h1_list,
            "h2_list": h2_list,
            "h3_list": h3_list,
            "full_text": full_text,
            "nav_text": nav_text,
            "emphasis_text": emphasis_text,
            "error": None,
        }

    except Exception as e:
        return {
            "title": "",
            "h1_list": [],
            "h2_list": [],
            "h3_list": [],
            "full_text": "",
            "nav_text": "",
            "emphasis_text": "",
            "error": str(e),
        }


# ──────────────────────────────────────────────
# 評分
# ──────────────────────────────────────────────

def score_page(title, h1_list, h2_list, h3_list,
               full_text, nav_text, emphasis_text, url):

    content       = full_text.lower()
    title_lower   = title.lower()
    h1_content    = " ".join(h1_list).lower()
    h2_content    = " ".join(h2_list).lower()
    h3_content    = " ".join(h3_list).lower()
    nav_content   = nav_text.lower()
    em_content    = emphasis_text.lower()

    url_bonuses = score_url_path(url)

    scores = {}

    for page_type, keywords in PAGE_HINTS.items():
        score = 0

        score += url_bonuses.get(page_type, 0)

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
                score += 2.5
                if page_type == "faq":
                    score += 1

            if kw in h3_content:
                score += H3_WEIGHT_H3
                if page_type == "faq":
                    score += 0

            if kw in h1_content:
                score += 4.5
                if page_type == "faq":
                    score += 1.5

            if kw in title_lower:
                score += 8.5
                if page_type == "faq":
                    score += 2

        score += emphasis_bonus
        score += density_bonus

        for kw in minus_keywords:
            if kw in content:
                score -= 0.5
            if kw in nav_content:
                score -= 0.5 * NAV_WEIGHT
            if kw in h2_content:
                score -= 4
            if kw in h3_content:
                score -= H3_WEIGHT_MINUS
            if kw in h1_content:
                score -= 5
            if kw in title_lower:
                score -= 8

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
            print(f"   ↺ retry {attempt - 1}/{MAX_RETRY} for {url}")
            page.wait_for_timeout(RETRY_WAIT_MS)

        extracted = extract_page_content_with_js(page, url)

        if not extracted["error"]:
            break
        last_error = extracted["error"]

    if extracted["error"]:
        return {
            "school_id":    school_id,
            "url":          url,
            "error":        last_error,
            "type":         "error",
            "score":        0,
            "best_type":    None,
            "best_score":   0,
            "matched_types": [],
            "scores":       {},
            "title":        "",
            "h1_list":      [],
            "h2_list":      [],
            "h3_list":      [],
            "text_preview": "",
            "timestamp":    datetime.now().isoformat(),
        }

    title         = extracted["title"]
    h1_list       = extracted["h1_list"]
    h2_list       = extracted["h2_list"]
    h3_list       = extracted["h3_list"]
    full_text     = extracted["full_text"]
    nav_text      = extracted["nav_text"]
    emphasis_text = extracted["emphasis_text"]

    scores, best_type, best_score = score_page(
        title, h1_list, h2_list, h3_list,
        full_text, nav_text, emphasis_text, url
    )

    # ── 各類別用自己的門檻判斷是否命中 ──
    matched_types = [
        {"type": pt, "score": sc}
        for pt, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        if sc >= thresholds.get(pt, 10)   # 找不到時預設 10
    ]

    if not matched_types:
        final_type  = "other"
        final_score = best_score
    elif len(matched_types) == 1:
        final_type  = matched_types[0]["type"]
        final_score = matched_types[0]["score"]
    else:
        final_type  = "multiple"
        final_score = best_score

    return {
        "school_id":     school_id,
        "url":           url,
        "type":          final_type,
        "score":         final_score,
        "best_type":     best_type,
        "best_score":    best_score,
        "matched_types": matched_types,
        "scores":        scores,
        "title":         title,
        "h1_list":       h1_list,
        "h2_list":       h2_list,
        "h3_list":       h3_list,
        "text_preview":  full_text[:1000],
        "error":         None,
        "timestamp":     datetime.now().isoformat(),
    }


# ──────────────────────────────────────────────
# 印出結果
# ──────────────────────────────────────────────

def print_result(result, thresholds=THRESHOLDS):
    school_id = result.get("school_id", "")
    url       = result["url"]

    print(f"[{school_id}] {url}")

    if result.get("error"):
        print(f" → type : {result['type']} | score: {result['score']}")
        print(f" → ERROR: {result['error']}")
        print()
        return

    print(f" → final type : {result['type']}")
    print(f" → best type  : {result['best_type']} | best score: {result['best_score']}")
    print(f" → title      : {result['title']}")

    if result["h1_list"]:
        print(" → h1:")
        for x in result["h1_list"]:
            print(f"    - {x}")

    if result["h2_list"]:
        print(" → h2:")
        for x in result["h2_list"][:10]:
            print(f"    - {x}")

    if result["h3_list"]:
        print(" → h3:")
        for x in result["h3_list"][:10]:
            print(f"    - {x}")

    if result["matched_types"]:
        threshold_info = ", ".join(f"{pt}={v}" for pt, v in thresholds.items())
        print(f" → matched types (thresholds: {threshold_info}):")
        for item in result["matched_types"]:
            print(f"    - {item['type']}: {item['score']} (threshold={thresholds.get(item['type'], 10)})")
    else:
        print(f" → matched types: none")

    print(" → all scores:")
    for pt, sc in sorted(result["scores"].items(), key=lambda x: x[1], reverse=True):
        print(f"    - {pt}: {sc}  (threshold={thresholds.get(pt, 10)})")

    print(" → text preview:")
    print(f"    {result['text_preview'][:8000]}...")
    print()


# ──────────────────────────────────────────────
# 執行結束後印出摘要
# ──────────────────────────────────────────────

def print_summary(all_results, thresholds=THRESHOLDS):
    total   = len(all_results)
    errors  = [r for r in all_results if r.get("error")]
    others  = [r for r in all_results if r.get("type") == "other"]
    matched = [r for r in all_results if r.get("matched_types")]

    threshold_info = ", ".join(f"{pt}={v}" for pt, v in thresholds.items())

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total URLs  : {total}")
    print(f"  Errors      : {len(errors)}")
    print(f"  Thresholds  : {threshold_info}")
    print(f"  Matched     : {len(matched)}")
    print(f"  Other / miss: {len(others)}")

    from collections import Counter
    type_counter = Counter(r.get("type") for r in all_results)
    print("\n  Type breakdown:")
    for t, cnt in type_counter.most_common():
        print(f"    {t:12s}: {cnt}")

    if others:
        print(f"\n  URLs classified as 'other' (best_score shown):")
        for r in others:
            print(f"    [{r['school_id']}] {r['url']}")
            print(f"      best: {r['best_type']} = {r['best_score']}")
    print()


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

def main():
    print("\n=== Batch Classification (JS-enabled with Playwright) ===\n")

    target_schools = crawl_all_schools(SCHOOLS_2, max_depth=10)

    all_results = []

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

        for school in target_schools:
            school_id = school["school_id"]
            urls      = school["urls"]

            print(f"\n====== SCHOOL: {school_id} ======\n")

            for url in urls:
                result = classify_url_with_browser(
                    page, url, thresholds=THRESHOLDS, school_id=school_id
                )
                print_result(result, thresholds=THRESHOLDS)
                all_results.append(result)

        context.close()
        browser.close()

    # print_summary(all_results, thresholds=THRESHOLDS)
    print("✅ 全部完成！")


if __name__ == "__main__":
    main()