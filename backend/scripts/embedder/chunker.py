"""
策略：
1. 雜訊清洗：移除網頁常見的 Cookie 聲明、導覽碎片等。
2. 上下文注入：為每個 chunk 加上 [學校 | 類型] 前綴，提升向量檢索精度。
3. FAQ 強化：更精確的問答對切分與合併邏輯。
4. 分層切分：優化 RecursiveCharacterTextSplitter 的分隔符號順序。
"""

from __future__ import annotations

import re
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter

_PAGE_TYPE_SIZES: dict[str, int] = {
    "faq":               1800,   # FAQ 通常較長，但需控制在 Token 限制內
    "checklist":         1000,   # 條列式，較小 chunk 以免混雜無關項目
    "requirements":      1000,
    "admissions":        1500,
    "apply":             1500,
    "accepting":         1400,
    "professor_profile": 1800,   # 保留完整個人資訊
    "professor_paper":   800,    # 短 chunk 適合單篇論文
    "general":           1400,
}

_DEFAULT_CHUNK_SIZE = 1400
_MIN_OVERLAP = 150
_SEPARATORS = [
    "\n\n", 
    "\n", 
    "---", 
    "***", 
    ". ", 
    "! ", 
    "? ", 
    "; ", 
    "。 ", 
    "！ ", 
    "？ ", 
    "； ", 
    "\t",
    " ", 
    ""
]

# ── 雜訊清洗 ────────────────────────────────────────────────────

_NOISE_RE = [
    # 移除 UIUC / Stanford 等常見 Web Cookie Notice
    (r"X\s+We use Cookies on this site.*?About Cookies", ""),
    (r"University of Illinois System Cookie Policy.*?Close", ""),
    (r"Stanford University \(link is external\)", ""),
    (r"Search this site|Submit Search|Back to Top", ""),
    (r"Jump to navigation|Skip to main content", ""),
    (r"Link opens in a new window", ""),
    (r"\(link is external\)", ""),
    # 移除連續多個換行與空白
    (r"\n{3,}", "\n\n"),
    (r" {2,}", " "),
]

def clean_text(text: str) -> str:
    """去除常見的網頁雜訊（Cookie 聲明、導覽列碎片等）。"""
    cleaned = text
    for pattern, repl in _NOISE_RE:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


# ── FAQ 專用預處理 ──────────────────────────────────────────────────

# 匹配常見問句開頭或問答標籤 (Q:, Question:)
_FAQ_SPLIT_RE = re.compile(
    r"(?:(?<=[.?!])\s*|(?<=\n))\s*(?="          # 在句號後或換行後
    r"(?:Q:|Question:|Do|Does|Is|Are|Can|Will|Should|How|What|Where|When|Why|Who|"
    r"Which|If|Am|Was|Were|Has|Have|Had|Would|Could|May|Must|Shall)\s)",
    re.MULTILINE | re.IGNORECASE,
)


def _split_faq_pairs(text: str) -> list[str]:
    """
    嘗試將 FAQ 頁面拆成獨立的 Q&A pair。

    策略：
      1. 先用問句開頭的 regex 分割，找出候選邊界
      2. 合併過短的碎片（< 80 字元）到前一個 pair
      3. 若單個 pair 超過 FAQ chunk_size 上限，再用通用 splitter 二次切分
    """
    faq_chunk_size = _PAGE_TYPE_SIZES["faq"]
    raw_pairs = _FAQ_SPLIT_RE.split(text)

    pairs: list[str] = []
    buffer = ""
    for part in raw_pairs:
        part = part.strip()
        if not part:
            continue
        # 合併太短的碎片（可能是被誤切的連接短句）
        if len(part) < 80:
            buffer = (buffer + " " + part).strip()
        else:
            if buffer:
                pairs.append(buffer)
            buffer = part
    if buffer:
        pairs.append(buffer)

    # 若單個 pair 仍太長，二次切分
    fallback_splitter = _make_splitter("faq", secondary=True)
    result: list[str] = []
    for pair in pairs:
        if len(pair) > faq_chunk_size:
            result.extend(fallback_splitter.split_text(pair))
        else:
            result.append(pair)

    return result


# ── 核心工具函式 ────────────────────────────────────────────────────

def infer_page_type(url: str) -> str:
    """
    從 URL 路徑推斷頁面類型。

    回傳 page_type 字串，例如 'faq', 'admissions', 'professor_profile', 'general'。
    """
    url_lower = url.lower()
    # Google Scholar 教授相關頁面
    if "scholar.google.com" in url_lower:
        if "view_op=view_citation" in url_lower:
            return "professor_paper"    # 單篇論文 citation 頁面
        return "professor_profile"      # 教授 profile 頁面
    if "faq" in url_lower or "frequently-asked" in url_lower:
        return "faq"
    if "checklist" in url_lower or "requirements" in url_lower:
        return "checklist"
    if "admissions" in url_lower or "graduate-admissions" in url_lower:
        return "admissions"
    if "apply" in url_lower:
        return "apply"
    if "accepting" in url_lower or "acceptance" in url_lower:
        return "accepting"
    return "general"


def _make_splitter(page_type: str, secondary: bool = False) -> RecursiveCharacterTextSplitter:
    """
    根據 page_type 建立對應的 splitter。

    secondary=True 時用於 FAQ 二次切分，採用固定大小而不遞迴推斷。
    """
    size = _PAGE_TYPE_SIZES.get(page_type, _DEFAULT_CHUNK_SIZE)
    overlap = max(_MIN_OVERLAP, int(size * 0.20))
    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=_SEPARATORS,
    )


def chunk_text(
    text: str, 
    page_type: str = "general",
    url: str = "",
    school_name: str = ""
) -> list[str]:
    """
    將長文字依 page_type 切成多個 chunk，並注入背景資訊。

    Args:
        text:        原始純文字
        page_type:   頁面類型
        url:         來源 URL（用於 context 注入，選填）
        school_name: 學校名稱（用於 context 注入，選填）

    Returns:
        切分後的字串列表，過濾掉過短的內容。
    """
    if not text or not text.strip():
        return []

    # 1. 雜訊清洗
    text = clean_text(text)
    if len(text) < 60:
        return []

    # 2. 執行切分
    if page_type == "faq":
        chunks = _split_faq_pairs(text)
    else:
        splitter = _make_splitter(page_type)
        chunks = splitter.split_text(text)

    # 3. 注入上下文前綴 & 過濾
    final_chunks = []
    prefix = ""
    if school_name or page_type != "general":
        label = f"{school_name} | {page_type.replace('_', ' ').title()}".strip(" |")
        prefix = f"[{label}]\n"

    for c in chunks:
        c = c.strip()
        if len(c) < 60:
            continue
        
        # 避免重複添加前綴
        chunk_content = c if c.startswith("[") else f"{prefix}{c}"
        final_chunks.append(chunk_content)

    return final_chunks
