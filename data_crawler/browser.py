"""Playwright 相關：BFS 逐層爬取（Node 2）與單頁全文擷取（Node 4）。

- extract_links / crawl_page / block_resources / run_worker 邏輯搬自
  crawler/url_crawler.py（extract_links 額外補上 anchor text，供未來
  Node 3 選用，主流程目前不用）
- extract_page_content_with_js 完整搬自 crawler/score.py（15 種 DOM 來源 +
  展開 accordion / lazy-load），另新增：
    * structured_markdown：展開後的 DOM 丟 trafilatura 轉 markdown（保留標題階層/表格）
    * structured_tables：<table> 逐一轉 markdown（保留欄位對應）
    * content_hash：md5（save_result.py 的去重邏輯）
    * PDF 分支：pymupdf 抽文字
"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .settings_bridge import CONFIG, USER_AGENT
from .url_tools import normalize_url, get_root_info, is_same_root, filter_url, is_pdf
from .text_clean import normalize_spaces, deduplicate_text, content_hash

MAX_FULL_TEXT_CHARS = 2_000_000
MAX_RETRY = 2
RETRY_WAIT_MS = 2000

# 全域併發上限：Send fan-out 時避免同時開太多瀏覽器
_BROWSER_SEMAPHORE = threading.Semaphore(CONFIG.NUM_WORKERS)


# ══════════════════════════════════════════════
# 共用
# ══════════════════════════════════════════════

def block_resources(context):
    context.route("**/*", lambda route: (
        route.abort()
        if route.request.resource_type in {"image", "media", "font", "stylesheet"}
        else route.continue_()
    ))


def _new_browser(p, headless=True):
    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1440, "height": 900},
        java_script_enabled=True,
        ignore_https_errors=True,
    )
    block_resources(context)
    return browser, context


# ══════════════════════════════════════════════
# Node 2：BFS 一層（搬自 url_crawler.py，回傳 dict 而非寫檔）
# ══════════════════════════════════════════════

def extract_links(page, current_url: str, root_info: dict) -> dict:
    """搬自 url_crawler.extract_links，補抓 anchor text（keep_anchors）。"""
    raw_links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
                   .map(a => ({href: a.href, text: (a.innerText || '').trim().slice(0, 120)}))
                   .filter(o => o.href && o.href.length > 0)
    """) or []

    keep = set()
    keep_anchors = {}
    drop = {}
    external = set()

    for item in raw_links:
        try:
            normalized = normalize_url(urljoin(current_url, item["href"]))
        except Exception:
            continue

        if not is_same_root(normalized, root_info):
            external.add(normalized)
            continue

        ok, reason = filter_url(normalized)
        if ok:
            keep.add(normalized)
            if item.get("text") and normalized not in keep_anchors:
                keep_anchors[normalized] = item["text"]
        else:
            drop[normalized] = reason

    return {"keep": keep, "keep_anchors": keep_anchors, "drop": drop, "external": external}


def crawl_page(page, url: str, root_info: dict) -> dict:
    result = {
        "url": url, "title": "",
        "keep": set(), "keep_anchors": {}, "drop": {}, "external": set(),
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


def _run_link_worker(task_chunk: list, worker_id: int, print_lock: threading.Lock) -> list:
    """搬自 url_crawler.run_worker：一個 worker 開一個瀏覽器，逐頁抓連結。

    task_chunk: [(url, depth, root_info), ...]
    """
    results = []
    with sync_playwright() as p:
        browser, context = _new_browser(p)
        page = context.new_page()

        for idx, (url, depth, root_info) in enumerate(task_chunk):
            result = crawl_page(page, url, root_info)
            results.append((url, depth, result))

            with print_lock:
                status = f"⚠️  {result['error'][:60]}" if result["error"] else f'"{result["title"][:50]}"'
                print(f"  [w{worker_id}|d={depth}|#{idx+1}] {url}  →  {status}")

            if idx < len(task_chunk) - 1:
                page.wait_for_timeout(CONFIG.CRAWL_DELAY_MS)

        context.close()
        browser.close()
    return results


def crawl_one_layer(layer: list[dict], roots: list[str]) -> list[tuple]:
    """爬一個 depth 的所有 URL，回傳 [(url, depth, result_dict), ...]。

    layer: [{"url", "depth", "root_index"}]
    保留原本多 worker + block_resources 的做法，只是結果回傳而不寫檔。
    """
    root_infos = [get_root_info(normalize_url(r)) for r in roots]
    tasks = [(item["url"], item["depth"], root_infos[item["root_index"]]) for item in layer]

    chunks = [tasks[i::CONFIG.NUM_WORKERS] for i in range(CONFIG.NUM_WORKERS)]
    chunks = [c for c in chunks if c]

    print_lock = threading.Lock()
    layer_results: list[tuple] = []
    with ThreadPoolExecutor(max_workers=CONFIG.NUM_WORKERS) as executor:
        futures = {
            executor.submit(_run_link_worker, chunk, wid, print_lock): wid
            for wid, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            try:
                layer_results.extend(future.result())
            except Exception as e:
                print(f"  ⚠️  link worker 例外: {e}")
    return layer_results


# ══════════════════════════════════════════════
# Node 4：單頁全文擷取（搬自 score.py + 新增結構化輸出）
# ══════════════════════════════════════════════

_SCROLL_JS = """async () => {
    await new Promise(resolve => {
        const distance = 300;
        const delay    = 300;
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
}"""

_CLICK_EXPANDERS_JS = """() => {
    const CLICK_SELECTORS = [
        '[aria-expanded="false"]',
        '[aria-selected="false"]',
        '[aria-checked="false"]',
        'button.accordion-button',
        '.accordion-header button',
        '.accordion-item .accordion-button',
        '[data-bs-toggle="collapse"]',
        '[data-toggle="collapse"]',
        '[data-bs-toggle="tab"]',
        '[data-toggle="tab"]',
        '[data-bs-toggle="pill"]',
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
        'summary',
        '[role="button"][aria-expanded="false"]',
        '[role="tab"]',
        '[role="treeitem"]',
        '.faq-question',
        '.faq-title',
        '.faq-toggle',
        '.question',
        '.q-item',
        '[class*="faq"] button',
        '[class*="faq"] [role="button"]',
        '[data-target]',
        '[href="#"]',
    ];
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
    window.scrollTo(0, 0);
}"""

_FORCE_SHOW_JS = """() => {
    document.querySelectorAll('details').forEach(el => el.open = true);
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
}"""

_TEXT_SOURCES_JS = {
    "p_texts": """() => Array.from(document.querySelectorAll('p'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "li_texts": """() => Array.from(document.querySelectorAll('li'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "table_texts": """() => Array.from(document.querySelectorAll('td, th'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "div_texts": """() => Array.from(document.querySelectorAll('div'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "span_texts": """() => Array.from(document.querySelectorAll('span'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "a_texts": """() => Array.from(document.querySelectorAll('a'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "button_texts": """() => Array.from(document.querySelectorAll('button, [role="button"]'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "label_texts": """() => Array.from(document.querySelectorAll('label'))
        .map(el => el.innerText || el.textContent || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "input_texts": """() => Array.from(document.querySelectorAll('input, textarea'))
        .map(el => [el.placeholder || '', el.value || '', el.getAttribute('aria-label') || ''].join(' '))
        .filter(t => t.trim().length > 0).join(' ')""",
    "option_texts": """() => Array.from(document.querySelectorAll('option'))
        .map(o => o.textContent || o.value || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "alt_texts": """() => Array.from(document.querySelectorAll('img[alt]'))
        .map(img => img.getAttribute('alt') || '')
        .filter(t => t.trim().length > 0).join(' ')""",
    "aria_texts": """() => Array.from(document.querySelectorAll('[aria-label],[aria-describedby],[title]'))
        .flatMap(el => [
            el.getAttribute('aria-label')       || '',
            el.getAttribute('aria-describedby') || '',
            el.getAttribute('title')            || '',
        ])
        .filter(t => t.trim().length > 0).join(' ')""",
    "data_attr_texts": """() => {
        const DATA_KEYS = ['data-content','data-text','data-label',
                           'data-title','data-description','data-tooltip',
                           'data-value','data-name'];
        return Array.from(document.querySelectorAll('*'))
            .flatMap(el => DATA_KEYS.map(k => el.getAttribute(k) || ''))
            .filter(t => t.trim().length > 0).join(' ');
    }""",
    "meta_desc": """() => {
        const desc = document.querySelector('meta[name="description"]');
        const kw   = document.querySelector('meta[name="keywords"]');
        return [
            desc ? (desc.getAttribute('content') || '') : '',
            kw   ? (kw.getAttribute('content')   || '') : '',
        ].join(' ');
    }""",
    "shadow_texts": """() => {
        function extractShadow(root) {
            let text = '';
            root.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot) text += ' ' + extractShadow(el.shadowRoot);
            });
            if (root !== document) text += ' ' + (root.textContent || '');
            return text;
        }
        return extractShadow(document);
    }""",
}

_WALKER_JS = """() => {
    const SKIP_TAGS = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE']);
    const parts = [];
    const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode(node) {
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
}"""

_MAIN_CONTENT_JS = """() => {
    const SELECTORS = [
        'main', 'article', '[role="main"]',
        '#main', '#main-content', '.main-content',
        '#content', '.content', '.entry-content',
        '.page-content', '#page-content',
    ];
    for (const sel of SELECTORS) {
        try {
            const el = document.querySelector(sel);
            if (el) {
                const t = el.innerText || el.textContent || '';
                if (t.trim().length > 500) return t;
            }
        } catch(e) {}
    }
    return '';
}"""

# 表格轉結構化資料：每個 table 回傳 rows（保留欄位對應）
_TABLES_JS = """() => Array.from(document.querySelectorAll('table')).slice(0, 30).map(table => {
    const rows = Array.from(table.querySelectorAll('tr')).slice(0, 100).map(tr =>
        Array.from(tr.querySelectorAll('th, td'))
             .map(cell => (cell.innerText || cell.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 300))
    );
    return rows.filter(r => r.some(c => c.length > 0));
}).filter(rows => rows.length > 0)"""


def _tables_to_markdown(tables: list) -> str:
    """把 _TABLES_JS 的結果轉為 markdown 表格字串。"""
    md_parts = []
    for rows in tables or []:
        if not rows:
            continue
        width = max(len(r) for r in rows)
        norm = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(norm[0]) + " |",
                 "| " + " | ".join(["---"] * width) + " |"]
        for r in norm[1:]:
            lines.append("| " + " | ".join(r) + " |")
        md_parts.append("\n".join(lines))
    return "\n\n".join(md_parts)


def _html_to_markdown(html: str, url: str) -> str:
    """展開後的 DOM HTML → markdown（trafilatura；保留標題階層與表格）。"""
    if not html:
        return ""
    try:
        import trafilatura
        md = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=False,
            include_formatting=True,
            favor_recall=True,
        )
        return md or ""
    except Exception as e:
        print(f"  ⚠️ trafilatura 失敗（{url}）：{e}")
        return ""


def extract_page_content_with_js(page, url, extra_wait_ms=3000):
    """完整搬自 score.py，新增 structured_markdown / structured_tables / content_hash。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(extra_wait_ms)

        # STEP 1-3：慢速捲動 → 點開所有可展開元素 → 強制顯示殘留隱藏節點
        page.evaluate(_SCROLL_JS)
        page.wait_for_timeout(1000)
        page.evaluate(_CLICK_EXPANDERS_JS)
        page.wait_for_timeout(1200)
        page.evaluate(_FORCE_SHOW_JS)
        page.wait_for_timeout(600)

        # STEP 4：擷取所有文字來源
        title = page.title() or ""
        h1_list = [normalize_spaces(x) for x in page.locator("h1").all_inner_texts() if normalize_spaces(x)]
        h2_list = [normalize_spaces(x) for x in page.locator("h2").all_inner_texts() if normalize_spaces(x)]
        h3_list = [normalize_spaces(x) for x in page.locator("h3").all_inner_texts() if normalize_spaces(x)]

        visible_text = normalize_spaces(page.evaluate("() => document.body.innerText || ''") or "")
        all_text_content = normalize_spaces(page.evaluate("() => document.body.textContent || ''") or "")
        walker_texts = normalize_spaces(page.evaluate(_WALKER_JS) or "")

        sources = {}
        for key, js in _TEXT_SOURCES_JS.items():
            try:
                sources[key] = normalize_spaces(page.evaluate(js) or "")
            except Exception:
                sources[key] = ""

        nav_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('nav, [role="navigation"], header'))
                       .map(el => el.innerText || el.textContent || '').join(' ')
        """) or "")
        emphasis_text = normalize_spaces(page.evaluate("""
            () => Array.from(document.querySelectorAll('strong, em, b, mark, cite'))
                       .map(el => el.innerText || el.textContent || '')
                       .filter(t => t.trim().length > 0).join(' ')
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

        main_content = ""
        try:
            main_content = normalize_spaces(page.evaluate(_MAIN_CONTENT_JS) or "")
        except Exception:
            pass

        # 合併所有來源（main_content 優先）
        prefix = (main_content + " ") if len(main_content) > 500 else ""
        full_text = normalize_spaces(prefix + " ".join([
            visible_text,
            all_text_content,
            walker_texts,
            sources["p_texts"],
            sources["li_texts"],
            sources["table_texts"],
            sources["div_texts"],
            sources["span_texts"],
            sources["a_texts"],
            sources["button_texts"],
            sources["label_texts"],
            sources["input_texts"],
            sources["option_texts"],
            sources["alt_texts"],
            sources["aria_texts"],
            sources["data_attr_texts"],
            sources["meta_desc"],
            sources["shadow_texts"],
            iframe_texts,
        ]))
        if len(full_text) > MAX_FULL_TEXT_CHARS:
            full_text = full_text[:MAX_FULL_TEXT_CHARS]
        full_text = deduplicate_text(full_text)

        # ── 新增：結構化輸出（DOM 已展開，只是多兩種呈現方式）─────────
        raw_tables = []
        try:
            raw_tables = page.evaluate(_TABLES_JS) or []
        except Exception:
            pass
        structured_tables = _tables_to_markdown(raw_tables)

        structured_markdown = ""
        try:
            html = page.content()
            structured_markdown = _html_to_markdown(html, url)
        except Exception:
            pass

        return {
            "title": title,
            "h1_list": h1_list, "h2_list": h2_list, "h3_list": h3_list,
            "full_text": full_text,
            "nav_text": nav_text,
            "emphasis_text": emphasis_text,
            "p_texts": sources["p_texts"],
            "structured_markdown": structured_markdown,
            "structured_tables": structured_tables,
            "content_hash": content_hash(full_text),
            "error": None,
        }

    except Exception as e:
        return {
            "title": "", "h1_list": [], "h2_list": [], "h3_list": [],
            "full_text": "", "nav_text": "", "emphasis_text": "", "p_texts": "",
            "structured_markdown": "", "structured_tables": "",
            "content_hash": "", "error": str(e),
        }


def extract_pdf_content(url: str) -> dict:
    """PDF 分支：下載 + pymupdf 抽文字（含簡單表格轉 markdown）。"""
    try:
        import urllib.request
        import fitz  # pymupdf

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()

        doc = fitz.open(stream=data, filetype="pdf")
        pages_text = []
        tables_md = []
        for pno in range(min(len(doc), 50)):
            pg = doc[pno]
            pages_text.append(pg.get_text("text"))
            try:
                for tab in pg.find_tables().tables[:5]:
                    tables_md.append(tab.to_markdown())
            except Exception:
                pass
        doc.close()

        full_text = normalize_spaces(" ".join(pages_text))[:MAX_FULL_TEXT_CHARS]
        markdown = "\n\n".join(pages_text)
        return {
            "title": url.rsplit("/", 1)[-1],
            "h1_list": [], "h2_list": [], "h3_list": [],
            "full_text": full_text,
            "nav_text": "", "emphasis_text": "", "p_texts": "",
            "structured_markdown": markdown,
            "structured_tables": "\n\n".join(tables_md),
            "content_hash": content_hash(full_text),
            "error": None,
        }
    except Exception as e:
        return {
            "title": "", "h1_list": [], "h2_list": [], "h3_list": [],
            "full_text": "", "nav_text": "", "emphasis_text": "", "p_texts": "",
            "structured_markdown": "", "structured_tables": "",
            "content_hash": "", "error": str(e),
        }


def scrape_url(url: str) -> dict:
    """Node 4 入口：單一 URL → 全文 + 結構化內容（帶重試，與 score.py 相同節奏）。

    以 semaphore 限制同時開啟的瀏覽器數（Send fan-out 併發上限）。
    """
    if is_pdf(url):
        return extract_pdf_content(url)

    with _BROWSER_SEMAPHORE:
        with sync_playwright() as p:
            browser, context = _new_browser(p)
            page = context.new_page()
            extracted = None
            for attempt in range(1, MAX_RETRY + 2):
                if attempt > 1:
                    print(f"   ↺ retry {attempt-1}/{MAX_RETRY} {url}")
                    page.wait_for_timeout(RETRY_WAIT_MS)
                extracted = extract_page_content_with_js(page, url)
                if not extracted["error"]:
                    break
            context.close()
            browser.close()
    return extracted
