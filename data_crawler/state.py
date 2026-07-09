"""LangGraph state 定義。

SchoolState  — 主圖（一間學校一個 thread_id，逐層 BFS + fan-out）
ScrapeState  — Node 4 fan-out 的單頁輸入
ProcessState — Node 5-8 頁面子圖的 state（單頁：分類 → 對應 program → 抽取 → 驗證）
"""
import operator
from typing import Annotated, Any, Optional
from typing_extensions import TypedDict


def merge_dict(a: Optional[dict], b: Optional[dict]) -> dict:
    out = dict(a or {})
    out.update(b or {})
    return out


class SchoolState(TypedDict, total=False):
    # ── 基本 ──────────────────────────────────────────
    school_id: str
    university_id: Optional[int]
    roots: list[str]
    max_depth: int
    max_pages: int
    skip_school: bool          # Node 1 SKIP_RECENT 判定
    dry_run: bool              # 不寫 DB（本機測試用）
    enable_embedding: bool     # Node 12 開關（預設關）
    skip_recent_hours: int     # >0 時啟用 SKIP_RECENT（查 DB last_extracted_at）
    known_program_codes: list[str]  # DB 既有 program_code（給 Node 6 參考）

    # ── Phase 1：BFS（逐層，跟著 checkpoint 走）──────────
    current_depth: int
    url_queue: list[dict]      # [{"url", "depth", "root_index"}] 目前這一層待爬
    visited_urls: list[str]    # 已入過佇列的 URL（續跑不重爬）
    discovered_urls: list[str] # 通過過濾、待進 Phase 2 的 URL
    dropped_urls: dict         # url -> reason
    external_urls: list[str]
    total_crawled: int

    # ── Phase 2：scrape fan-out 收集 ─────────────────────
    scraped_pages: Annotated[list[dict], operator.add]
    unique_pages: list[dict]   # content-hash 去重後（save_result.py 邏輯）

    # ── Phase 3-4：page pipeline fan-out 收集 ────────────
    page_results: Annotated[list[dict], operator.add]
    review_items: Annotated[list[dict], operator.add]

    # ── Phase 5：sufficiency ─────────────────────────────
    sufficiency_iterations: int
    max_sufficiency_iterations: int
    sufficiency_report: dict
    extra_seed_urls: list[str]

    # ── Phase 6 ──────────────────────────────────────────
    chunks: list[dict]
    summary: Annotated[dict, merge_dict]


class ScrapeState(TypedDict, total=False):
    """Send 到 scrape_page 的單頁輸入。"""
    school_id: str
    url: str
    depth: int


class ProcessState(TypedDict, total=False):
    """頁面子圖（Node 5-8）的 state。"""
    school_id: str
    university_id: Optional[int]
    page: dict                 # unique_pages 裡的一筆（含 full_text / structured_markdown ...）
    known_program_codes: list[str]
    scope: str                 # "page" | "school_wide"

    # Node 5
    classification: dict       # {"is_relevant": bool, "types": [{"type","confidence"}], "reason"}

    # Node 6
    program_codes: list[dict]  # [{"program_code","degree_type","program_name","department"}]

    # Node 7
    extraction: dict           # {"programs":[...], "deadlines":[...], "scholarships":[...], "app_materials":[...]}
    extraction_retries: int

    # Node 8
    validation: dict           # {"passed": bool, "issues": [...], "confidence": float}

    # 輸出（合併回主圖）
    page_results: Annotated[list[dict], operator.add]
    review_items: Annotated[list[dict], operator.add]


# 頁面分類類型（與使用者定案的 6+2 清單一致；faculty / other 分類後即丟棄）
KEEP_TYPES = ["admissions", "deadlines", "program", "tuition", "scholarship", "faq"]
DROP_TYPES = ["faculty", "other"]
ALL_TYPES = KEEP_TYPES + DROP_TYPES
