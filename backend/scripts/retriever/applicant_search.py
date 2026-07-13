"""申請經驗檢索：查 applicant_reports（GradCafe / 一畝三分地的錄取回報）。

這是「經驗類」資料——個別申請者的主觀回報，非官方、有樣本偏誤。與 programs（官方
申請門檻）性質截然不同，只用來回答「錄取者背景長怎樣 / 某分數有沒有機會」這類問題。
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

from db.connection import get_connection

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


def applicant_search(query: str, school_id: str | None = None, limit: int = 16) -> list[dict]:
    """對 applicant_reports 做全文檢索，依相關度回傳前 limit 筆。

    回傳格式比照其他 chunk doc（含 chunk_text / source_url / school_id），
    並帶 type='applicant_experience' 供 generator 辨識與加註警語。
    """
    tsquery_str = _build_or_tsquery(query)
    # 既無有效關鍵字（例如純中文泛問題拆不出詞）又沒鎖定學校，才真的無從查起
    if not tsquery_str and not school_id:
        return []

    conn = get_connection()
    if not conn:
        print("[ApplicantSearch] 無法取得資料庫連線")
        return []

    conn.read_only = True

    def _run(sql: str, params: dict) -> list[dict]:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, r)) for r in cur.fetchall()]

    _COLS = ("source, source_url, school_id, school_raw, program, "
             "degree_level, decision, gpa, gpa_raw, season, notes")

    try:
        rows: list[dict] = []
        # 第一優先：FTS 關鍵字檢索（有 tsquery 時）
        if tsquery_str:
            sql = f"""
                SELECT {_COLS},
                       ts_rank(fts_vector, to_tsquery('simple', %(q)s)) AS rank
                FROM applicant_reports
                WHERE fts_vector @@ to_tsquery('simple', %(q)s)
            """
            params: dict = {"q": tsquery_str, "limit": limit}
            if school_id:
                sql += " AND school_id = %(school_id)s"
                params["school_id"] = school_id
            sql += " ORDER BY rank DESC LIMIT %(limit)s"
            rows = _run(sql, params)

        # 保底：關鍵字查無（或本來就沒關鍵字）但有鎖定學校 → 回傳該校的經驗回報
        # （中文「某校歷史申請經驗」這類泛問題會落在這裡）
        if not rows and school_id:
            rows = _run(
                f"SELECT {_COLS} FROM applicant_reports "
                "WHERE school_id = %(school_id)s ORDER BY id DESC LIMIT %(limit)s",
                {"school_id": school_id, "limit": limit},
            )
    except Exception as e:
        print(f"[ApplicantSearch] 查詢失敗：{e}")
        return []
    finally:
        conn.close()

    docs = []
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
    return docs
