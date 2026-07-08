"""URL 工具 — 邏輯原封不動搬自 crawler/url_crawler.py 與 crawler/score.py。

normalize_url / get_root_info / is_same_root / filter_url 是既有已驗證的規則式
過濾，依 v4 規格直接沿用；score_url_path 保留為便宜訊號（餵給 LLM prompt 參考，
最終分類決定權在 LLM）。
"""
from urllib.parse import urlparse, urlunparse

from .settings_bridge import (
    CONFIG,
    IGNORED_EXTENSIONS,
    BLACKLIST_PATH_FRAGMENTS,
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


def has_ignored_extension(url: str) -> bool:
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in IGNORED_EXTENSIONS)


def is_pdf(url: str) -> bool:
    return urlparse(url).path.lower().split("?")[0].endswith(".pdf")


def filter_url(url: str, allow_pdf: bool = False) -> tuple[bool, str]:
    """黑名單過濾。allow_pdf=True 時放行 .pdf（Node 4 有 PDF 分支可處理）。"""
    if not url.startswith(("http://", "https://")):
        return False, "non-http"
    if has_ignored_extension(url):
        if allow_pdf and is_pdf(url):
            pass
        else:
            return False, "ext"
    full_low = url.lower()
    for frag in BLACKLIST_PATH_FRAGMENTS:
        if frag in full_low:
            return False, f"black:{frag}"
    return True, "keep"


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
