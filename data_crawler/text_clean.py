"""文字清洗 — 保留自既有 pipeline 的邏輯。

- deduplicate_text：搬自 crawler/score.py（n-gram 滑動視窗去重，處理 nav bar
  在多個 DOM 來源重複出現的問題）
- clean_noise：沿用 crawler/clean_json_data.py 的 KEYWORDS_TO_REMOVE 與
  remove_keywords（原本是存檔後整批清洗，這裡改為進 chunk / 進 DB 前清洗）
"""
import hashlib

from .settings_bridge import KEYWORDS_TO_REMOVE, remove_keywords


def normalize_spaces(text):
    return " ".join(text.split()) if text else ""


def deduplicate_text(text: str, window: int = 40) -> str:
    if not text:
        return text
    words = text.split()
    if len(words) < window * 2:
        return text

    seen: set = set()
    kept: list = []

    for i, word in enumerate(words):
        end = i + window
        if end <= len(words):
            ngram = tuple(w.lower() for w in words[i:end])
            if ngram in seen:
                continue
            seen.add(ngram)
        kept.append(word)

    return " ".join(kept)


def clean_noise(text: str) -> str:
    """套用 clean_json_data.py 的關鍵字移除清單。"""
    if not text:
        return text
    return remove_keywords(text, KEYWORDS_TO_REMOVE)


def content_hash(text: str) -> str:
    """與 save_result.py 相同的 md5 content-hash（跨頁去重用）。"""
    return hashlib.md5((text or "").encode("utf-8", errors="replace")).hexdigest()
