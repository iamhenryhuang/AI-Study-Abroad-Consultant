"""頁面子圖：Node 5（LLM 分類）→ Node 6（對應 program）→ Node 7（結構化抽取）
→ Node 8（幻覺驗證，含重試迴圈）。

一個 Send 分支處理一頁；輸出 page_results / review_items 合併回主圖。
"""
import re

from .state import ProcessState, KEEP_TYPES
from .url_tools import score_url_path
from .llm import call_llm_json
from .prompts import classification_prompt, identify_programs_prompt, extraction_prompt

CLASSIFY_EXCERPT_CHARS = 8_000
EXTRACT_EXCERPT_CHARS = 28_000
MAX_EXTRACTION_RETRIES = 2
CONFIDENCE_THRESHOLD = 0.6  # 分類信心低於此值視為不相關


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
    return None


# ──────────────────────────────────────────────
# Node 5：content_classification（取代 score_page 關鍵字評分）
# ──────────────────────────────────────────────

def content_classification(state: ProcessState) -> dict:
    page = state["page"]
    url = page["url"]
    text = _page_text(page)
    excerpt = text[:CLASSIFY_EXCERPT_CHARS]

    if len(excerpt.strip()) < 80:
        return {"classification": {"is_relevant": False, "types": [{"type": "other", "confidence": 1.0}],
                                   "reason": "頁面內容過少"}}

    # score_url_path 保留為便宜訊號，放進 prompt 供 LLM 參考（最終決定權在 LLM）
    bonuses = score_url_path(url)
    override = _common_requirements_override(url, text)
    try:
        result = call_llm_json(classification_prompt(url, page.get("title", ""), bonuses, excerpt))
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
    return {"classification": {
        "is_relevant": bool(result.get("is_relevant")),
        "types": types,
        "reason": result.get("reason", ""),
    }}


def is_relevant_content(state: ProcessState) -> str:
    """條件邊：不相關（含 faculty / other / 低信心）→ 丟棄；相關 → Node 6。"""
    cls = state.get("classification") or {}
    keep_types = [t for t in cls.get("types", [])
                  if t["type"] in KEEP_TYPES and float(t.get("confidence", 0)) >= CONFIDENCE_THRESHOLD]
    if cls.get("is_relevant") and keep_types:
        return "identify_programs"
    return "discard_page"


def discard_page(state: ProcessState) -> dict:
    """丟棄記錄（只留摘要資訊供 finalize 統計，不進 DB）。"""
    page = state["page"]
    cls = state.get("classification") or {}
    return {"page_results": [{
        "url": page["url"],
        "status": "dropped",
        "types": cls.get("types", []),
        "reason": cls.get("reason", ""),
    }]}


# ──────────────────────────────────────────────
# Node 6：identify_programs
# ──────────────────────────────────────────────

def identify_programs(state: ProcessState) -> dict:
    page = state["page"]
    text = _page_text(page)[:CLASSIFY_EXCERPT_CHARS]
    known = state.get("known_program_codes") or []

    try:
        result = call_llm_json(identify_programs_prompt(page["url"], page.get("title", ""), text, known))
    except Exception as e:
        print(f"  ⚠️ identify_programs 失敗（{page['url']}）：{e}")
        result = {"school_wide": False, "programs": []}

    programs = [p for p in result.get("programs", []) if isinstance(p, dict) and p.get("program_code")]
    school_wide = bool(result.get("school_wide"))

    # admissions/語言/學費等共用頁常不會寫出特定 program 名稱；LLM 即使漏標
    # school_wide，也要套用既有目標 program，否則抓到全文卻完全不做欄位抽取。
    classification = state.get("classification") or {}
    generic_types = {"admissions", "deadlines", "tuition", "scholarship", "faq"}
    classified_types = {t.get("type") for t in classification.get("types", [])
                        if isinstance(t, dict)}
    if not programs and classified_types & generic_types:
        school_wide = True

    if not programs and school_wide:
        # 全校通用頁：套用到既有 program（DB 已知的），都沒有時以 CS MS 為主要目標
        codes = known or ["CS MS"]
        programs = [{"program_code": c, "scope": "school_wide"} for c in codes]

    return {"program_codes": programs,
            "extraction_retries": 0,
            "scope": "school_wide" if school_wide else "page"}


def has_program_target(state: ProcessState) -> str:
    """條件邊：找不到任何 program → 該頁只保留原文（供 RAG chunk），不做欄位抽取。"""
    if state.get("program_codes"):
        return "structured_extraction"
    return "finalize_page"


# ──────────────────────────────────────────────
# Node 7：structured_extraction
# ──────────────────────────────────────────────

def structured_extraction(state: ProcessState) -> dict:
    page = state["page"]
    codes = [p["program_code"] for p in state.get("program_codes", [])]
    markdown = _page_text(page)
    if page.get("structured_tables"):
        markdown = markdown + "\n\n## 表格\n" + page["structured_tables"]
    markdown = markdown[:EXTRACT_EXCERPT_CHARS]

    feedback = None
    validation = state.get("validation")
    if validation and validation.get("issues"):
        feedback = "\n".join(
            f"- {i.get('program_code','?')}.{i.get('field_name','?')}: {i.get('problem','')}"
            for i in validation["issues"][:20]
        )

    try:
        extraction = call_llm_json(extraction_prompt(page["url"], codes, markdown, feedback))
    except Exception as e:
        print(f"  ⚠️ structured_extraction 失敗（{page['url']}）：{e}")
        extraction = {}

    return {"extraction": extraction or {},
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
    # 容錯：取前 60 字再試一次（LLM 偶爾在結尾多字）
    return len(e) > 60 and e[:60] in haystack


def _digits_of(value) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", str(value))


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
        return False

    # 日期 YYYY-MM-DD：月份常以英文寫出，只要求年與日
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        y, _m, dd = str(value).split("-")
        return present(y) and present(str(int(dd)))
    return all(present(d) for d in digits)


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
                               "problem": "source_excerpt 在原文找不到"})
            elif not _value_supported_by_excerpt(fval["value"], excerpt):
                issues.append({"program_code": code, "field_name": fname,
                               "field_value": fval["value"], "source_excerpt": excerpt,
                               "problem": "數值與 source_excerpt 對不起來"})

    for list_name in ("deadlines", "scholarships", "app_materials"):
        for item in extraction.get(list_name, []):
            total += 1
            excerpt = item.get("source_excerpt", "")
            check_value = item.get("deadline_date") or item.get("amount_usd") or item.get("word_limit")
            field_label = f"{list_name}[{item.get('deadline_type') or item.get('name') or item.get('material_type') or '?'}]"
            if not _excerpt_in_source(excerpt, haystack):
                issues.append({"program_code": item.get("program_code"), "field_name": field_label,
                               "field_value": check_value, "source_excerpt": excerpt,
                               "problem": "source_excerpt 在原文找不到"})
            elif check_value is not None and not _value_supported_by_excerpt(check_value, excerpt):
                issues.append({"program_code": item.get("program_code"), "field_name": field_label,
                               "field_value": check_value, "source_excerpt": excerpt,
                               "problem": "數值/日期與 source_excerpt 對不起來"})

    confidence = 1.0 if total == 0 else round(1.0 - len(issues) / total, 2)
    return {"validation": {"passed": not issues, "issues": issues,
                           "confidence": confidence, "total_fields": total}}


def extraction_quality_check(state: ProcessState) -> str:
    """條件邊：有問題且未達重試上限 → 回 Node 7；否則 → finalize（問題欄位進 review_queue）。"""
    validation = state.get("validation") or {}
    if not validation.get("passed") and state.get("extraction_retries", 0) <= MAX_EXTRACTION_RETRIES:
        return "structured_extraction"
    return "finalize_page"


# ──────────────────────────────────────────────
# finalize：把單頁結果整理成主圖要收集的格式
# ──────────────────────────────────────────────

def _strip_invalid_fields(extraction: dict, issues: list[dict]) -> dict:
    """把驗證失敗的欄位從抽取結果移除（它們會進 review_queue，不進正式表）。"""
    bad = {(i.get("program_code"), i.get("field_name")) for i in issues}
    out = {"programs": [], "deadlines": [], "scholarships": [], "app_materials": []}

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
    return out


def finalize_page(state: ProcessState) -> dict:
    page = state["page"]
    cls = state.get("classification") or {}
    validation = state.get("validation") or {}
    issues = validation.get("issues", [])
    extraction = _strip_invalid_fields(state.get("extraction") or {}, issues)

    passed_types = [{"type": t["type"], "score": round(float(t.get("confidence", 0)), 2)}
                    for t in cls.get("types", []) if t["type"] in KEEP_TYPES]

    review_items = [{**i, "url": page["url"], "reason": "hallucination_detected"} for i in issues]

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
        "extraction": extraction,
        "confidence": validation.get("confidence", 1.0),
        "retries": state.get("extraction_retries", 0),
    }
    return {"page_results": [result], "review_items": review_items}
