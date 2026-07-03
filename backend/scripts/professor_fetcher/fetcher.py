"""
搜尋策略：
  1. search_professor_id()  — 用 google_scholar engine 搜尋 "{name}" "{school}"，
                              從論文結果的 publication_info.authors[].link 中取出 author_id
  2. fetch_recent_papers()  — 搜尋該教授的近兩年論文，過濾年份
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

SERPAPI_KEY: str = os.environ.get("SERPAPI_KEY", "").strip()
SERPAPI_BASE = "https://serpapi.com/search"

_THIS_YEAR = datetime.now().year
RECENT_YEARS_CUTOFF = _THIS_YEAR - 2   # e.g. 2026 → 取 2024、2025、2026

_PLACEHOLDER_KEYS = {"your_serpapi_key_here", "your_key_here", "", "YOUR_KEY"}


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _validate_key() -> None:
    """驗證 SERPAPI_KEY 是否有效。"""
    if not SERPAPI_KEY or SERPAPI_KEY in _PLACEHOLDER_KEYS or len(SERPAPI_KEY) < 20:
        raise RuntimeError(
            "\nSERPAPI_KEY 未設定或仍是預設值！\n\n"
            "請按照以下步驟設定：\n"
            "  1. 前往 https://serpapi.com 取得 API Key\n"
            "  2. 在 .env 中設定：SERPAPI_KEY=你的實際key\n\n"
            "或使用 --author-id 直接跳過搜尋（從 Google Scholar 個人頁 URL 取得）\n"
        )


def _get(params: dict) -> dict:
    """
    呼叫 SerpAPI 並處理重試與報錯。
    
    Args:
        params: API 查詢參數
        
    Returns:
        解析後的 JSON 結果
    """
    _validate_key()
    all_params = {**params, "api_key": SERPAPI_KEY}

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # 增加超時時間到 120 秒以應對 SerpAPI 伺服器響應慢的情況
            resp = requests.get(SERPAPI_BASE, params=all_params, timeout=120)

            if resp.status_code == 400:
                try:
                    data = resp.json()
                    err = data.get("error", resp.text[:200])
                except Exception:
                    err = resp.text[:200]
                raise requests.HTTPError(
                    f"SerpAPI 400 Bad Request (engine={params.get('engine')}): {err}",
                    response=resp,
                )

            resp.raise_for_status()
            return resp.json()

        except requests.HTTPError as e:
            # 400, 401, 403 為客戶端錯誤，通常不需要重試
            if e.response is not None and e.response.status_code in (400, 401, 403):
                raise
            if attempt == max_retries - 1:
                raise
            print(f"  [retry {attempt + 1}/{max_retries}] HTTPError: {str(e)[:100]}")
            time.sleep(2 ** attempt)

        except (requests.RequestException, Exception) as e:
            if attempt == max_retries - 1:
                raise
            print(f"  [retry {attempt + 1}/{max_retries}] 網路錯誤: {type(e).__name__}: {str(e)[:100]}")
            time.sleep(min(2 ** (attempt + 1), 10))

    return {}


def _extract_author_id_from_url(url: str) -> str | None:
    """從 Google Scholar URL 提取 user= 參數（author_id）。"""
    if not url:
        return None
    m = re.search(r"[?&]user=([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _extract_year_from_snippet(snippet: str, publication: str) -> int | None:
    """從 snippet 或 publication 字串中找年份（4 位數字）。"""
    for text in (publication, snippet):
        if not text:
            continue
        m = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        if m:
            return int(m.group(1))
    return None


# ── 主要功能：搜尋 author_id ─────────────────────────────────────────────────

def _normalize_name(s: str) -> str:
    """把連字號、句點去掉並轉小寫，方便模糊比對。例：'F.-F. Li' → 'ff li'"""
    return re.sub(r"[-.]", "", s.lower())


def _name_score(search_name: str, candidate_name: str) -> float:
    """
    回傳 0–1 分數，衡量 candidate_name 和 search_name 的相似度。
    同時考慮精確詞匹配與正規化後的模糊匹配。
    """
    s_parts  = search_name.lower().split()          # ["fei-fei", "li"]
    c_parts  = candidate_name.lower().split()       # ["fei-fei", "li"] or ["fei", "li"]
    c_norm   = _normalize_name(candidate_name)      # "feifei li" or "fei li"

    matched = 0.0
    for sp in s_parts:
        if sp in c_parts:
            matched += 1.0                          # 精確詞匹配
        elif _normalize_name(sp) in c_norm:
            matched += 0.8                          # 正規化後匹配（去連字號/句點）
        elif sp[0] in [p[0] for p in c_parts]:
            matched += 0.3                          # 首字母匹配（縮寫情況）

    return matched / len(s_parts) if s_parts else 0.0


def _affiliation_matches(school: str, profile_affil: str) -> bool:
    """檢查 school 關鍵字是否出現在 profile 的 affiliation 字串中。"""
    if not school or not profile_affil:
        return False
    school_lower  = school.lower()
    affil_lower   = profile_affil.lower()
    # 直接包含
    if school_lower in affil_lower:
        return True
    # 取 school 的第一個有意義詞（≥ 4 字元）作為關鍵字
    for word in school_lower.split():
        if len(word) >= 4 and word in affil_lower:
            return True
    return False


def _search_via_profiles(name: str, affiliation: str) -> str | None:
    """
    使用 google_scholar_profiles engine 搜尋教授 profile。
    該 engine 直接回傳姓名 + affiliation，比從論文作者列表推斷更準確。
    """
    query = f"{name} {affiliation}".strip()
    print(f"  [Profiles] 搜尋：{query!r}")

    try:
        data = _get({
            "engine": "google_scholar_profiles",
            "q": query,
            "hl": "en",
        })
    except Exception as e:
        print(f"  [Profiles] 搜尋失敗：{e}")
        return None

    profiles = data.get("profiles", [])
    if not profiles:
        print("  [Profiles] 無結果")
        return None

    best_id    = None
    best_score = 0.0

    for p in profiles:
        pid    = p.get("author_id") or _extract_author_id_from_url(p.get("link", ""))
        if not pid:
            continue

        p_name  = p.get("name", "")
        p_affil = p.get("affiliations", "")

        n_score = _name_score(name, p_name)
        affil_ok = _affiliation_matches(affiliation, p_affil)

        # 必須姓名高度相似（≥ 0.6）且 affiliation 要能對上
        if n_score < 0.6 or not affil_ok:
            print(f"    跳過 {p_name!r} @ {p_affil!r}  (name_score={n_score:.2f}, affil={affil_ok})")
            continue

        combined = n_score + (1.0 if affil_ok else 0.0)
        if combined > best_score:
            best_score = combined
            best_id    = pid
            print(f"    候選：{p_name!r} @ {p_affil!r}  score={combined:.2f}  id={pid}")

    if best_id:
        print(f"  [Profiles] 選定 id={best_id}")
    else:
        print(f"  [Profiles] 找不到符合條件的 profile（name+affiliation 皆需匹配）")
    return best_id


def _search_via_papers(name: str, affiliation: str) -> str | None:
    """
    舊方法（fallback）：搜尋論文結果，從論文作者連結提取 author_id。
    加入更嚴格的名字比對門檻，並在有多個同分候選時印出警告。
    """
    # 不加引號，讓 Google Scholar 做模糊搜尋；_name_score 負責過濾正確作者
    query = f'{name} {affiliation}'.strip() if affiliation else name
    print(f"  [Papers] 搜尋：{query!r}")

    try:
        data = _get({
            "engine": "google_scholar",
            "q": query,
            "hl": "en",
            "num": 10,
        })
    except Exception as e:
        print(f"  [Papers] 搜尋失敗：{e}")
        return None

    organic = data.get("organic_results", [])
    if not organic:
        print("  [Papers] 搜尋無結果")
        return None

    candidates: list[dict] = []
    for result in organic:
        authors = result.get("publication_info", {}).get("authors", [])
        for author in authors:
            a_name = author.get("name", "")
            link   = author.get("link", "") or author.get("serpapi_scholar_link", "")

            aid = _extract_author_id_from_url(link)
            if not aid:
                m = re.search(r"author_id=([A-Za-z0-9_-]+)", link)
                aid = m.group(1) if m else None
            if not aid:
                continue

            score = _name_score(name, a_name)
            # 需要高度相似（≥ 0.6）才納入候選，以涵蓋縮寫名如 "A Ng"、"AY Ng"
            if score < 0.6:
                continue

            if not any(c["id"] == aid for c in candidates):
                candidates.append({"name": a_name, "id": aid, "score": score})

    if not candidates:
        print(f"  [Papers] 無法找到 {name!r} 的 author_id")
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]

    if len(candidates) > 1 and abs(candidates[1]["score"] - best["score"]) < 0.1:
        print(f"  ⚠️  [Papers] 多位候選者分數相近，可能找到錯誤的人：")
        for c in candidates[:3]:
            print(f"      - {c['name']}  id={c['id']}  score={c['score']:.2f}")

    print(f"  [Papers] 選定 id={best['id']} ({best['name']}  score={best['score']:.2f})")
    return best["id"]


def search_professor_id(name: str, affiliation: str = "") -> str | None:
    """
    搜尋教授的 Google Scholar author_id。

    注意：google_scholar_profiles engine 已停用，直接使用論文搜尋。

    Args:
        name:        教授全名
        affiliation: 學校名稱或關鍵字
    """
    return _search_via_papers(name, affiliation)


def _extract_recent_papers(
    data: dict,
    professor_name: str = "",
    cutoff_year: int | None = None,
    max_papers: int = 20,
) -> list[dict]:
    cutoff = cutoff_year if cutoff_year is not None else RECENT_YEARS_CUTOFF

    papers = []
    for article in data.get("articles", []):
        year = None
        year_str = str(article.get("year", ""))
        m = re.search(r"\b(20\d{2}|19\d{2})\b", year_str)
        if m:
            year = int(m.group(1))

        if year and year < cutoff:
            continue

        cited_val = 0
        cited_raw = article.get("cited_by", {})
        if isinstance(cited_raw, dict):
            cited_val = cited_raw.get("value", 0) or 0

        papers.append({
            "title":          article.get("title", ""),
            "link":           article.get("link", ""),
            "authors":        article.get("authors", professor_name),
            "publication":    article.get("publication", ""),
            "year":           year or cutoff,
            "cited_by_value": cited_val,
            "snippet":        article.get("description", ""),
        })
        if len(papers) >= max_papers:
            break

    print(f"  [Papers] collected {len(papers)} papers since {cutoff}")
    return papers


def fetch_author_profile_and_recent_papers(
    author_id: str,
    professor_name: str = "",
    cutoff_year: int | None = None,
    max_papers: int = 20,
) -> tuple[dict, list[dict]]:
    """Fetch author metadata and recent papers with one SerpAPI author request."""
    try:
        data = _get({
            "engine":    "google_scholar_author",
            "author_id": author_id,
            "hl":        "en",
            "sort":      "pubdate",
        })
    except Exception as e:
        print(f"  author fetch failed: {e}")
        return {"search_parameters": {"author_id": author_id}}, []

    papers = _extract_recent_papers(
        data=data,
        professor_name=professor_name,
        cutoff_year=cutoff_year,
        max_papers=max_papers,
    )
    return data, papers


def fetch_recent_papers(
    author_id: str,
    professor_name: str = "",
    cutoff_year: int | None = None,
    max_papers: int = 20,
) -> list[dict]:
    """
    透過 google_scholar_author engine 抓取該教授的近年論文。
    以 author_id 直接存取個人頁，保證只回傳該教授的論文。
    """
    cutoff = cutoff_year if cutoff_year is not None else RECENT_YEARS_CUTOFF

    try:
        data = _get({
            "engine":    "google_scholar_author",
            "author_id": author_id,
            "hl":        "en",
            "sort":      "pubdate",   # 依發表時間排序，優先取近年論文
        })
    except Exception as e:
        print(f"  論文抓取失敗：{e}")
        return []

    papers = []
    for article in data.get("articles", []):
        year = None
        year_str = str(article.get("year", ""))
        m = re.search(r"\b(20\d{2}|19\d{2})\b", year_str)
        if m:
            year = int(m.group(1))

        if year and year < cutoff:
            continue

        cited_val = 0
        cited_raw = article.get("cited_by", {})
        if isinstance(cited_raw, dict):
            cited_val = cited_raw.get("value", 0) or 0

        papers.append({
            "title":          article.get("title", ""),
            "link":           article.get("link", ""),
            "authors":        article.get("authors", professor_name),
            "publication":    article.get("publication", ""),
            "year":           year or cutoff,
            "cited_by_value": cited_val,
            "snippet":        article.get("description", ""),
        })
        if len(papers) >= max_papers:
            break

    print(f"  取得 {cutoff} 年後論文：{len(papers)} 篇")
    return papers
