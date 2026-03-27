import json
import os
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from url_crawler import crawl_one_school
from setting.parameter import THRESHOLDS, PAGE_HINTS, MINUS_KEYWORDS, URL_PATH_HINTS


MAX_FULL_TEXT_CHARS = 2_000_000
MAX_RETRY           = 2
RETRY_WAIT_MS       = 2000
NUM_THREADS         = 3          # ← 依機器調整

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

        # ── STEP 1：慢速捲動全頁，觸發 lazy-load / scroll-reveal ──────────
        page.evaluate("""async () => {
            await new Promise(resolve => {
                const distance = 300;          // 每次捲動距離 px
                const delay    = 300;          // 每步間隔 ms
                let current    = 0;
                const total    = document.body.scrollHeight;
                const timer = setInterval(() => {
                    window.scrollBy(0, distance);
                    current += distance;
                    if (current >= total) {
                        clearInterval(timer);
                        resolve();
                    }
                }, delay);
            });
        }""")
        page.wait_for_timeout(1000)

        # ── STEP 2：逐一捲動到每個可展開觸發元素後點擊 ───────────────────
        #    每個元素先 scrollIntoView 到視窗中央，再 click()
        #    確保不論元素在頁面哪個位置都能觸發
        page.evaluate("""() => {
            const CLICK_SELECTORS = [
                // ARIA 語意：未展開的可互動元素
                '[aria-expanded="false"]',
                '[aria-selected="false"]',
                '[aria-checked="false"]',

                // Bootstrap / 常見 class-based accordion
                'button.accordion-button',
                '.accordion-header button',
                '.accordion-item .accordion-button',
                '[data-bs-toggle="collapse"]',
                '[data-toggle="collapse"]',
                '[data-bs-toggle="tab"]',
                '[data-toggle="tab"]',
                '[data-bs-toggle="pill"]',

                // 通用展開按鈕 class 關鍵字
                'button[class*="accordion"]',
                'button[class*="toggle"]',
                'button[class*="collapse"]',
                'button[class*="expand"]',
                'button[class*="show"]',
                'button[class*="open"]',
                '[class*="toggle-btn"]',
                '[class*="collapsible"]',
                '[class*="expand-btn"]',
                '[class*="read-more"]',
                '[class*="readmore"]',
                '[class*="show-more"]',
                '[class*="load-more"]',

                // <details><summary>
                'summary',

                // role=button 未展開
                '[role="button"][aria-expanded="false"]',
                '[role="tab"]',
                '[role="treeitem"]',

                // FAQ / 問答常見模式
                '.faq-question',
                '.faq-title',
                '.faq-toggle',
                '.question',
                '.q-item',
                '[class*="faq"] button',
                '[class*="faq"] [role="button"]',

                // 其他雜項
                '[data-target]',
                '[href="#"]',
            ];

            // 用 Set 去重，避免同一元素被多個 selector 重複點擊
            const clicked = new WeakSet();

            CLICK_SELECTORS.forEach(sel => {
                let els;
                try { els = document.querySelectorAll(sel); } catch(e) { return; }
                els.forEach(el => {
                    if (clicked.has(el)) return;
                    clicked.add(el);
                    try {
                        el.scrollIntoView({ behavior: 'instant', block: 'center' });
                        el.click();
                    } catch(e) {}
                });
            });

            // 點完後回到頂部
            window.scrollTo(0, 0);
        }""")
        page.wait_for_timeout(1200)   # 等所有展開動畫完成

        # ── STEP 3：強制把所有殘留隱藏節點打開 ───────────────────────────
        #    先用 class 名稱模式批次處理，再全域掃描計算樣式
        page.evaluate("""() => {
            // 3-a：原生 <details> 全展開
            document.querySelectorAll('details').forEach(el => el.open = true);

            // 3-b：依 class 關鍵字批次強制顯示
            const SHOW_SELECTORS = [
                '[class*="dropdown"]',  '[class*="collapse"]', '[class*="accordion"]',
                '[class*="menu"]',      '[class*="submenu"]',  '[class*="panel"]',
                '[class*="tab-pane"]',  '[class*="content"]',  '[class*="toggle"]',
                '[class*="drawer"]',    '[class*="modal"]',    '[class*="dialog"]',
                '[class*="popover"]',   '[class*="tooltip"]',  '[class*="overlay"]',
                '[class*="offcanvas"]', '[class*="sidebar"]',  '[class*="flyout"]',
                '[class*="expand"]',    '[class*="reveal"]',   '[class*="hidden"]',
                '[class*="hide"]',      '[class*="inactive"]', '[class*="closed"]',
                '[hidden]',
                '[aria-hidden="true"]',
            ];
            SHOW_SELECTORS.forEach(sel => {
                let els;
                try { els = document.querySelectorAll(sel); } catch(e) { return; }
                els.forEach(el => {
                    el.style.setProperty('display',     'block',   'important');
                    el.style.setProperty('visibility',  'visible', 'important');
                    el.style.setProperty('opacity',     '1',       'important');
                    el.style.setProperty('max-height',  'none',    'important');
                    el.style.setProperty('height',      'auto',    'important');
                    el.style.setProperty('overflow',    'visible', 'important');
                    el.removeAttribute('hidden');
                    el.removeAttribute('aria-hidden');
                });
            });

            // 3-c：全域掃描所有節點，對計算樣式為隱藏的元素強制顯示
            //      只掃 Element 節點，跳過 script / style / svg 等無文字容器
            const SKIP_TAGS = new Set(['SCRIPT','STYLE','SVG','NOSCRIPT','TEMPLATE','HEAD']);
            document.querySelectorAll('*').forEach(el => {
                if (SKIP_TAGS.has(el.tagName)) return;
                const s = window.getComputedStyle(el);
                if (
                    s.display     === 'none'    ||
                    s.visibility  === 'hidden'  ||
                    s.visibility  === 'collapse'||
                    s.opacity     === '0'       ||
                    s.height      === '0px'     ||
                    s.maxHeight   === '0px'     ||
                    s.overflow    === 'hidden' && (parseFloat(s.height) === 0)
                ) {
                    el.style.setProperty('display',     'block',   'important');
                    el.style.setProperty('visibility',  'visible', 'important');
                    el.style.setProperty('opacity',     '1',       'important');
                    el.style.setProperty('max-height',  'none',    'important');
                    el.style.setProperty('height',      'auto',    'important');
                    el.style.setProperty('overflow',    'visible', 'important');
                }
            });
        }""")
        page.wait_for_timeout(600)   # 等 DOM reflow 完成
        # ──────────────────────────────────────────────────────────────────

        # ── STEP 4：擷取所有文字節點 ──────────────────────────────────────

        title    = page.title() or ""
        h1_list  = [normalize_spaces(x) for x in page.locator("h1").all_inner_texts() if normalize_spaces(x)]
        h2_list  = [normalize_spaces(x) for x in page.locator("h2").all_inner_texts() if normalize_spaces(x)]
        h3_list  = [normalize_spaces(x) for x in page.locator("h3").all_inner_texts() if normalize_spaces(x)]

        # body.innerText（已展開後的可見文字，最主要來源）
        visible_text = normalize_spaces(page.evaluate("""
            () => document.body.innerText || ''
        """) or "")

        # body.textContent（補抓 innerText 漏掉的非渲染文字節點）
        all_text_content = normalize_spaces(page.evaluate("""
            () => document.body.textContent || ''
        """) or "")

        # <p> 標籤（段落正文，獨立保留方便下游使用）
        p_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('p'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <li> 條列
        li_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('li'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # 表格 td / th
        table_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('td, th'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <div> 全掃（包含展開後才出現的內容）
        div_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('div'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <span> 獨立補抓（常見於 badge / tag / label）
        span_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('span'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <a> 連結文字（anchor text 常含重要關鍵詞）
        a_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('a'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <button> 文字（accordion 按鈕本身的標題）
        button_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('button, [role="button"]'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <label> 表單標籤
        label_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('label'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <input> placeholder / value
        input_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('input, textarea'))
                       .map(el => [el.placeholder || '', el.value || '', el.getAttribute('aria-label') || ''].join(' '))
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <select><option> 下拉選單選項
        option_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('option'))
                       .map(o => o.textContent || o.value || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # <img alt> 圖片替代文字
        alt_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('img[alt]'))
                       .map(img => img.getAttribute('alt') || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # aria-label / aria-describedby / title 屬性（無障礙標注常含資訊）
        aria_texts = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('[aria-label],[aria-describedby],[title]'))
                       .flatMap(el => [
                           el.getAttribute('aria-label')       || '',
                           el.getAttribute('aria-describedby') || '',
                           el.getAttribute('title')            || '',
                       ])
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # data-* 屬性中常見的文字欄位（data-content / data-text / data-label 等）
        data_attr_texts = normalize_spaces(page.evaluate("""
            () => {
                const DATA_KEYS = ['data-content','data-text','data-label',
                                   'data-title','data-description','data-tooltip',
                                   'data-value','data-name'];
                return Array.from(document.querySelectorAll('*'))
                    .flatMap(el => DATA_KEYS.map(k => el.getAttribute(k) || ''))
                    .filter(t => t.trim().length > 0)
                    .join(' ');
            }
        """) or "")

        # <meta> description / keywords
        meta_desc = normalize_spaces(page.evaluate("""
            () => {
                const desc = document.querySelector('meta[name="description"]');
                const kw   = document.querySelector('meta[name="keywords"]');
                return [
                    desc ? (desc.getAttribute('content') || '') : '',
                    kw   ? (kw.getAttribute('content')   || '') : '',
                ].join(' ');
            }
        """) or "")

        # nav / header 導覽列
        nav_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('nav, [role="navigation"], header'))
                       .map(el => el.innerText || el.textContent || '').join(' ')
        """) or "")

        # strong / em / b 強調文字
        emphasis_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('strong, em, b, mark, cite'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0)
                       .join(' ')
        """) or "")

        # Shadow DOM 深層文字
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

        # 同源 iframe 文字
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

        # ── TreeWalker：逐一走訪所有 TEXT_NODE，不漏任何文字節點 ──────────
        #    這是最底層的保險網：不管元素結構怎麼包，只要有文字就抓到
        walker_texts = normalize_spaces(page.evaluate("""
            () => {
                const SKIP_TAGS = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE']);
                const parts = [];
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    {
                        acceptNode(node) {
                            // 跳過 script/style 內的文字
                            let p = node.parentElement;
                            while (p) {
                                if (SKIP_TAGS.has(p.tagName)) return NodeFilter.FILTER_REJECT;
                                p = p.parentElement;
                            }
                            return node.nodeValue && node.nodeValue.trim()
                                ? NodeFilter.FILTER_ACCEPT
                                : NodeFilter.FILTER_SKIP;
                        }
                    }
                );
                let node;
                while ((node = walker.nextNode())) {
                    const t = node.nodeValue.trim();
                    if (t) parts.push(t);
                }
                return parts.join(' ');
            }
        """) or "")
        # ──────────────────────────────────────────────────────────────────

        # ── 合併所有來源 ───────────────────────────────────────────────────
        full_text = normalize_spaces(" ".join([
            visible_text,       # body.innerText（主力）
            all_text_content,   # body.textContent（補漏）
            walker_texts,       # TreeWalker 逐節點（終極保險）
            p_texts,            # <p>
            li_texts,           # <li>
            table_texts,        # <td><th>
            div_texts,          # <div>
            span_texts,         # <span>
            a_texts,            # <a>
            button_texts,       # <button>
            label_texts,        # <label>
            input_texts,        # <input> placeholder/value
            option_texts,       # <option>
            alt_texts,          # img[alt]
            aria_texts,         # aria-label / title 屬性
            data_attr_texts,    # data-content / data-text 等
            meta_desc,          # <meta description/keywords>
            shadow_texts,       # Shadow DOM
            iframe_texts,       # 同源 iframe
        ]))
        if len(full_text) > MAX_FULL_TEXT_CHARS:
            full_text = full_text[:MAX_FULL_TEXT_CHARS]

        return {
            "title":         title,
            "h1_list":       h1_list,
            "h2_list":       h2_list,
            "h3_list":       h3_list,
            "full_text":     full_text,
            "nav_text":      nav_text,
            "emphasis_text": emphasis_text,
            "p_texts":       p_texts,   # 單獨保留，下游可直接使用
            "error":         None,
        }

    except Exception as e:
        return {
            "title": "", "h1_list": [], "h2_list": [], "h3_list": [],
            "full_text": "", "nav_text": "", "emphasis_text": "",
            "p_texts": "", "error": str(e),
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

    print(f"[{sid}] {url}  |  {title}")

    if result.get("error"):
        print(f"  ERROR: {result['error']}")
        return

    h1 = " / ".join(result["h1_list"][:3])
    h2 = " / ".join(result["h2_list"][:3])
    h3 = " / ".join(result["h3_list"][:3])
    heads = []
    if h1: heads.append(f"h1:{h1}")
    if h2: heads.append(f"h2:{h2}")
    if h3: heads.append(f"h3:{h3}")
    if heads:
        print("  " + "  |  ".join(heads))

    scores_str = "  ".join(
        f"{pt}={sc}" for pt, sc in
        sorted(result["scores"].items(), key=lambda x: x[1], reverse=True)
    )
    print(f"  → {result['type']} (best:{result['best_type']}={result['best_score']})  [{scores_str}]")
    print()


# ──────────────────────────────────────────────
# 單一 thread 的工作單元
# ──────────────────────────────────────────────

def worker(school_urls_batch):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
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

def score_one_school(school_id: str, path: str):
    print(f"\n=== Batch Classification for school={school_id} ===\n")

    file = school_id + "_keep.json"
    FILE_PATH = path / school_id / file

    with open(FILE_PATH, "r", encoding="utf-8") as f:
        school = json.load(f)

    flat = [
        (school["school_id"], url)
        for url in school["urls"]
    ]

    if not flat:
        print("⚠️  沒有找到任何 URL，結束")
        return []

    chunks = [flat[i::NUM_THREADS] for i in range(NUM_THREADS)]
    chunks = [c for c in chunks if c]

    all_results = []

    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = {
            executor.submit(worker, chunk): i
            for i, chunk in enumerate(chunks)
        }

        for future in as_completed(futures):
            try:
                all_results.extend(future.result())
            except Exception as e:
                print(f"[ERROR] thread {futures[future]} failed: {e}")

    print(f"\n✅ 完成！學校={school_id}，共處理 {len(all_results)} 個 URL")
    return all_results


# if __name__ == "__main__":
#     main()