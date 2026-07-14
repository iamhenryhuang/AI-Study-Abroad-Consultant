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
from .url_tools import normalize_url, filter_url, application_scope_exclusion
from .browser import crawl_one_layer, scrape_url
from .text_clean import clean_noise
from .llm import call_llm_json
from .prompts import url_filter_prompt
from . import db as dbm

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
URL_RESULT_DIR = Path(__file__).resolve().parent / "url"

DEFAULT_MAX_SUFFICIENCY_ITERATIONS = 2
URL_FILTER_BATCH_SIZE = max(1, int(os.getenv("URL_FILTER_BATCH_SIZE", "40")))

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

    configured_roots = state.get("root_urls_override") or school["roots"]
    roots = [normalize_url(r) for r in configured_roots if r and r.startswith("http")]
    if not roots:
        raise ValueError(f"school_id={school_id} 沒有有效 root URL")
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
        "url_filter_candidates": [],
        "url_filter_decisions": [],
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

    candidates_by_url: dict[str, dict] = {}
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
                    candidates_by_url[child_url] = {
                        "url": child_url,
                        "source_url": url,
                        "anchor_text": result.get("keep_anchors", {}).get(child_url, ""),
                        "depth": dep + 1,
                        "root_index": root_index_by_url.get(url, 0),
                    }

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
        "url_queue": [],
        "visited_urls": sorted(visited),
        "discovered_urls": discovered,
        "dropped_urls": dropped,
        "external_urls": sorted(external),
        "total_crawled": total_crawled,
        "url_filter_candidates": list(candidates_by_url.values()),
    }


def _normalize_url_filter_batch(candidates: list[dict], raw: object) -> list[dict]:
    """把不可信的 LLM JSON 整理成一個候選 URL 對應一筆決策。"""
    expected = {item["url"]: item for item in candidates}
    raw_items = raw.get("decisions", []) if isinstance(raw, dict) else []
    received: dict[str, list[dict]] = {}
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict) or item.get("url") not in expected:
            continue
        received.setdefault(item["url"], []).append(item)

    normalized = []
    for url, candidate in expected.items():
        items = received.get(url, [])
        raw_decisions = {str(item.get("decision", "")).strip().lower() for item in items}
        reason = next((str(item.get("reason", "")).strip() for item in items
                       if str(item.get("reason", "")).strip()), "")
        try:
            confidence = max(float(item.get("confidence", 0)) for item in items)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        fallback_reason = ""
        if not items:
            decision, fallback_reason = "keep", "LLM 未回傳此 URL，依保守策略保留"
        elif "keep" in raw_decisions:
            decision = "keep"
            if len(items) > 1:
                fallback_reason = "LLM 重複回傳且至少一筆為 keep，依保留優先策略保留"
        elif raw_decisions != {"drop"}:
            decision, fallback_reason = "keep", "LLM decision 格式無效，依保守策略保留"
        elif not reason:
            decision, fallback_reason = "keep", "LLM 未提供過濾理由，依保守策略保留"
        else:
            # decision/reason 格式合法時尊重 LLM 的 drop；confidence 只供 audit，
            # 不可再把明確 drop 因低信心自動翻成 keep。
            decision = "drop"

        normalized.append({
            **candidate,
            "decision": decision,
            "reason": fallback_reason or reason or "LLM 未提供理由，依保守策略保留",
            "confidence": confidence,
            "decision_source": "fallback" if fallback_reason else "llm",
        })
    return normalized


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    temp.replace(path)


def _write_url_filter_review(state: SchoolState, decisions: list[dict]) -> str:
    """每層覆寫完整 audit，並依 keep/drop 拆檔供人工排查。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{state['school_id']}_url_filter_review.json"
    kept = sum(1 for item in decisions if item["decision"] == "keep")
    payload = {
        "school_id": state["school_id"],
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total": len(decisions),
            "kept": kept,
            "dropped": len(decisions) - kept,
            "fallback_kept": sum(1 for item in decisions
                                 if item.get("decision_source") == "fallback"),
        },
        "decisions": decisions,
    }
    _write_json_atomic(out, payload)

    common = {
        "school_id": state["school_id"],
        "generated_at": payload["generated_at"],
    }
    keep_items = [item for item in decisions if item["decision"] == "keep"]
    drop_items = [item for item in decisions if item["decision"] == "drop"]
    _write_json_atomic(
        URL_RESULT_DIR / "keep" / f"{state['school_id']}.json",
        {**common, "decision": "keep", "count": len(keep_items), "urls": keep_items},
    )
    _write_json_atomic(
        URL_RESULT_DIR / "drop" / f"{state['school_id']}.json",
        {**common, "decision": "drop", "count": len(drop_items), "urls": drop_items},
    )
    # all_url 刻意只存 URL 字串陣列：root + keep/drop 前完整候選清單。
    all_urls = list(dict.fromkeys([
        *state.get("roots", []),
        *(item["url"] for item in decisions if item.get("url")),
    ]))
    _write_json_atomic(
        URL_RESULT_DIR / "all_url" / f"{state['school_id']}.json",
        all_urls,
    )
    return str(out)


def filter_discovered_urls(state: SchoolState) -> dict:
    """批次 LLM 初篩本層新 URL，整理成下一層 BFS queue 並寫 audit。"""
    candidates = state.get("url_filter_candidates", [])
    previous = list(state.get("url_filter_decisions", []))
    if not candidates:
        _write_url_filter_review(state, previous)
        return {"url_queue": [], "url_filter_candidates": []}

    current: list[dict] = []
    llm_candidates = []
    for item in candidates:
        exclusion = application_scope_exclusion(item["url"], item.get("anchor_text", ""))
        if exclusion:
            current.append({**item, "decision": "drop", "reason": exclusion,
                            "confidence": 1.0, "decision_source": "rule"})
        else:
            llm_candidates.append(item)

    for start in range(0, len(llm_candidates), URL_FILTER_BATCH_SIZE):
        batch = llm_candidates[start:start + URL_FILTER_BATCH_SIZE]
        try:
            raw = call_llm_json(url_filter_prompt(state["school_id"], state["roots"], batch))
            current.extend(_normalize_url_filter_batch(batch, raw))
        except Exception as exc:
            reason = f"LLM 批次判斷失敗，依保守策略保留：{str(exc)[:200]}"
            current.extend([{**item, "decision": "keep", "reason": reason,
                             "confidence": 0.0, "decision_source": "fallback"}
                            for item in batch])

    queue = [{"url": item["url"], "depth": item["depth"],
              "root_index": item["root_index"]}
             for item in current if item["decision"] == "keep"]
    dropped = dict(state.get("dropped_urls", {}))
    for item in current:
        if item["decision"] == "drop":
            dropped[item["url"]] = f"llm:{item['reason']}"

    decisions = previous + current
    audit_file = _write_url_filter_review(state, decisions)
    print(f"  [URL FILTER] batches={(len(llm_candidates) + URL_FILTER_BATCH_SIZE - 1) // URL_FILTER_BATCH_SIZE} "
          f"keep={len(queue)} drop={len(current) - len(queue)} audit={audit_file}")
    return {
        "url_queue": queue,
        "dropped_urls": dropped,
        "url_filter_candidates": [],
        "url_filter_decisions": decisions,
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
    """依 URL 去重並過濾錯誤頁；content hash 僅供診斷，不跨 URL 丟資料。

    不同官方頁可能因共用模板、載入失敗或舊 checkpoint 缺少 hash 而得到
    相同/空 content_hash。跨 URL 直接 hash 去重會漏掉 admissions requirements，
    因此只有完全相同 URL 才跳過。
    """
    first_url_by_hash: dict[str, str] = {}
    seen_urls: set[str] = set()
    unique = []
    processed_urls = {p["url"] for p in state.get("page_results", [])}

    for rec in state.get("scraped_pages", []):
        if rec.get("error") or not rec.get("full_text"):
            continue
        if rec["url"] in processed_urls:
            continue  # sufficiency 迴圈時不重複處理
        if rec["url"] in seen_urls:
            print(f"  ⏭ duplicate URL skipped: {rec['url'][:80]}")
            continue
        seen_urls.add(rec["url"])

        h = rec.get("content_hash")
        if h:
            first_url = first_url_by_hash.get(h)
            if first_url and first_url != rec["url"]:
                print(f"  ⚠️ same content hash retained: {rec['url'][:70]} "
                      f"(same as {first_url[:70]})")
            else:
                first_url_by_hash[h] = rec["url"]
        else:
            print(f"  ⚠️ missing content hash retained: {rec['url'][:80]}")
        unique.append(rec)

    print(f"[COLLECT] {len(unique)} 頁待分類（URL 去重/去錯誤後）")
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
# Node 9：sufficiency_evaluator（只產生覆蓋率報告，不再動態補爬）
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
            "missing_important": [
                f for f in IMPORTANT_FIELDS
                if f not in got["fields"]
                and not (f == "toefl_ibt_min" and "toefl_min" in got["fields"])
            ],
        }
    return report


def _registrable_domain(netloc: str) -> str:
    parts = netloc.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


def sufficiency_evaluator(state: SchoolState) -> dict:
    iterations = state.get("sufficiency_iterations", 0)
    report = _coverage_report(state.get("page_results", []))
    missing = sorted({field for item in report.values()
                      for field in item.get("missing_important", [])})
    if not report:
        missing_summary = "沒有任何結構化抽取結果"
    elif missing:
        missing_summary = "缺少：" + ", ".join(missing)
    else:
        missing_summary = "重要欄位已齊全"
    result = {
        "sufficient": bool(report) and not missing,
        "missing_summary": missing_summary,
        "seed_urls": [],
        "auto_recrawl_disabled": True,
    }
    print(f"[SUFFICIENCY] sufficient={result['sufficient']} "
          f"missing={result['missing_summary'][:100]}（自動補爬已關閉）")

    return {
        "sufficiency_iterations": iterations + 1,
        "sufficiency_report": {"coverage": report, **result},
        "extra_seed_urls": [],
    }


def check_sufficiency(state: SchoolState) -> str:
    """資料不足只記錄報告，不再加入 seed URL 或重跑 BFS。"""
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

def _validate_school_write_scope(state: SchoolState, ok_results: list[dict]) -> None:
    """阻止 checkpoint/state 混線時把 A 校 URL 寫入 B 校。"""
    school_id = state["school_id"]
    school = next((item for item in SCHOOLS if item["school_id"] == school_id), None)
    if not school:
        raise RuntimeError(f"DB 寫入中止：找不到 school_id={school_id} 的 root 設定")

    allowed_domains = {
        _registrable_domain(urlparse(root).netloc)
        for root in school.get("roots", []) if root and root.startswith("http")
    }
    if not allowed_domains:
        raise RuntimeError(f"DB 寫入中止：school_id={school_id} 沒有有效 root domain")

    scoped_urls = [
        *(state.get("roots") or []),
        *(result.get("url", "") for result in ok_results),
        *(chunk.get("url", "") for chunk in state.get("chunks", [])),
    ]
    invalid = [url for url in scoped_urls
               if url and _registrable_domain(urlparse(url).netloc) not in allowed_domains]
    if invalid:
        samples = ", ".join(invalid[:5])
        raise RuntimeError(
            f"DB 寫入中止：{school_id} state 混入其他學校 domain；"
            f"允許={sorted(allowed_domains)}，異常 URL={samples}"
        )


def _common_source_priority(result: dict) -> int:
    """同 scope 衝突時弱來源先寫、強來源後寫；DB 最後保留強證據。"""
    text = f"{result.get('url', '')} {result.get('title', '')}".lower()
    if "checklist" in text:
        return 50
    if "faq" in text:
        return 20
    if "requirement" in text or "admission" in text:
        return 40
    if "english" in text or "language" in text:
        return 35
    return 10


def db_writer(state: SchoolState) -> dict:
    school_id = state["school_id"]
    ok_results = [r for r in state.get("page_results", []) if r.get("status") == "ok"]
    _validate_school_write_scope(state, ok_results)

    # 無論是否 dry-run，都輸出一份可供人工稽核的 JSON。DB 模式不能只把
    # 原文寫進 web_pages，否則不連 DB 時無法判斷是爬蟲或 LLM 漏資料。
    out = OUTPUT_DIR / f"{school_id}_result.json"
    payload = {
        "school_id": school_id,
        "generated_at": datetime.now().isoformat(),
        "page_results": [{k: v for k, v in r.items()
                          if k not in ("full_text", "structured_markdown")} | {
                             "data": r.get("full_text", ""),
                             "char_count": len(r.get("full_text", ""))}
                         for r in state.get("page_results", [])],
        "review_items": state.get("review_items", []),
        "url_filter_decisions": state.get("url_filter_decisions", []),
        "sufficiency_report": state.get("sufficiency_report", {}),
        "chunk_count": len(state.get("chunks", [])),
    }
    _write_json_atomic(out, payload)
    print(f"[OUTPUT] 完整結果與原文寫入 {out}")

    if state.get("dry_run"):
        return {"summary": {"db": "dry_run", "output_file": str(out)}}

    conn = dbm.get_connection()
    # 不信任 checkpoint 內可能過期的 university_id；每次寫入都依 school_id 重新解析。
    university_id = dbm.get_or_create_university(conn, school_id)
    stale_id = state.get("university_id")
    if stale_id is not None and stale_id != university_id:
        print(f"  ⚠️ stale university_id corrected: state={stale_id} db={university_id} ({school_id})")
    stats = {"pages": 0, "programs": 0, "global_extractions": 0,
             "deadlines": 0, "scholarships": 0,
             "materials": 0, "review": 0, "chunks": 0}
    chunks_by_url: dict[str, list] = {}
    for c in state.get("chunks", []):
        chunks_by_url.setdefault(c["url"], []).append(c)

    try:
        program_id_by_code: dict[str, int] = {}

        # 第一階段：先彙整有官方 heading 證據的 program，避免全校表單下拉選單
        # 誤建空殼。接著把 SCHOOL_WIDE 共用欄位先寫入；後面的個別頁面值可覆蓋它。
        verified_meta: dict[str, dict] = {}
        school_common_results = []
        department_common_results = []
        for result in ok_results:
            for meta in result.get("program_codes", []):
                code = meta.get("program_code")
                if code == "SCHOOL_WIDE":
                    school_common_results.append(result)
                elif code == "CS_DEPARTMENT_WIDE":
                    department_common_results.append(result)
                elif code and meta.get("official_evidence"):
                    verified_meta.setdefault(code, meta)

        # 最後保底：若整校沒有任何可建立的正式 CS program，仍建立一個 CS MS，
        # 讓已驗證的全校／CS 系級共用申請資料有正式承接目標，不再全部停在 global_extractions。
        if not verified_meta:
            verified_meta["CS MS"] = {
                "program_code": "CS MS",
                "degree_type": "MS",
                "program_name": "Master of Science in Computer Science",
                "department": "Computer Science",
                "official_evidence": "default fallback: no official CS program was identified",
                "is_default": True,
            }
            print(f"  [program fallback] {school_id} → 建立預設 CS MS")

        for code, meta in verified_meta.items():
            program_id_by_code[code] = dbm.upsert_program(
                conn, university_id, code, meta, {}, "", None,
            )

        # 優先序：全校 fallback < CS/CSE department；同層 checklist 最強。
        common_results = [
            *sorted(school_common_results, key=_common_source_priority),
            *sorted(department_common_results, key=_common_source_priority),
        ]
        for result in common_results:
            for prog in (result.get("extraction") or {}).get("programs", []):
                if prog.get("program_code") not in ("SCHOOL_WIDE", "CS_DEPARTMENT_WIDE"):
                    continue
                fields = {name: value.get("value")
                          for name, value in (prog.get("fields") or {}).items()
                          if isinstance(value, dict)}
                for code, meta in verified_meta.items():
                    program_id_by_code[code] = dbm.upsert_program(
                        conn, university_id, code, meta, fields,
                        result["url"], result.get("confidence"),
                    )

        for r in ok_results:
            ext = r.get("extraction") or {}
            meta_by_code = {p["program_code"]: p for p in r.get("program_codes", [])
                            if p.get("program_code") in verified_meta}

            # programs（欄位非 null 才覆蓋）
            for prog in ext.get("programs", []):
                code = prog.get("program_code")
                if not code or code in ("SCHOOL_WIDE", "CS_DEPARTMENT_WIDE") or code not in verified_meta:
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

            # 全校／CS 系級抽取結果獨立保存。即使目前沒有任何正式 program，
            # 仍可從 DB 稽核，未來建立 program 後也能重新套用。
            global_codes = [p for p in r.get("program_codes", [])
                            if p.get("program_code") in ("SCHOOL_WIDE", "CS_DEPARTMENT_WIDE")]
            if global_codes and ext:
                for meta in global_codes:
                    code = meta["program_code"]
                    scoped_extraction = {
                        "programs": [p for p in ext.get("programs", [])
                                     if p.get("program_code") == code],
                        "deadlines": [p for p in ext.get("deadlines", [])
                                      if p.get("program_code") == code],
                        "scholarships": [p for p in ext.get("scholarships", [])
                                         if p.get("program_code") == code],
                        "app_materials": [p for p in ext.get("app_materials", [])
                                          if p.get("program_code") == code],
                    }
                    if any(scoped_extraction.values()):
                        dbm.upsert_global_extraction(
                            conn, university_id, page_id,
                            meta.get("scope") or r.get("scope") or "school_wide",
                            code, scoped_extraction, r.get("confidence"), r["url"],
                        )
                        stats["global_extractions"] += 1

            # 子表：查詢後決定 insert/update（v4 3-1）；過濾掉沒有實質內容的空殼列
            for d in ext.get("deadlines", []):
                pid = program_id_by_code.get(d.get("program_code"))
                if pid and (d.get("deadline_date") or d.get("application_open_date") or d.get("application_close_date")
                            or d.get("decision_release_date")
                            or (d.get("semester") and d.get("note"))):
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

        # 全校通用的 deadline / scholarship / material 也分發到已確認的 programs。
        for result in common_results:
            ext = result.get("extraction") or {}
            for code, pid in program_id_by_code.items():
                for item in ext.get("deadlines", []):
                    if item.get("program_code") in ("SCHOOL_WIDE", "CS_DEPARTMENT_WIDE") and (
                            item.get("deadline_date") or item.get("application_open_date") or item.get("application_close_date")
                            or item.get("decision_release_date")
                            or (item.get("semester") and item.get("note"))):
                        dbm.upsert_deadline(conn, pid, {**item, "program_code": code})
                        stats["deadlines"] += 1
                for item in ext.get("scholarships", []):
                    if item.get("program_code") in ("SCHOOL_WIDE", "CS_DEPARTMENT_WIDE") and item.get("name"):
                        dbm.upsert_scholarship(conn, pid, {**item, "program_code": code}, result["url"])
                        stats["scholarships"] += 1
                for item in ext.get("app_materials", []):
                    if item.get("program_code") in ("SCHOOL_WIDE", "CS_DEPARTMENT_WIDE") and item.get("material_type") and (
                            item.get("requirement") or item.get("note")):
                        dbm.upsert_app_material(conn, pid, {**item, "program_code": code})
                        stats["materials"] += 1
    finally:
        conn.close()

    print(f"[DB] {school_id}: {stats}")
    return {"summary": {"db": stats, "output_file": str(out)}}


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
        "urls_llm_kept": sum(1 for item in state.get("url_filter_decisions", [])
                             if item.get("decision") == "keep"),
        "urls_llm_dropped": sum(1 for item in state.get("url_filter_decisions", [])
                                if item.get("decision") == "drop"),
        "pages_kept": ok,
        "pages_dropped": dropped,
        "review_items": len(state.get("review_items", [])),
        "chunks": len(state.get("chunks", [])),
        "sufficiency_iterations": state.get("sufficiency_iterations", 0),
        "finished_at": datetime.now().isoformat(),
    }
    print(f"\n{'='*60}\n[FINISH] {json.dumps(summary, ensure_ascii=False, indent=2)}\n{'='*60}")
    return {"summary": summary}
