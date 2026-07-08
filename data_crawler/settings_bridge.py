"""橋接既有 crawler/setting 設定，避免複製一份設定造成雙重維護。

data_crawler 不修改 crawler/ 底下任何檔案，只透過 sys.path 讀取：
  - setting.parameter  → CONFIG / USER_AGENT / PAGE_HINTS / URL_PATH_HINTS ...
  - setting.blacklist  → IGNORED_EXTENSIONS / BLACKLIST_PATH_FRAGMENTS
  - setting.root_url   → SCHOOLS
  - clean_json_data    → KEYWORDS_TO_REMOVE / remove_keywords（清洗邏輯沿用）
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRAWLER_DIR = PROJECT_ROOT / "crawler"

if str(CRAWLER_DIR) not in sys.path:
    sys.path.insert(0, str(CRAWLER_DIR))

from setting.parameter import (  # noqa: E402
    CONFIG,
    USER_AGENT,
    PAGE_HINTS,
    URL_PATH_HINTS,
    MINUS_KEYWORDS,
    THRESHOLDS,
)
from setting.blacklist import (  # noqa: E402
    IGNORED_EXTENSIONS,
    BLACKLIST_PATH_FRAGMENTS,
)
from setting.root_url import SCHOOLS  # noqa: E402
from clean_json_data import KEYWORDS_TO_REMOVE, remove_keywords  # noqa: E402

__all__ = [
    "CONFIG",
    "USER_AGENT",
    "PAGE_HINTS",
    "URL_PATH_HINTS",
    "MINUS_KEYWORDS",
    "THRESHOLDS",
    "IGNORED_EXTENSIONS",
    "BLACKLIST_PATH_FRAGMENTS",
    "SCHOOLS",
    "KEYWORDS_TO_REMOVE",
    "remove_keywords",
    "PROJECT_ROOT",
]
