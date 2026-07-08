"""主圖節點：Node 1（init）、Node 2（逐層 BFS）、Node 4 fan-out、
Node 9（sufficiency）、Node 10-14（tagging / chunking / embedding / db_writer / finalize）。
"""
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from langgraph.types import Send

from .settings_bridge import CONFIG, SCHOOLS
from .state import SchoolState, ScrapeState, KEEP_TYPES
from .url_tools import normalize_url, filter_url
from .browser import crawl_one_layer, scrape_url
from .text_clean import clean_noise
from .llm import call_llm_json
from .prompts import sufficiency_prompt
from . import db as dbm

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

DEFAULT_MAX_SUFFICIENCY_ITERATIONS = 2

# 重要欄位（sufficiency 覆蓋率評估用）
IMPORTANT_FIELDS = ["toefl_ibt_min", "ielts_min", "gre_required",
                    "application_fee_usd", "tuition_per_year_usd", "rec_letter_count"]


# ──────────────────────────────────────────────
# Node 1：init_crawl
# ──────────────────────────────────────────────

def init_crawl(state: SchoolState) -> dict:
    school_id = state["school_id"]
    school = next((s for s in SCHOOLS if s["school_id"] == school_id), None)
    if school is None:
        raise ValueError(f"setting/root_url.py 找不到 school_id={school_id}")

    roots = [normalize_url(r) for r in school["roots"] if r and r.startswith("http")]
    max_depth = state.get("max_depth")
    max_depth = CONFIG.MAX_DEPTH if max_depth is None else max_depth
    max_pages = state.get("max_pages")
    max_pages = CONFIG.MAX_PAGES if max_pages is None else max_pages

    university_id = None
    known_codes: list[str] = []
    skip_school = False

    if not state.get("dry_run"):
        conn = dbm.get_connection()
        try:
            dbm.ensure_migrations(conn)
            university_id = dbm.get_or_create_university(conn, school_id)
            known_codes = dbm.list_known_program_codes(conn, university_id)
            hours = state.get("skip_recent_hours") or 0
            if hours > 0 and dbm.school_recently_extracted(conn, university_id, hours):
                skip_school = True
                print(f"[SKIP] {school_id} — DB 內資料 {hours}h 內已更新過，整校跳過")
        finally:
            conn.close()

    queue = [{"url": r, "depth": 0, "root_index": i} for i, r in enumerate(roots)]
    print(f"[INIT] {school_id}: {len(roots)} roots, max_depth={max_depth}, max_pages={max_pages}")

    return {
        "roots": roots,
        "university_id": university_id,
        "known_program_codes": known_codes,
        "skip_school": skip_school,
        "max_depth": max_depth,
        "max_pages": max_pages,
        "current_depth": 0,
        "url_queue": queue,
        "visited_urls": [q["url"] for q in queue],
        "discovered_urls": [],
        "dropped_urls": {},
        "external_urls": [],
        "total_crawled": 0,
        "unique_pages": [],
        "sufficiency_iterations": 0,
        "max_sufficiency_iterations": state.get("max_sufficiency_iterations")
                                      or DEFAULT_MAX_SUFFICIENCY_ITERATIONS,
    }


def after_init(state: SchoolState) -> str:
    return "finalize_school" if state.get("skip_school") else "discover_urls"


# ──────────────────────────────────────────────
# Node 2：discover_urls（一次一層，每層結束即 checkpoint）
# ──────────────────────────────────────────────

def discover_urls(state: SchoolState) -> dict:
    layer = state.get("url_queue", [])
    if not layer:
        return {}

    max_pages = state.get("max_pages", CONFIG.MAX_PAGES)
    total_crawled = state.get("total_crawled", 0)
    if max_pages > 0:
        layer = layer[: max(0, max_pages - total_crawled)]
    if not layer:
        return {"url_queue": []}

    depth = layer[0]["depth"]
    print(f"\n[BFS depth={depth}] {len(layer)} 頁待爬（{state['school_id']}）")

    layer_results = crawl_one_layer(layer, state["roots"])

    visited = set(state.get("visited_urls", []))
    discovered = list(state.get("discovered_urls", []))
    discovered_set = set(discovered)
    dropped = dict(state.get("dropped_urls", {}))
    external = set(state.get("external_urls", []))
    root_index_by_url = {item["url"]: item["root_index"] for item in layer}

    next_layer = []
    max_depth = state.get("max_depth", CONFIG.MAX_DEPTH)

    for url, dep, result in layer_results:
        total_crawled += 1
        if not result["error"] and url not in discovered_set:
            discovered.append(url)
            discovered_set.add(url)
        dropped.update(result["drop"])
        external.update(result["external"])

        for child_url in sorted(result["keep"]):
            if child_url not in visited:
                visited.add(child_url)
                if dep + 1 <= max_depth:
                    next_layer.append({"url": child_url, "depth": dep + 1,
                                       "root_index": root_index_by_url.get(url, 0)})

    # drop 統計（沿用 crawl_tree 的層級摘要）
    reasons: dict = {}
    for _, _, r in layer_results:
        for reason in r["drop"].values():
            reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        top = sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:8]
        print(f"  [depth={depth} drop] " + "  ".join(f"{r}={c}" for r, c in top))

    return {
        "current_depth": depth + 1,
        "url_queue": next_layer,
        "visited_urls": sorted(visited),
        "discovered_urls": discovered,
        "dropped_urls": dropped,
        "external_urls": sorted(external),
        "total_crawled": total_crawled,
    }


def should_continue_bfs(state: SchoolState) -> str:
    max_pages = state.get("max_pages", CONFIG.MAX_PAGES)
    if (state.get("url_queue")
            and (max_pages <= 0 or state.get("total_crawled", 0) < max_pages)):
        return "discover_urls"
    return "plan_scrape"


# ──────────────────────────────────────────────
# Node 4 fan-out：plan_scrape →(Send)→ scrape_page → collect_scraped
# ──────────────────────────────────────────────

def plan_scrape(state: SchoolState) -> dict:
    return {}


def dispatch_scrape(state: SchoolState):
    already = {p["url"] for p in state.get("scraped_pages", [])}
    pending = [u for u in state.get("discovered_urls", []) if u not in already]
    if not pending:
        return "collect_scraped"
    print(f"\n[SCRAPE] fan-out {len(pending)} 頁（{state['school_id']}）")
    return [Send("scrape_page", {"school_id": state["school_id"], "url": url})
            for url in pending]


def scrape_page(state: ScrapeState) -> dict:
    """Node 4：單頁全文 + structured_markdown / tables / content_hash / PDF。"""
    url = state["url"]
    extracted = scrape_url(url)
    record = {"url": url, **extracted}
    status = f"⚠️ {extracted['error'][:60]}" if extracted["error"] else f"{len(extracted['full_text'])} chars"
    print(f"  [scraped] {url} → {status}")
    return {"scraped_pages": [record]}


def collect_scraped(state: SchoolState) -> dict:
    """content-hash 去重（save_result.py 邏輯）＋ 過濾錯誤頁。"""
    seen_hashes = set()
    unique = []
    processed_urls = {p["url"] for p in state.get("page_results", [])}

    for rec in state.get("scraped_pages", []):
        if rec.get("error") or not rec.get("full_text"):
            continue
        if rec["url"] in processed_urls:
            continue  # sufficiency 迴圈時不重複處理
        h = rec.get("content_hash")
        if h in seen_hashes:
            print(f"  ⏭ duplicate content skipped: {rec['url'][:80]}")
            continue
        seen_hashes.add(h)
        unique.append(rec)

    print(f"[COLLECT] {len(unique)} 頁待分類（去重/去錯誤後）")
    return {"unique_pages": unique}


def dispatch_process(state: SchoolState):
    pages = state.get("unique_pages", [])
    if not pages:
        return "sufficiency_evaluator"
    return [Send("process_page", {
        "school_id": state["school_id"],
        "university_id": state.get("university_id"),
        "known_program_codes": state.get("known_program_codes", []),
        "page": page,
    }) for page in pages]


# ──────────────────────────────────────────────
# Node 9：sufficiency_evaluator（LLM 自主判斷 + 動態補爬）
# ──────────────────────────────────────────────

def _coverage_report(page_results: list[dict]) -> dict:
    coverage: dict = {}
    for r in page_results:
        if r.get("status") != "ok":
            continue
        ext = r.get("extraction") or {}
        for prog in ext.get("programs", []):
            code = prog.get("program_code") or "?"
            got = coverage.setdefault(code, {"fields": set(), "deadlines": 0})
            got["fields"].update(prog.get("fields", {}).keys())
        for d in ext.get("deadlines", []):
            code = d.get("program_code") or "?"
            coverage.setdefault(code, {"fields": set(), "deadlines": 0})["deadlines"] += 1

    report = {}
    for code, got in coverage.items():
        report[code] = {
            "fields_extracted": sorted(got["fields"]),
            "deadline_count": got["deadlines"],
            "missing_important": [f for f in IMPORTANT_FIELDS if f not in got["fields"]],
        }
    return report


def _registrable_domain(netloc: str) -> str:
    parts = netloc.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


def sufficiency_evaluator(state: SchoolState) -> dict:
    iterations = state.get("sufficiency_iterations", 0)
    report = _coverage_report(state.get("page_results", []))

    # 候選補爬 URL：同校 registrable domain 的外部連結（通過黑名單過濾、未爬過）
    school_domains = {_registrable_domain(urlparse(r).netloc) for r in state.get("roots", [])}
    visited = set(state.get("visited_urls", []))
    candidates = []
    for u in state.get("external_urls", []):
        if u in visited:
            continue
        if _registrable_domain(urlparse(u).netloc) not in school_domains:
            continue
        ok, _ = filter_url(u)
        if ok:
            candidates.append(u)

    if not report:
        result = {"sufficient": False, "missing_summary": "沒有任何抽取結果", "seed_urls": []}
    else:
        try:
            result = call_llm_json(sufficiency_prompt(state["school_id"], report, candidates))
        except Exception as e:
            print(f"  ⚠️ sufficiency LLM 失敗，視為足夠：{e}")
            result = {"sufficient": True, "missing_summary": str(e), "seed_urls": []}

    seeds = [normalize_url(u) for u in result.get("seed_urls", []) if u in set(candidates)]
    print(f"[SUFFICIENCY iter={iterations}] sufficient={result.get('sufficient')} "
          f"missing={result.get('missing_summary','')[:80]} seeds={len(seeds)}")

    return {
        "sufficiency_iterations": iterations + 1,
        "sufficiency_report": {"coverage": report, **result},
        "extra_seed_urls": seeds,
    }


def check_sufficiency(state: SchoolState) -> str:
    report = state.get("sufficiency_report") or {}
    seeds = state.get("extra_seed_urls") or []
    # sufficiency_iterations 在 evaluator 內先 +1，所以用 <=：max=N 代表最多補爬 N 輪
    if (not report.get("sufficient")
            and seeds
            and state.get("sufficiency_iterations", 0) <= state.get("max_sufficiency_iterations", 2)):
        return "seed_more_urls"
    return "tagging"


def seed_more_urls(state: SchoolState) -> dict:
    """把 LLM 挑的種子 URL 當新 root 塞回同一個 BFS 佇列，繼續往下爬。"""
    roots = list(state.get("roots", []))
    visited = set(state.get("visited_urls", []))
    queue = list(state.get("url_queue", []))

    added = 0
    for u in state.get("extra_seed_urls", []):
        if u in visited:
            continue
        roots.append(u)
        visited.add(u)
        queue.append({"url": u, "depth": 0, "root_index": len(roots) - 1})
        added += 1

    print(f"[SEED] 新增 {added} 個種子 URL，回到 BFS")
    return {"roots": roots, "visited_urls": sorted(visited),
            "url_queue": queue, "extra_seed_urls": []}


# ──────────────────────────────────────────────
# Node 10-12：tagging / chunking / embedding
# ──────────────────────────────────────────────

def tagging(state: SchoolState) -> dict:
    """規則式標籤：分類類型 + program codes（無需 LLM）。"""
    for r in state.get("page_results", []):
        if r.get("status") != "ok":
            continue
        tags = [t["type"] for t in r.get("passed_types", [])]
        tags += [p["program_code"] for p in r.get("program_codes", []) if p.get("program_code")]
        r["tags"] = sorted(set(tags))
    return {}


def chunking(state: SchoolState) -> dict:
    """用 structured_markdown 切 chunk（清洗沿用 clean_json_data 關鍵字清單）。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n## ", "\n### ", "\n#### ", "\n\n", "\n", ". ", " "],
    )

    chunks = []
    for r in state.get("page_results", []):
        if r.get("status") != "ok":
            continue
        text = r.get("structured_markdown") or r.get("full_text") or ""
        text = clean_noise(text)
        if not text.strip():
            continue
        type_label = ",".join(t["type"] for t in r.get("passed_types", [])) or "general"
        prefix = f"[{state['school_id']} | {type_label}] "
        for piece in splitter.split_text(text):
            if len(piece.strip()) < 50:
                continue
            chunks.append({"url": r["url"], "text": prefix + piece.strip()})

    print(f"[CHUNK] 共 {len(chunks)} 個 chunk")
    return {"chunks": chunks}


def embedding(state: SchoolState) -> dict:
    """ENABLE_EMBEDDING 開關（預設關，靠 fts_vector 全文檢索）。

    開啟時沿用 backend embedder 的模型（BAAI/bge-m3，1024 維，
    env BGE_EMBED_MODEL_PATH 可指定本機路徑）。
    """
    if not state.get("enable_embedding"):
        print("[EMBED] ENABLE_EMBEDDING=off，跳過（之後可用 backfill_embeddings.py 補）")
        return {}

    from sentence_transformers import SentenceTransformer
    model_path = os.getenv("BGE_EMBED_MODEL_PATH", "BAAI/bge-m3")
    model = SentenceTransformer(model_path)

    chunks = state.get("chunks", [])
    texts = [c["text"] for c in chunks]
    vectors = model.encode(texts, batch_size=16, show_progress_bar=True, normalize_embeddings=True)
    for c, v in zip(chunks, vectors):
        c["embedding"] = v.tolist()
    print(f"[EMBED] 完成 {len(chunks)} 個 chunk")
    return {"chunks": chunks}


# ──────────────────────────────────────────────
# Node 13：db_writer
# ──────────────────────────────────────────────

def db_writer(state: SchoolState) -> dict:
    school_id = state["school_id"]
    ok_results = [r for r in state.get("page_results", []) if r.get("status") == "ok"]

    if state.get("dry_run"):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / f"{school_id}_result.json"
        payload = {
            "school_id": school_id,
            "generated_at": datetime.now().isoformat(),
            "page_results": [{k: v for k, v in r.items()
                              if k not in ("full_text", "structured_markdown")} | {
                                 "char_count": len(r.get("full_text", ""))}
                             for r in state.get("page_results", [])],
            "review_items": state.get("review_items", []),
            "sufficiency_report": state.get("sufficiency_report", {}),
            "chunk_count": len(state.get("chunks", [])),
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[DB] dry-run：結果寫入 {out}")
        return {"summary": {"db": "dry_run", "output_file": str(out)}}

    conn = dbm.get_connection()
    university_id = state["university_id"]
    stats = {"pages": 0, "programs": 0, "deadlines": 0, "scholarships": 0,
             "materials": 0, "review": 0, "chunks": 0}
    chunks_by_url: dict[str, list] = {}
    for c in state.get("chunks", []):
        chunks_by_url.setdefault(c["url"], []).append(c)

    try:
        program_id_by_code: dict[str, int] = {}

        for r in ok_results:
            ext = r.get("extraction") or {}
            meta_by_code = {p["program_code"]: p for p in r.get("program_codes", [])
                            if p.get("program_code")}

            # programs（欄位非 null 才覆蓋）
            for prog in ext.get("programs", []):
                code = prog.get("program_code")
                if not code:
                    continue
                fields = {name: fv.get("value") for name, fv in (prog.get("fields") or {}).items()}
                pid = dbm.upsert_program(conn, university_id, code,
                                         meta_by_code.get(code, {}), fields,
                                         r["url"], r.get("confidence"))
                program_id_by_code[code] = pid
                stats["programs"] += 1

            # 沒有欄位抽取但有識別到 program 的頁面，仍建立 program 骨架
            for code, meta in meta_by_code.items():
                if code not in program_id_by_code:
                    pid = dbm.upsert_program(conn, university_id, code, meta, {},
                                             r["url"], r.get("confidence"))
                    program_id_by_code[code] = pid

            main_program_id = None
            codes = [p.get("program_code") for p in r.get("program_codes", [])]
            if codes and codes[0] in program_id_by_code:
                main_program_id = program_id_by_code[codes[0]]

            # web_pages（清洗後全文）
            raw_text = clean_noise(r.get("full_text", ""))
            page_id = dbm.upsert_web_page(conn, university_id, r["url"],
                                          r.get("passed_types", []), raw_text,
                                          main_program_id)
            stats["pages"] += 1

            # 子表：查詢後決定 insert/update（v4 3-1）；過濾掉沒有實質內容的空殼列
            for d in ext.get("deadlines", []):
                pid = program_id_by_code.get(d.get("program_code"))
                if pid and (d.get("deadline_date") or (d.get("semester") and d.get("note"))):
                    dbm.upsert_deadline(conn, pid, d)
                    stats["deadlines"] += 1
            for s in ext.get("scholarships", []):
                pid = program_id_by_code.get(s.get("program_code"))
                if pid and s.get("name"):
                    dbm.upsert_scholarship(conn, pid, s, r["url"])
                    stats["scholarships"] += 1
            for m in ext.get("app_materials", []):
                pid = program_id_by_code.get(m.get("program_code"))
                if pid and m.get("material_type") and (m.get("requirement") or m.get("note")):
                    dbm.upsert_app_material(conn, pid, m)
                    stats["materials"] += 1

            # document_chunks
            page_chunks = chunks_by_url.get(r["url"], [])
            if page_chunks:
                dbm.replace_chunks(conn, university_id, page_id, school_id, r["url"],
                                   r.get("passed_types", []), page_chunks, main_program_id)
                stats["chunks"] += len(page_chunks)

            # review_queue（該頁驗證失敗的欄位）
            for item in state.get("review_items", []):
                if item.get("url") == r["url"]:
                    dbm.insert_review_item(conn, university_id, page_id, item)
                    stats["review"] += 1
    finally:
        conn.close()

    print(f"[DB] {school_id}: {stats}")
    return {"summary": {"db": stats}}


# ──────────────────────────────────────────────
# Node 14：finalize_school
# ──────────────────────────────────────────────

def finalize_school(state: SchoolState) -> dict:
    results = state.get("page_results", [])
    ok = sum(1 for r in results if r.get("status") == "ok")
    dropped = sum(1 for r in results if r.get("status") == "dropped")
    summary = {
        "school_id": state["school_id"],
        "skipped": bool(state.get("skip_school")),
        "urls_discovered": len(state.get("discovered_urls", [])),
        "pages_kept": ok,
        "pages_dropped": dropped,
        "review_items": len(state.get("review_items", [])),
        "chunks": len(state.get("chunks", [])),
        "sufficiency_iterations": state.get("sufficiency_iterations", 0),
        "finished_at": datetime.now().isoformat(),
    }
    print(f"\n{'='*60}\n[FINISH] {json.dumps(summary, ensure_ascii=False, indent=2)}\n{'='*60}")
    return {"summary": summary}
