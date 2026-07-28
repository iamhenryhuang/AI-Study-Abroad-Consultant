"""頁面子圖：Node 5（LLM 分類）→ Node 6（對應 program）→ Node 7（結構化抽取）
→ Node 8（幻覺驗證，含重試迴圈）。

一個 Send 分支處理一頁；輸出 page_results / review_items 合併回主圖。
"""
import json
import re

from .state import ProcessState, KEEP_TYPES
from .url_tools import score_url_path
from .llm import call_llm_json, llm_is_unavailable
from .prompts import classification_prompt, extraction_prompt, validation_repair_prompt
from .demo_events import emit_event, preview

CLASSIFY_EXCERPT_CHARS = 12_000
EXTRACT_CHUNK_CHARS = 26_000
EXTRACT_CHUNK_OVERLAP_CHARS = 2_000
EXTRACT_FOCUSED_CHARS = 14_000
MAX_EXTRACTION_RETRIES = 2
MAX_VALIDATION_REPAIRS = 2
CONFIDENCE_THRESHOLD = 0.6  # 分類信心低於此值視為不相關
TARGET_PROGRAM_CODE = "INTERNATIONAL_CS_MASTERS"


def _page_text(page: dict) -> str:
    """以 Playwright 完整文字為主，補上 markdown 的標題／表格結構。

    UCLA 等網站的 accordion 內容會存在 full_text，卻常被 trafilatura markdown
    省略；不能再用 `structured_markdown or full_text` 讓短 markdown 蓋掉全文。
    """
    full_text = page.get("full_text") or ""
    markdown = page.get("structured_markdown") or ""
    if full_text and markdown:
        return f"{full_text}\n\n## Structured page view\n{markdown}"
    return full_text or markdown


def _common_requirements_override(url: str, text: str) -> tuple[str, str] | None:
    """用強 URL + 內容證據保住適用所有目標 program 的共用申請頁。"""
    path = url.lower()
    low = text[:12_000].lower()
    rules = [
        ("admissions",
         ("english-requirement", "english-language", "language-requirement", "proficiency"),
         ("toefl", "ielts", "english language proficiency"),
         "頁面明確包含研究所英語檢定／語言門檻，屬於目標學程共用申請資訊"),
        ("tuition",
         ("tuition", "fees", "living-expenses", "cost-of-attendance"),
         ("tuition", "student fees", "cost of attendance"),
         "頁面明確包含研究所學費或就學成本，屬於目標學程共用資訊"),
        ("deadlines",
         ("deadline", "calendar"),
         ("application deadline", "deadline"),
         "頁面明確包含研究所申請截止日期或時程"),
        ("admissions",
         ("required-academic-record", "transcript", "materials-to-be-uploaded",
          "recommendation", "steps-to-apply", "application-for-graduate"),
         ("transcript", "letter of recommendation", "application materials", "graduate application"),
         "頁面明確包含研究所申請文件或流程，屬於目標學程共用資訊"),
        ("scholarship",
         ("scholarship", "fellowship", "financial-aid", "funding"),
         ("scholarship", "fellowship", "financial aid", "funding"),
         "頁面明確包含研究所獎助或 funding 資訊"),
    ]
    for page_type, path_hints, content_hints, reason in rules:
        if any(hint in path for hint in path_hints) and any(hint in low for hint in content_hints):
            return page_type, reason

    # Preserve curated graduate-application pages for FTS when LLM quota is gone.
    strong_path = any(hint in path for hint in (
        '/admission', '/application', '/requirements', '/english',
        '/statement-of-purpose', '/sop', '/recommendation',
    ))
    application_signal = any(hint in low for hint in (
        'graduate application', 'application requirement', 'application process',
        'statement of purpose', 'letter of recommendation', 'international applicant',
        'toefl', 'ielts', 'admission requirement',
    ))
    if strong_path and application_signal:
        if 'faq' in path:
            return 'faq', 'Graduate application FAQ preserved for full-text retrieval'
        return 'admissions', 'Graduate application page preserved for full-text retrieval'
    return None


# ──────────────────────────────────────────────
# Node 5：content_classification（取代 score_page 關鍵字評分）
# ──────────────────────────────────────────────

def content_classification(state: ProcessState) -> dict:
    page = state["page"]
    url = page["url"]
    text = _page_text(page)
    excerpts = _split_extraction_text(text)
    emit_event(state["school_id"], "content_classification", "running",
               "LLM 正在分類頁面", url=url,
               data={"title": page.get("title", ""), "parts": len(excerpts)})

    if len(text.strip()) < 80:
        return {"classification": {"is_relevant": False, "types": [{"type": "other", "confidence": 1.0}],
                                   "reason": "頁面內容過少"}}

    low = _norm(f"{page.get('title', '')} {text[:12_000]}")
    if (any(term in low for term in (
            "no terminal master", "no terminal masters", "does not offer a terminal master",
            "doctoral program only", "application is for the doctoral program only",
    )) and any(term in low for term in ("computer science", " eecs ", " cse "))):
        return {"classification": {
            "is_relevant": True,
            "types": [{"type": "program", "confidence": 1.0}],
            "reason": "Official CS page explicitly indicates that a terminal master's is unavailable.",
        }}

    # score_url_path 保留為便宜訊號，放進 prompt 供 LLM 參考（最終決定權在 LLM）
    bonuses = score_url_path(url)
    override = _common_requirements_override(url, text)
    try:
        partials = [call_llm_json(classification_prompt(
            url, page.get("title", ""), bonuses,
            f"[Part {i}/{len(excerpts)} of the same page]\n{excerpt}",
        )) for i, excerpt in enumerate(excerpts, 1)]
        # 全文分段判斷；任一段有充分的申請語意即可保留，類別信心取各段最高值。
        best_types: dict[str, dict] = {}
        reasons = []
        for partial in partials:
            reasons.append(str(partial.get("reason") or ""))
            for item in partial.get("types", []):
                if not isinstance(item, dict) or not item.get("type"):
                    continue
                old = best_types.get(item["type"])
                if old is None or float(item.get("confidence", 0)) > float(old.get("confidence", 0)):
                    best_types[item["type"]] = item
        result = {
            "is_relevant": any(bool(p.get("is_relevant")) for p in partials),
            "types": list(best_types.values()),
            "reason": " | ".join(r for r in reasons if r)[:2000],
        }
    except Exception as e:
        if override:
            page_type, reason = override
            print(f"  [classification fallback] {url} → {page_type}（LLM 失敗）")
            return {"classification": {
                "is_relevant": True,
                "types": [{"type": page_type, "confidence": 1.0}],
                "reason": f"{reason}；LLM 分類失敗時由程式證據保留",
            }}
        return {"classification": {"is_relevant": False, "types": [],
                                   "reason": f"LLM 分類失敗：{e}"}}

    types = [t for t in result.get("types", [])
             if isinstance(t, dict) and t.get("type")]
    has_keep_type = any(t.get("type") in KEEP_TYPES for t in types)
    if override and (not result.get("is_relevant") or not has_keep_type):
        page_type, reason = override
        print(f"  [classification override] {url} → {page_type}")
        result["is_relevant"] = True
        types = [{"type": page_type, "confidence": 1.0}]
        result["reason"] = reason
    classification = {
        "is_relevant": bool(result.get("is_relevant")),
        "types": types,
        "reason": result.get("reason", ""),
    }
    emit_event(state["school_id"], "content_classification", "completed",
               "頁面分類完成", url=url, data=classification)
    return {"classification": classification}


def is_relevant_content(state: ProcessState) -> str:
    """條件邊：不相關（含 faculty / other / 低信心）→ 丟棄；相關 → Node 6。"""
    cls = state.get("classification") or {}
    keep_types = [t for t in cls.get("types", [])
                  if t["type"] in KEEP_TYPES and float(t.get("confidence", 0)) >= CONFIDENCE_THRESHOLD]
    if cls.get("is_relevant") and keep_types:
        return "identify_programs"
    return "discard_page"


def discard_page(state: ProcessState) -> dict:
    """丟棄記錄（保留爬蟲全文供結果 JSON 診斷，但不進 DB）。"""
    page = state["page"]
    cls = state.get("classification") or {}
    emit_event(state["school_id"], "content_classification", "dropped",
               "頁面未進入抽取", url=page["url"], data={
                   "types": cls.get("types", []), "reason": cls.get("reason", ""),
               })
    return {"page_results": [{
        "url": page["url"],
        "status": "dropped",
        "types": cls.get("types", []),
        "reason": cls.get("reason", ""),
        "full_text": page.get("full_text", ""),
    }]}


# ──────────────────────────────────────────────
# Node 6：identify_programs
# ──────────────────────────────────────────────

def identify_programs(state: ProcessState) -> dict:
    """把所有相關頁映射到「國際 CS 碩士」單一目標，不再以 program heading 阻斷抽取。

    program-specific 名稱只作來源 metadata；全校、系級及特定 CS master's 頁面
    都可以提供欄位。PhD-only、特殊 BS/MS 與在校生任用頁仍不應污染申請標準。
    """
    page = state["page"]
    text = _page_text(page)
    url_lower = page["url"].lower()
    title_lower = page.get("title", "").lower()

    # 特殊管道與任用頁保留原文，但不得提供一般國際 CS 碩士入學門檻。
    excluded_hints = (
        "bachelorsmasters", "bachelor-master", "bs-ms", "bs/ms", "bsms",
        "teaching-assistant", "applying-teaching", "ta-position",
        "qualifying-exam", "dissertation", "current-student",
    )
    if any(hint in f"{url_lower} {title_lower}" for hint in excluded_hints):
        return {"program_codes": [], "extraction_retries": 0, "scope": "reference_only"}

    heading_text = " ".join([
        page.get("title", ""),
        *(page.get("h1_list") or []),
        *(page.get("h2_list") or []),
        *(page.get("h3_list") or []),
    ])
    heading_norm = _norm(heading_text)
    low = _norm(f"{heading_text} {text[:8_000]}")
    master_signal = any(term in low for term in (
        "master of science", "master's", "masters", " m.s.", " ms program",
        "m.sc", "msc", "meng", "m.eng", "mcs",
    ))
    no_terminal_master = any(term in low for term in (
        "no terminal master", "no terminal masters", "does not offer a terminal master",
        "doctoral program only", "application is for the doctoral program only",
    ))
    # Do not infer that a master's is unavailable merely because a generic
    # graduate page mentions PhD/doctoral/postdoctoral study.  Navigation and
    # school-wide admissions pages routinely contain those words.  A negative
    # target assessment must be backed by an explicit official statement.
    if no_terminal_master:
        return {
            "program_codes": [],
            "extraction_retries": 0,
            "scope": "masters_unavailable",
            "target_assessment": {
                "masters_available": False,
                "reason": "Official page is PhD-only or explicitly states no terminal master's program.",
            },
        }

    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url_lower).split("/", 1)[0])
    department_signal = (
        host.startswith(("cs.", "cse."))
        or any(term in heading_norm for term in (
            "computer science", "computer engineering", " cse ", " eecs ", " scs ",
        ))
    )
    program_specific = master_signal and department_signal
    scope = "program_specific" if program_specific else (
        "department_wide" if department_signal else "school_wide"
    )
    target = {
        "program_code": TARGET_PROGRAM_CODE,
        "degree_type": "MS",
        "program_name": "International Computer Science Master's",
        "department": "Computer Science",
        "scope": scope,
        "official_evidence": heading_text[:500] or page.get("title", ""),
        "source_program_name": page.get("title", "") if program_specific else None,
    }
    result = {"program_codes": [target],
            "extraction_retries": 0,
            "scope": scope,
            "target_assessment": {"masters_available": True}}
    emit_event(state.get("school_id", "unknown"), "identify_programs", "completed",
               f"套用 {scope} 的國際 CS 碩士目標", url=page["url"],
               data={"scope": scope, "program_code": TARGET_PROGRAM_CODE})
    return result


def has_program_target(state: ProcessState) -> str:
    """條件邊：找不到任何 program → 該頁只保留原文（供 RAG chunk），不做欄位抽取。"""
    if state.get("program_codes"):
        return "structured_extraction"
    return "finalize_page"


# ──────────────────────────────────────────────
# Node 7：structured_extraction
# ──────────────────────────────────────────────

def _split_extraction_text(text: str) -> list[str]:
    """將全文切成重疊片段；優先在換行或空白處斷開。"""
    if not text:
        return []
    if len(text) <= EXTRACT_CHUNK_CHARS:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        hard_end = min(start + EXTRACT_CHUNK_CHARS, len(text))
        end = hard_end
        if hard_end < len(text):
            search_from = start + EXTRACT_CHUNK_CHARS // 2
            newline = text.rfind("\n", search_from, hard_end)
            space = text.rfind(" ", search_from, hard_end)
            boundary = max(newline, space)
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - EXTRACT_CHUNK_OVERLAP_CHARS)
    return chunks


def _merge_item(existing: dict, incoming: dict) -> None:
    """以先出現的非空值為主，並用後續片段補齊缺漏欄位。"""
    for key, value in incoming.items():
        if existing.get(key) in (None, "", []) and value not in (None, "", []):
            existing[key] = value


def _list_item_key(list_name: str, item: dict) -> str:
    if list_name == "deadlines":
        fields = ("program_code", "deadline_type", "semester")
    elif list_name == "scholarships":
        fields = ("program_code", "name")
    elif list_name == "evidence_paragraphs":
        fields = ("program_code", "category", "field_name")
    else:
        fields = ("program_code", "material_type", "requirement", "word_limit")
    values = tuple(str(item.get(field) or "").strip().lower() for field in fields)
    # 欄位不足時不要把不同的空殼資料誤合併。
    return repr(values) if any(values) else json.dumps(item, ensure_ascii=False, sort_keys=True)


def _merge_extractions(parts: list[dict]) -> dict:
    """合併同一頁各片段的抽取結果，並維持首次出現順序。"""
    merged = {"programs": [], "deadlines": [], "scholarships": [],
              "app_materials": [], "evidence_paragraphs": []}
    programs_by_code: dict[str, dict] = {}

    for part in parts:
        if not isinstance(part, dict):
            continue
        for program in part.get("programs", []):
            if not isinstance(program, dict) or not program.get("program_code"):
                continue
            code = program["program_code"]
            target = programs_by_code.get(code)
            if target is None:
                target = {"program_code": code, "fields": {}}
                programs_by_code[code] = target
                merged["programs"].append(target)
            for field, field_value in (program.get("fields") or {}).items():
                current = target["fields"].get(field)
                if current is None or (isinstance(current, dict) and current.get("value") is None):
                    target["fields"][field] = field_value

        for list_name in ("deadlines", "scholarships", "app_materials", "evidence_paragraphs"):
            existing_by_key = {_list_item_key(list_name, item): item
                               for item in merged[list_name]}
            for item in part.get(list_name, []):
                if not isinstance(item, dict):
                    continue
                key = _list_item_key(list_name, item)
                if key in existing_by_key:
                    _merge_item(existing_by_key[key], item)
                else:
                    copied = dict(item)
                    merged[list_name].append(copied)
                    existing_by_key[key] = copied
    return merged


def _normalize_target_codes(extraction: dict, program_codes: list[str]) -> dict:
    """單一目標模式下，不接受 LLM 自行創造或沿用舊版 program code。"""
    if not isinstance(extraction, dict) or not program_codes:
        return extraction if isinstance(extraction, dict) else {}
    target = program_codes[0]
    for program in extraction.get("programs", []):
        if isinstance(program, dict):
            program["program_code"] = target
    for list_name in ("deadlines", "scholarships", "app_materials", "evidence_paragraphs"):
        for item in extraction.get(list_name, []):
            if isinstance(item, dict):
                item["program_code"] = target
    return extraction


_EXTRACTION_TERMS = re.compile(
    r"toefl|ielts|duolingo|english proficiency|language waiver|gre|gpa|grade point|"
    r"transcript|academic record|recommendation|reference letter|statement of purpose|"
    r"personal statement|\bsop\b|curriculum vitae|\bcv\b|resume|writing sample|"
    r"application fee|fee waiver|tuition|cost of attendance|deadline|due date|"
    r"scholarship|fellowship|funding|financial aid|application portal|apply online|"
    r"international applicant|admission requirement",
    re.IGNORECASE,
)


def _focus_extraction_text(text: str) -> str:
    """保留申請欄位關鍵詞前後文，降低導覽列與重複 DOM 對 LLM 的干擾。"""
    matches = list(_EXTRACTION_TERMS.finditer(text))
    if not matches:
        return text[:EXTRACT_FOCUSED_CHARS]

    intervals = []
    for match in matches:
        start = max(0, match.start() - 700)
        end = min(len(text), match.end() + 1_100)
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))
    # 不截掉後方命中的證據；一般頁面會大幅縮短，關鍵詞極密集時最多仍是原 chunk 大小。
    return "\n\n...\n\n".join(text[start:end] for start, end in intervals)

def structured_extraction(state: ProcessState) -> dict:
    page = state["page"]
    codes = [p["program_code"] for p in state.get("program_codes", [])]
    codes.sort(key=lambda code: (not any(degree in code.upper().split()
                                         for degree in ("MS", "MSC", "MENG", "MCS", "MDS")), code))
    markdown = _page_text(page)
    if page.get("structured_tables"):
        markdown = markdown + "\n\n## 表格\n" + page["structured_tables"]
    chunks = _split_extraction_text(markdown)
    emit_event(state["school_id"], "structured_extraction", "running",
               f"LLM 正在抽取頁面（{len(chunks)} 段）", url=page["url"],
               data={"program_codes": codes})

    feedback = None
    validation = state.get("validation")
    if validation and validation.get("issues"):
        feedback = "\n".join(
            f"- {i.get('program_code','?')}.{i.get('field_name','?')}: {i.get('problem','')}"
            for i in validation["issues"][:20]
        )

    extractions = []
    for index, chunk in enumerate(chunks, start=1):
        if llm_is_unavailable():
            break
        # 每個片段完整輸入。關鍵詞不再裁掉未命中的文字，讓 LLM 依上下文語意判斷。
        focused_chunk = chunk
        try:
            extraction = call_llm_json(extraction_prompt(
                page["url"], codes,
                f"[同一網頁第 {index}/{len(chunks)} 段；已保留欄位相關上下文]\n"
                f"{focused_chunk}", feedback,
            ))
            if isinstance(extraction, dict):
                extractions.append(_normalize_target_codes(extraction, codes))
        except Exception as e:
            if not llm_is_unavailable():
                print(f"  [WARN] structured_extraction 第 {index}/{len(chunks)} 段失敗"
                      f"（{page['url']}）：{e}")

    extraction = _promote_grounded_evidence(_merge_extractions(extractions))
    if len(chunks) > 1:
        print(f"  [extraction] {page['url']} → {len(chunks)} 段已合併")
    field_names = sorted({
        field
        for program in extraction.get("programs", [])
        for field in (program.get("fields") or {})
    })
    emit_event(state["school_id"], "structured_extraction", "completed",
               f"抽取完成：{len(field_names)} 個結構化欄位", url=page["url"], data={
                   "fields": field_names,
                   "programs": extraction.get("programs", []),
                   "deadlines": extraction.get("deadlines", []),
                   "evidence_count": len(extraction.get("evidence_paragraphs", [])),
               })

    return {"extraction": extraction,
            "extraction_retries": state.get("extraction_retries", 0) + 1}


# ──────────────────────────────────────────────
# Node 8：hallucination_validation（程式化逐欄位比對原文）
# ──────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower().strip()


def _excerpt_in_source(excerpt: str, haystack: str) -> bool:
    e = _norm(excerpt)
    if not e:
        return False
    if e in haystack:
        return True
    # 允許標點、連字號、空白差異與輕微語序/省略；避免要求逐字完全一致。
    words = re.findall(r"[a-z0-9]+", e)
    source_words = set(re.findall(r"[a-z0-9]+", haystack))
    meaningful = [w for w in words if len(w) > 2 or w.isdigit()]
    if not meaningful:
        return False
    overlap = sum(1 for w in meaningful if w in source_words) / len(meaningful)
    # DOM/table 正規化常會少掉欄名或標點；其餘數值與欄位語意檢查仍會把關。
    return overlap >= 0.55


def _digits_of(value) -> list[str]:
    # 金額常含千分位逗號：$51,226 必須能支持資料庫數值 51226。
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", str(value))
    return re.findall(r"\d+(?:\.\d+)?", normalized)


def _value_supported_by_excerpt(value, excerpt: str) -> bool:
    """數字/日期/金額類：值的數字必須出現在 source_excerpt 內。"""
    digits = _digits_of(value)
    if not digits:
        return True  # 純文字欄位只要求 excerpt 存在
    ex = excerpt or ""
    ex_digits = set(_digits_of(ex))

    def present(d: str) -> bool:
        if d in ex_digits or d in ex:
            return True
        # 3.0 vs 3、3.50 vs 3.5 的容錯
        if "." in d:
            trimmed = d.rstrip("0").rstrip(".")
            return trimmed in ex_digits or trimmed in ex
        number_words = {
            "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
            "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
            "10": "ten", "11": "eleven", "12": "twelve",
        }
        if number_words.get(d) and re.search(rf"\b{number_words[d]}\b", ex, re.I):
            return True
        return False

    # 日期 YYYY-MM-DD：要求年、英文月份與日期出現在同一日期片段。
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        y, month, dd = str(value).split("-")
        month_name = ("january", "february", "march", "april", "may", "june",
                      "july", "august", "september", "october", "november", "december")[int(month) - 1]
        day = str(int(dd))
        low = ex.lower()
        # 要求年、月、日屬於同一個日期片段，避免從同一句其他日期拼湊年份或日數。
        textual = re.search(
            rf"(?:{month_name}\s+{day}(?:st|nd|rd|th)?\D{{0,20}}{y}|"
            rf"{y}\D{{0,20}}{month_name}\s+{day}(?:st|nd|rd|th)?)",
            low,
        )
        numeric = any(token in low for token in (
            f"{y}-{month}-{dd}", f"{int(month)}/{int(dd)}/{y}", f"{month}/{dd}/{y}",
        ))
        return bool(textual or numeric)
    return all(present(d) for d in digits)


def _field_semantics_supported(field_name: str, excerpt: str) -> bool:
    """避免把 Duolingo、TOEFL speaking/TA 門檻錯填為 admission overall TOEFL。"""
    low = _norm(excerpt)
    admission_only_fields = {
        "toefl_min", "toefl_ibt_min", "toefl_ibt_new_scale_min",
        "toefl_section_requirements", "ielts_min", "duolingo_min",
        "language_waiver", "english_test_notes", "gre_required",
        "gre_quant_min", "gre_verbal_min", "gre_awa_min", "gpa_min",
        "gpa_scale", "gpa_note", "rec_letter_count", "sop_word_limit",
        "sop_prompt", "cv_required", "writing_sample_required",
        "application_fee_usd", "application_url", "application_system",
    }
    post_admission_signals = (
        "current student", "current students", "enrolled student", "enrolled students",
        "during the course of their studies", "maintain a cumulative", "remain in good standing",
        "maintain a minimum", "good academic standing", "satisfactory academic progress",
        "satisfactory progress toward", "academic probation",
        "after enrollment", "after enrolment", "degree requirements", "graduation requirement",
        "graduate writing exam", "qualifying exam",
    )
    prospective_signals = ("applicant", "application", "admission", "apply")
    if (field_name in admission_only_fields
            and any(signal in low for signal in post_admission_signals)
            and not any(signal in low for signal in prospective_signals)):
        return False
    if field_name in ("toefl_min", "toefl_ibt_min", "toefl_ibt_new_scale_min"):
        if "toefl" not in low and "internet-based test" not in low:
            return False
        if any(term in low for term in ("spoken toefl", "speaking score", "ta position",
                                        "teaching assistant")):
            return False
        if field_name == "toefl_ibt_new_scale_min":
            # 新制必須有 1–6 scale/band/new score 等明確制度證據，避免把 IELTS 5 誤填。
            if not any(term in low for term in ("1-6", "1-to-6", "1 – 6", "1 to 6",
                                                 "new scale", "new scoring scale",
                                                 "updated scale", "band score",
                                                 "on or after january 21 2026",
                                                 "on or after january 21, 2026",
                                                 "starting january 21 2026",
                                                 "starting january 21, 2026")):
                return False
    elif field_name == "duolingo_min" and not any(term in low for term in (
            "duolingo", "det score", "english test (det", "english test (det;")):
        return False
    elif field_name == "ielts_min" and "ielts" not in low:
        return False
    elif field_name in ("tuition_per_year_usd", "tuition_note"):
        if not any(term in low for term in ("tuition", "cost", "fee", "$", "usd")):
            return False
        if field_name == "tuition_per_year_usd" and not any(
                term in low for term in ("per year", "annual", "/year", "/yr")):
            return False
    elif field_name == "application_url":
        if not re.search(r"(?:https?://)?[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?", excerpt,
                         re.IGNORECASE):
            return False
    elif field_name == "sop_word_limit":
        # schema 是 word limit，characters/pages 不可被當成字數。
        if any(term in low for term in ("character", "characters", "page", "pages")) \
                and not any(term in low for term in ("word", "words")):
            return False
    return True


_NUMBER_WORD_VALUES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}
_MONTH_VALUES = {
    name: index for index, name in enumerate(
        ("january", "february", "march", "april", "may", "june",
         "july", "august", "september", "october", "november", "december"),
        1,
    )
}


def _promote_grounded_evidence(extraction: dict) -> dict:
    """將 evidence 中無歧義的推薦信數量與完整日期提升回結構化欄位。"""
    programs = extraction.setdefault("programs", [])
    deadlines = extraction.setdefault("deadlines", [])

    def program_for(code: str) -> dict:
        for program in programs:
            if program.get("program_code") == code:
                return program
        program = {"program_code": code, "fields": {}}
        programs.append(program)
        return program

    existing_deadlines = {
        (item.get("program_code"), item.get("application_close_date"))
        for item in deadlines
    }
    for item in extraction.get("evidence_paragraphs", []):
        code = item.get("program_code")
        excerpt = item.get("source_excerpt") or item.get("evidence_text") or ""
        low = _norm(excerpt)
        field_name = item.get("field_name") or ""

        if code and (field_name == "rec_letter_count" or "letter" in low) \
                and any(term in low for term in ("recommendation", "reference")) \
                and any(term in low for term in ("required", "must submit", "at least")):
            match = re.search(
                r"\b(?:at least\s+)?(\d+|one|two|three|four|five)\b"
                r"(?:(?:\s+\w+){0,3})?\s+(?:letters?|recommendations?|references?)\b",
                low,
            )
            if match:
                raw = match.group(1)
                count = int(raw) if raw.isdigit() else _NUMBER_WORD_VALUES.get(raw)
                if count:
                    fields = program_for(code).setdefault("fields", {})
                    fields.setdefault("rec_letter_count", {
                        "value": count,
                        "source_excerpt": excerpt,
                    })

        if not code or (item.get("category") != "deadline" and "deadline" not in low):
            continue
        for match in re.finditer(
            r"\b(" + "|".join(_MONTH_VALUES) + r")\s+(\d{1,2})(?:st|nd|rd|th)?"
            r"\s*,?\s*(20\d{2})\b",
            low,
        ):
            month_name, day, year = match.groups()
            date_value = f"{year}-{_MONTH_VALUES[month_name]:02d}-{int(day):02d}"
            if (code, date_value) in existing_deadlines:
                continue
            nearby = low[max(0, match.start() - 80):match.end() + 80]
            if any(term in nearby for term in (
                "earliest valid test", "latest valid test", "test date",
                "score validity", "scores valid",
            )):
                continue
            if not any(term in nearby for term in ("deadline", "application", "start")):
                continue
            # 取日期之前最近的 term，避免同一段列 Spring/Fall 時交叉綁定。
            prefix = low[max(0, match.start() - 120):match.start()]
            term_matches = list(re.finditer(
                r"\b(fall|spring|summer|winter)\s+(20\d{2})\b", prefix
            ))
            term_match = term_matches[-1] if term_matches else None
            deadlines.append({
                "program_code": code,
                "deadline_type": "regular",
                "application_open_date": None,
                "application_close_date": date_value,
                "decision_release_date": None,
                "semester": (
                    f"{term_match.group(1)}_{term_match.group(2)}" if term_match else None
                ),
                "note": item.get("evidence_text") or excerpt,
                "source_excerpt": excerpt,
            })
            existing_deadlines.add((code, date_value))
    return extraction


def _excerpt_context(excerpt: str, haystack: str, radius: int = 1000) -> str:
    """Return nearby source text so table rows can inherit their test/header label.

    Official tables often put ``TOEFL`` or ``IELTS`` in a preceding header while
    the extracted row contains only ``Minimum score: 7``.  The excerpt itself
    remains the grounding evidence; nearby text is used only for field semantics.
    """
    normalized_excerpt = _norm(excerpt)
    if not normalized_excerpt:
        return ""
    normalized_haystack = _norm(haystack)
    position = normalized_haystack.find(normalized_excerpt)
    if position < 0:
        # LLM excerpts may faithfully compress a table row whose cells are not
        # contiguous in DOM text. Locate a distinctive leading fragment so the
        # surrounding column headers remain available for semantic checks.
        words = normalized_excerpt.split()
        for width in (8, 6, 4, 2):
            if len(words) < width:
                continue
            fragment = " ".join(words[:width])
            position = normalized_haystack.find(fragment)
            if position >= 0:
                break
        else:
            return normalized_excerpt
    start = max(0, position - radius)
    end = min(len(normalized_haystack), position + len(normalized_excerpt) + radius)
    return normalized_haystack[start:end]


def _deadline_semantics_supported(item: dict, excerpt: str) -> bool:
    """Reject non-admission and non-actionable records from program deadlines."""
    values = [
        item.get("application_open_date"),
        item.get("application_close_date"),
        item.get("decision_release_date"),
    ]
    if not any(values):
        return False
    low = _norm(excerpt)
    if any(term in low for term in (
            "fee waiver request", "scholarship", "fellowship", "award commencing",
            "tuition deposit", "recommendation letter deadline")):
        return False
    return any(term in low for term in (
        "application", "applications", "admission", "entry", "enrollment",
        "deadline", "decision",
    ))


def hallucination_validation(state: ProcessState) -> dict:
    page = state["page"]
    extraction = state.get("extraction") or {}
    haystack = _norm(" ".join([
        page.get("full_text", ""),
        page.get("structured_markdown", ""),
        page.get("structured_tables", ""),
    ]))

    issues = []
    total = 0

    for prog in extraction.get("programs", []):
        code = prog.get("program_code")
        for fname, fval in (prog.get("fields") or {}).items():
            if not isinstance(fval, dict) or fval.get("value") is None:
                continue
            total += 1
            excerpt = fval.get("source_excerpt", "")
            if not _excerpt_in_source(excerpt, haystack):
                issues.append({"program_code": code, "field_name": fname,
                               "field_value": fval["value"], "source_excerpt": excerpt,
                               "problem": "source_excerpt is not sufficiently grounded in the source"})
            elif (isinstance(fval["value"], (int, float))
                  and not isinstance(fval["value"], bool)
                  and not _value_supported_by_excerpt(fval["value"], excerpt)):
                issues.append({"program_code": code, "field_name": fname,
                               "field_value": fval["value"], "source_excerpt": excerpt,
                               "problem": "the numeric value is not supported by source_excerpt"})
            elif not _field_semantics_supported(
                    fname, f"{_excerpt_context(excerpt, haystack)} {excerpt}"):
                issues.append({"program_code": code, "field_name": fname,
                               "field_value": fval["value"], "source_excerpt": excerpt,
                               "problem": "source_excerpt does not semantically support this field"})
            elif fname == "toefl_ibt_new_scale_min" and (
                    not isinstance(fval["value"], (int, float))
                    or not 1 <= float(fval["value"]) <= 6
                    or float(fval["value"]) * 2 % 1 != 0):
                issues.append({"program_code": code, "field_name": fname,
                               "field_value": fval["value"], "source_excerpt": excerpt,
                               "problem": "the updated TOEFL score must be 1-6 in 0.5 increments"})

    for list_name in ("deadlines", "scholarships", "app_materials"):
        for item in extraction.get(list_name, []):
            total += 1
            excerpt = item.get("source_excerpt", "")
            deadline_values = [item.get(name) for name in (
                "application_open_date", "application_close_date", "decision_release_date",
            ) if item.get(name)]
            check_value = (deadline_values or item.get("amount_usd") or item.get("word_limit"))
            field_label = f"{list_name}[{item.get('deadline_type') or item.get('name') or item.get('material_type') or '?'}]"
            if not _excerpt_in_source(excerpt, haystack):
                issues.append({"program_code": item.get("program_code"), "field_name": field_label,
                               "field_value": check_value, "source_excerpt": excerpt,
                               "problem": "source_excerpt was not found verbatim in the source"})
            elif isinstance(check_value, list) and not all(
                    _value_supported_by_excerpt(value, excerpt) for value in check_value):
                issues.append({"program_code": item.get("program_code"), "field_name": field_label,
                               "field_value": check_value, "source_excerpt": excerpt,
                               "problem": "one or more dates are not supported by source_excerpt"})
            elif check_value is not None and not isinstance(check_value, list) and not _value_supported_by_excerpt(check_value, excerpt):
                issues.append({"program_code": item.get("program_code"), "field_name": field_label,
                               "field_value": check_value, "source_excerpt": excerpt,
                               "problem": "the numeric value or date is not supported by source_excerpt"})
            elif list_name == "deadlines" and not _deadline_semantics_supported(item, excerpt):
                issues.append({"program_code": item.get("program_code"), "field_name": field_label,
                               "field_value": check_value, "source_excerpt": excerpt,
                               "problem": "not an actionable program application deadline"})

    for item in extraction.get("evidence_paragraphs", []):
        total += 1
        excerpt = item.get("source_excerpt", "")
        if not item.get("evidence_text") or not _excerpt_in_source(excerpt, haystack):
            issues.append({
                "program_code": item.get("program_code"),
                "field_name": f"evidence[{item.get('category') or 'other'}]",
                "field_value": item.get("evidence_text"),
                "source_excerpt": excerpt,
                "problem": "evidence paragraph is not grounded in the source",
            })

    confidence = 1.0 if total == 0 else round(1.0 - len(issues) / total, 2)
    validation = {"passed": not issues, "issues": issues,
                  "confidence": confidence, "total_fields": total}
    emit_event(state["school_id"], "hallucination_validation",
               "completed" if not issues else "warning",
               f"驗證 {total} 項，發現 {len(issues)} 項需修正",
               url=page["url"], data=validation)
    return {"validation": validation}


def extraction_quality_check(state: ProcessState) -> str:
    if llm_is_unavailable():
        return 'finalize_page'
    """至少進行一次原文語意修正；仍有問題時最多再修正一次。"""
    validation = state.get("validation") or {}
    repairs = state.get("validation_repair_retries", 0)
    if repairs == 0 or (not validation.get("passed") and repairs < MAX_VALIDATION_REPAIRS):
        return "semantic_repair"
    return "finalize_page"


def semantic_repair(state: ProcessState) -> dict:
    """以完整原文分批修正錯誤與補漏，再交回程式驗證。"""
    page = state["page"]
    current = state.get("extraction") or {}
    issues = (state.get("validation") or {}).get("issues", [])
    codes = [p["program_code"] for p in state.get("program_codes", [])]
    source = _page_text(page)
    if page.get("structured_tables"):
        source += "\n\n## Tables\n" + page["structured_tables"]
    parts = _split_extraction_text(source)
    emit_event(state["school_id"], "semantic_repair", "running",
               f"針對 {len(issues)} 個問題進行語意修正", url=page["url"],
               data={"issues": issues, "parts": len(parts)})

    repairs = []
    for index, part in enumerate(parts, 1):
        if llm_is_unavailable():
            break
        try:
            result = call_llm_json(validation_repair_prompt(
                page["url"], codes, current, issues, part, index, len(parts),
            ))
            if isinstance(result, dict):
                repairs.append(_normalize_target_codes(result, codes))
        except Exception as exc:
            if not llm_is_unavailable():
                print(f"  [WARN] semantic_repair 第 {index}/{len(parts)} 段失敗"
                      f"（{page['url']}）：{exc}")

    # 先移除已知無效值，讓有原文證據的修正值可以補回；既有有效值維持優先。
    valid_current = _strip_invalid_fields(current, issues)
    _append_issue_evidence(valid_current, issues, page)
    repaired = _promote_grounded_evidence(_merge_extractions([valid_current, *repairs]))
    print(f"  [validation repair] {page['url']} → {len(parts)} 段完成修正")
    emit_event(state["school_id"], "semantic_repair", "completed",
               "語意修正完成，重新交給驗證節點", url=page["url"], data={
                   "field_preview": preview(repaired),
               })
    return {
        "extraction": repaired,
        "validation_repair_retries": state.get("validation_repair_retries", 0) + 1,
    }


# ──────────────────────────────────────────────
# finalize：把單頁結果整理成主圖要收集的格式
# ──────────────────────────────────────────────

def _strip_invalid_fields(extraction: dict, issues: list[dict]) -> dict:
    """把驗證失敗的欄位從抽取結果移除（它們會進 review_queue，不進正式表）。"""
    bad = {(i.get("program_code"), i.get("field_name")) for i in issues}
    out = {"programs": [], "deadlines": [], "scholarships": [],
           "app_materials": [], "evidence_paragraphs": []}

    for prog in extraction.get("programs", []):
        code = prog.get("program_code")
        fields = {f: v for f, v in (prog.get("fields") or {}).items()
                  if isinstance(v, dict) and v.get("value") is not None and (code, f) not in bad}
        if fields:
            out["programs"].append({"program_code": code, "fields": fields})

    for list_name in ("deadlines", "scholarships", "app_materials"):
        for item in extraction.get(list_name, []):
            label = f"{list_name}[{item.get('deadline_type') or item.get('name') or item.get('material_type') or '?'}]"
            if (item.get("program_code"), label) not in bad:
                out[list_name].append(item)
    out["evidence_paragraphs"] = [
        item for item in extraction.get("evidence_paragraphs", [])
        if (item.get("program_code"),
            f"evidence[{item.get('category') or 'other'}]") not in bad
    ]
    return out


def _evidence_category(field_name: str) -> str:
    low = (field_name or "").lower()
    if "deadline" in low:
        return "deadline"
    if any(term in low for term in ("toefl", "ielts", "duolingo", "english", "language")):
        return "english"
    if "gpa" in low:
        return "gpa"
    if "gre" in low:
        return "gre"
    if "fee" in low:
        return "fee"
    if any(term in low for term in ("transcript", "recommend", "sop", "cv", "material")):
        return "materials"
    return "other"


def _issues_as_evidence(issues: list[dict], page: dict) -> list[dict]:
    """Preserve grounded rejected values as contextual paragraphs for RAG."""
    haystack = _norm(" ".join([
        page.get("full_text", ""),
        page.get("structured_markdown", ""),
        page.get("structured_tables", ""),
    ]))
    evidence = []
    for issue in issues:
        excerpt = issue.get("source_excerpt", "")
        if not excerpt or not _excerpt_in_source(excerpt, haystack):
            continue
        field_name = issue.get("field_name") or "other"
        context = _excerpt_context(excerpt, haystack, radius=700)
        evidence.append({
            "program_code": issue.get("program_code"),
            "category": _evidence_category(field_name),
            "field_name": field_name,
            "evidence_kind": "validator_rejected",
            "evidence_text": context or excerpt,
            "source_excerpt": excerpt,
        })
    return evidence


def _append_issue_evidence(extraction: dict, issues: list[dict], page: dict) -> None:
    """Add validator evidence unless the LLM already preserved the same fact."""
    target = extraction.setdefault("evidence_paragraphs", [])
    for candidate in _issues_as_evidence(issues, page):
        candidate_excerpt = _norm(candidate.get("source_excerpt", ""))
        duplicate = any(
            item.get("category") == candidate.get("category")
            and (
                candidate_excerpt in _norm(item.get("source_excerpt", ""))
                or _norm(item.get("source_excerpt", "")) in candidate_excerpt
            )
            for item in target
            if item.get("source_excerpt")
        )
        if not duplicate:
            target.append(candidate)


def _issue_resolved_by_extraction(issue: dict, extraction: dict) -> bool:
    """已由 grounded evidence 補回正式欄位的 issue 不再留在人工 review。"""
    code = issue.get("program_code")
    field_name = issue.get("field_name") or ""
    if field_name.startswith("deadlines["):
        expected = issue.get("field_value")
        expected_dates = set(expected if isinstance(expected, list) else [expected])
        expected_dates.discard(None)
        actual_dates = {
            value
            for item in extraction.get("deadlines", [])
            if item.get("program_code") == code
            for value in (
                item.get("application_open_date"),
                item.get("application_close_date"),
                item.get("decision_release_date"),
            )
            if value
        }
        return bool(expected_dates) and expected_dates.issubset(actual_dates)

    for program in extraction.get("programs", []):
        if program.get("program_code") != code:
            continue
        field = (program.get("fields") or {}).get(field_name)
        if isinstance(field, dict) and field.get("value") is not None:
            return True
    return False


def finalize_page(state: ProcessState) -> dict:
    page = state["page"]
    cls = state.get("classification") or {}
    validation = state.get("validation") or {}
    issues = validation.get("issues", [])
    extraction = _strip_invalid_fields(state.get("extraction") or {}, issues)
    _append_issue_evidence(extraction, issues, page)
    # 最後一次驗證才轉成 evidence 的明確數值，也要有機會回到正式欄位。
    extraction = _promote_grounded_evidence(extraction)
    unresolved_issues = [
        issue for issue in issues
        if not _issue_resolved_by_extraction(issue, extraction)
    ]

    passed_types = [{"type": t["type"], "score": round(float(t.get("confidence", 0)), 2)}
                    for t in cls.get("types", []) if t["type"] in KEEP_TYPES]

    review_items = [
        {**i, "url": page["url"], "reason": "hallucination_detected"}
        for i in unresolved_issues
    ]

    result = {
        "url": page["url"],
        "title": page.get("title", ""),
        "status": "ok",
        "passed_types": passed_types,
        "types": cls.get("types", []),
        "full_text": page.get("full_text", ""),
        "structured_markdown": page.get("structured_markdown", ""),
        "content_hash": page.get("content_hash", ""),
        "program_codes": state.get("program_codes", []),
        "scope": state.get("scope", "page"),
        "target_assessment": state.get("target_assessment", {}),
        "extraction": extraction,
        "confidence": validation.get("confidence", 1.0),
        "retries": state.get("extraction_retries", 0),
    }
    emit_event(state["school_id"], "finalize_page", "completed",
               f"頁面處理完成，保留 {len(unresolved_issues)} 個人工檢視項",
               url=page["url"], data={
                   "types": passed_types,
                   "scope": result["scope"],
                   "extraction": extraction,
                   "review_items": review_items,
               })
    return {"page_results": [result], "review_items": review_items}
