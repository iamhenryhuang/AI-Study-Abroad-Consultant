"""URL 工具 — URL 正規化、爬蟲邊界檢查與便宜的路徑訊號。

filter_url 只處理確定性的格式／檔案類型檢查；URL 是否與研究所資料相關，
由主圖的批次 LLM URL filter 決定。score_url_path 則保留給內容分類參考。
"""
from urllib.parse import urlparse, urlunparse

from .settings_bridge import (
    CONFIG,
    IGNORED_EXTENSIONS,
    URL_PATH_HINTS,
)


def normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
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


def is_same_host(url: str, root_info: dict) -> bool:
    """是否仍在同一官方 host；供 root path 外的高價值 sibling 頁使用。"""
    try:
        return urlparse(url).netloc.lower() == root_info["netloc"]
    except Exception:
        return False


def is_high_value_sibling_url(url: str, anchor_text: str = "") -> bool:
    """保守放行同 host、root path 外的明確申請資料頁。

    全站放行容易把新聞、課程與人員頁灌滿 MAX_PAGES；只靠嚴格 root path 又會
    漏掉常放在 /financial-support、/tuition 或 /international 的全校規則。
    URL path 或 anchor 任一具有強申請訊號即可交給後續 LLM URL filter 再判斷。
    """
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    evidence = f"{path} {(anchor_text or '').lower()}"
    non_applicant_hints = (
        "admitted-student", "admitted_student", "respond-offer",
        "current-student", "current_student", "undergraduate",
        "alumni", "graduation", "commencement",
    )
    if any(hint in evidence for hint in non_applicant_hints):
        return False
    strong_hints = (
        "admission", "apply", "application", "requirement", "deadline",
        "prospective", "international applicant", "international student",
        "english proficiency", "language proficiency", "toefl", "ielts",
        "duolingo", "transcript", "academic record", "recommendation",
        "statement of purpose", "application fee", "fee waiver",
        "tuition", "cost of attendance", "financial support", "financial aid",
        "funding", "scholarship", "fellowship",
    )
    return any(hint in evidence for hint in strong_hints)


def has_ignored_extension(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in IGNORED_EXTENSIONS)


def is_pdf(url: str) -> bool:
    return urlparse(url).path.lower().split("?")[0].endswith(".pdf")


def filter_url(url: str, allow_pdf: bool = False) -> tuple[bool, str]:
    """確定性 URL 檢查；不在此判斷內容是否與研究所資料相關。"""
    if not url.startswith(("http://", "https://")):
        return False, "non-http"
    if has_ignored_extension(url):
        if allow_pdf and is_pdf(url):
            pass
        else:
            return False, "ext"
    return True, "keep"


def application_scope_exclusion(url: str, anchor_text: str = "") -> str | None:
    """排除明確不是研究所申請資訊的 catalog／課程頁，避免送進 LLM 與全文爬取。"""
    path = urlparse(url).path.lower()
    evidence = f"{path} {anchor_text.lower()}"
    application_hints = (
        "admission", "apply", "application", "deadline", "english requirement",
        "language requirement", "tuition", "fee waiver", "funding", "scholarship",
        "fellowship", "financial aid", "toefl", "ielts", "duolingo", "gre",
        "recommendation", "statement of purpose",
    )
    if "/course/" in path:
        return "hard-drop: individual course page"
    if "/minor/" in path:
        return "hard-drop: undergraduate minor page"
    if "/browse/" in path:
        browse_keep_hints = (
            "admission", "apply", "application", "deadline", "tuition", "fee waiver",
            "funding", "scholarship", "fellowship", "financial aid", "toefl", "ielts", "gre",
        )
        if any(hint in evidence for hint in browse_keep_hints):
            return None
        return "hard-drop: catalog browse/index page without application evidence"
    strict_drop_tokens = (
        "/events", "/event/", "/news", "newsletter", "newsroom", "/announcements",
        "faculty-award", "faculty-recruit", "faculty-member", "/faculty", "/people",
        "research-area", "research-lab", "graduate-research", "research-center",
        "undergraduate", "student-life", "alumni", "current-fellowship",
        "current-student-funding", "timesheet", "job-opening",
        "financial_support/english-proficiency",
        "staff-position", "academic-and-staff-positions",
    )
    if any(token in evidence for token in strict_drop_tokens):
        return "hard-drop: non-application news/faculty/research/undergraduate page"
    if any(token in evidence for token in ("login", "sign-in", "privacy", "terms-of-use")):
        return "hard-drop: login/legal page"
    if any(token in evidence for token in ("dept-forms", "department-forms")) and not any(
            hint in evidence for hint in application_hints):
        return "hard-drop: generic department forms page without application evidence"
    if any(token in evidence for token in (
            "faculty-roster", "faculty roster", "research-centers", "research centers",
            "academic-advising", "academic advising", "honors", "course catalog")):
        return "hard-drop: non-application academic/faculty page"
    return None


def parse_url_path(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:
        return url.lower()


def score_url_path(url: str) -> dict:
    """URL 路徑加分（搬自 score.py），當作 LLM 分類的參考訊號。"""
    path = parse_url_path(url)
    bonuses = {pt: 0 for pt in URL_PATH_HINTS}
    for page_type, hints in URL_PATH_HINTS.items():
        for segment, pts in hints:
            if segment in path:
                bonuses[page_type] += pts
    return bonuses
