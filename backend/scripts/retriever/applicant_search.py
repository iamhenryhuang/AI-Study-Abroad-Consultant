"""申請經驗檢索：整合 applicant_reports 與 user_experiences。

前者是 GradCafe / 一畝三分地，後者是本站前端使用者分享。兩者都是「經驗類」資料——
非官方、有樣本偏誤，與 programs（官方申請門檻）性質截然不同。
回傳的 doc 一律標記 type='applicant_experience'，讓 generator 加上非官方警語與護欄。

沿用 document_chunks 全文檢索的作法：'simple' config + OR tsquery 廣撒網。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from retriever.db_query import fetch_dicts, readonly_connection

# 拉丁字母/數字組成的詞（英文關鍵字），中文等 CJK 字元不含在內
_WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9]+", re.UNICODE)
# CJK 統一表意文字（中文字）——simple config 無分詞，逐字當 token 才可能命中
_CJK_PATTERN = re.compile(r"[一-鿿]")
# 中文常用停用詞：逐字命中會污染結果（幾乎每篇貼文都有），過濾掉
_CJK_STOPWORDS = set("的是了在和與及或有我你他她它們這那之為以及對於個是如何嗎呢啊吧了過會要能就都很")
# 英文停用詞：simple config 不會過濾英文 function words，OR tsquery 若含
# the/are/for 這類詞，ts_rank 會偏向「notes 冗長」的列，把 notes 為空但
# program 精準命中的案例擠出前幾名（Decomposer 產生的英文子問題整句都是這類詞）。
_EN_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "who", "whom", "whose", "how", "when", "where", "why",
    "do", "does", "did", "done", "can", "could", "may", "might", "must",
    "shall", "should", "will", "would", "has", "have", "had",
    "for", "of", "in", "on", "at", "to", "from", "by", "with", "about",
    "as", "and", "or", "not", "no", "nor", "but", "if", "so", "than", "then",
    "this", "that", "these", "those", "it", "its", "they", "them", "their",
    "there", "here", "i", "you", "we", "he", "she", "my", "your", "our", "me", "us",
    "also", "just", "any", "some", "such", "too", "very", "please",
}

_USER_SCHOOL_PATTERNS = {
    "gatech": ("%gatech%", "%georgia tech%", "%georgia institute of technology%"),
    "purdue": ("%purdue%", "%purdue university%"),
    "stanford": ("%stanford%", "%stanford university%"),
}
_USER_SOURCE_TERMS = (
    "使用者分享", "本站分享", "前端分享", "user submission", "user-submitted",
)
_EXTERNAL_SOURCE_TERMS = ("gradcafe", "一畝三分地", "一亩三分地", "1point3")


def user_submissions_only(query: str) -> bool:
    low = (query or "").lower()
    return any(term in low for term in _USER_SOURCE_TERMS) \
        and not any(term in low for term in _EXTERNAL_SOURCE_TERMS)


def external_reports_only(query: str) -> bool:
    low = (query or "").lower()
    return any(term in low for term in _EXTERNAL_SOURCE_TERMS) \
        and not any(term in low for term in _USER_SOURCE_TERMS)


def _build_or_tsquery(text: str) -> str | None:
    """把自然語句拆成 token 後用 OR 組成 tsquery（交給 to_tsquery('simple', ...) 解析）。

    英文取長度 > 1 的整詞；中文因 simple config 不分詞，逐字拆成單字 token
    （去掉常用停用詞），讓「申請經驗」這類中文問題也能命中 notes 中的相同字。
    """
    low = text.lower()
    tokens = [w for w in _WORD_PATTERN.findall(low)
              if len(w) > 1 and w not in _EN_STOPWORDS]
    tokens += [c for c in _CJK_PATTERN.findall(low) if c not in _CJK_STOPWORDS]
    if not tokens:
        return None
    return " | ".join(tokens)


def _format_report_text(row: dict) -> str:
    """把一列 applicant_reports 組成 LLM 易讀的一段文字。"""
    parts = []
    school = row.get("school_raw") or (row.get("school_id") or "").upper()
    if school:
        parts.append(f"學校：{school}")
    if row.get("program"):
        parts.append(f"科系：{row['program']}")
    if row.get("degree_level"):
        parts.append(f"學位：{row['degree_level']}")
    if row.get("decision"):
        parts.append(f"結果：{row['decision']}")
    if row.get("gpa") is not None:
        parts.append(f"GPA：{row['gpa']}")
    elif row.get("gpa_raw"):
        parts.append(f"GPA(原始)：{row['gpa_raw']}")
    if row.get("season"):
        parts.append(f"季度：{row['season']}")
    head = "；".join(parts)
    notes = (row.get("notes") or "").strip()
    return f"{head}\n{notes}".strip() if notes else head


def _format_user_experience_text(row: dict) -> str:
    """把本站使用者分享轉成與外部回報相同的可檢索文件格式。"""
    lines = ["來源：本站使用者分享（非官方）"]
    fields = (
        ("申請學校", row.get("apply_school")),
        ("申請科系", row.get("apply_program")),
        ("原就讀學校", row.get("graduate_school")),
        ("國家", row.get("country")),
        ("GPA", row.get("gpa")),
    )
    for label, value in fields:
        if value not in (None, ""):
            lines.append(f"{label}：{value}")

    rank = row.get("class_rank")
    size = row.get("class_size")
    if rank is not None and size is not None:
        lines.append(f"系排名：{rank}/{size}")

    items = []
    for item in row.get("experience") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("item") or "").strip()
        result = str(item.get("result") or "").strip()
        if name and result:
            items.append(f"{name}：{result}")
        elif name or result:
            items.append(name or result)
    if items:
        lines.append("經歷：" + "；".join(items))

    review = str(row.get("review") or "").strip()
    if review:
        lines.append("心得：" + review)
    return "\n".join(lines)


def _canonical_user_school_id(apply_school: str) -> str:
    low = (apply_school or "").strip().lower()
    for school_id, patterns in _USER_SCHOOL_PATTERNS.items():
        if any(pattern.strip("%") in low for pattern in patterns):
            return school_id
    return ""


def _fetch_user_experiences(conn, school_id: str | None, limit: int) -> list[dict]:
    if limit <= 0:
        return []
    sql = """
        SELECT id, graduate_school, country, apply_school, apply_program, gpa,
               class_rank, class_size, experience, review, created_at
        FROM user_experiences
    """
    params: dict = {"limit": limit}
    if school_id:
        patterns = _USER_SCHOOL_PATTERNS.get(school_id, (f"%{school_id}%",))
        sql += " WHERE lower(apply_school) LIKE ANY(%(school_patterns)s)"
        params["school_patterns"] = list(patterns)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %(limit)s"
    return fetch_dicts(conn, sql, params)


def applicant_search(query: str, school_id: str | None = None, limit: int = 16) -> list[dict]:
    """合併本站分享與外部申請回報，回傳最多 limit 筆。

    回傳格式比照其他 chunk doc（含 chunk_text / source_url / school_id），
    並帶 type='applicant_experience' 供 generator 辨識與加註警語。
    """
    tsquery_str = _build_or_tsquery(query)
    # 既無有效關鍵字（例如純中文泛問題拆不出詞）又沒鎖定學校，才真的無從查起
    if not tsquery_str and not school_id:
        return []

    _COLS = ("source, source_url, school_id, school_raw, program, "
             "degree_level, decision, gpa, gpa_raw, season, notes")

    with readonly_connection() as conn:
        if not conn:
            print("[ApplicantSearch] 無法取得資料庫連線")
            return []

        user_rows: list[dict] = []
        if not external_reports_only(query):
            try:
                # 本站使用者分享優先保留，避免被大量外部案例擠出前 limit 筆。
                user_rows = _fetch_user_experiences(conn, school_id, min(limit, 8))
            except Exception as e:
                print(f"[ApplicantSearch] 本站使用者分享查詢失敗，略過：{e}")

        rows: list[dict] = []
        external_limit = 0 if user_submissions_only(query) else max(limit - len(user_rows), 0)
        try:
            # 第一優先：FTS 關鍵字檢索（有 tsquery 時）
            if tsquery_str and external_limit > 0:
                sql = f"""
                    SELECT {_COLS},
                           ts_rank(fts_vector, to_tsquery('simple', %(q)s)) AS rank
                    FROM applicant_reports
                    WHERE fts_vector @@ to_tsquery('simple', %(q)s)
                """
                params: dict = {"q": tsquery_str, "limit": external_limit}
                if school_id:
                    sql += " AND school_id = %(school_id)s"
                    params["school_id"] = school_id
                sql += " ORDER BY rank DESC LIMIT %(limit)s"
                rows = fetch_dicts(conn, sql, params)

            # 保底：關鍵字查無（或本來就沒關鍵字）但有鎖定學校 → 回傳該校的經驗回報
            # （中文「某校歷史申請經驗」這類泛問題會落在這裡）
            if not rows and school_id and external_limit > 0:
                rows = fetch_dicts(
                    conn,
                    f"SELECT {_COLS} FROM applicant_reports "
                    "WHERE school_id = %(school_id)s ORDER BY id DESC LIMIT %(limit)s",
                    {"school_id": school_id, "limit": external_limit},
                )
        except Exception as e:
            print(f"[ApplicantSearch] 外部申請回報查詢失敗，略過：{e}")

    docs = []
    for row in user_rows:
        text = _format_user_experience_text(row)
        if not text:
            continue
        docs.append({
            "type": "applicant_experience",
            "chunk_text": text,
            "source": "user_submission",
            "source_url": None,
            "school_id": school_id or _canonical_user_school_id(row.get("apply_school") or ""),
            "user_experience_id": row.get("id"),
        })

    for row in rows:
        text = _format_report_text(row)
        if not text:
            continue
        docs.append({
            "type":        "applicant_experience",
            "chunk_text":  text,
            "source":      row.get("source"),
            "source_url":  row.get("source_url"),
            "school_id":   row.get("school_id") or "",
        })
    return docs[:limit]
