"""
fetcher.py — SerpAPI Google Scholar 教授資料抓取模組

搜尋策略（已簡化）：
  1. search_professor_id()       — 用 google_scholar engine 搜尋 "{name}" "{school}"，
                                   從論文結果的 publication_info.authors[].link 中取出 author_id
  2. fetch_recent_papers()       — 搜尋該教授的近兩年論文，過濾年份
  3. fetch_author_profile()      — 返回基本的 author_id metadata（節省 API 配額）
  4. fetch_school_cs_professors() — 搜尋學校全體教授（不限定學科）

環境變數：
  SERPAPI_KEY — SerpAPI 的 API Key（必填）
  申請：https://serpapi.com（每月 100 次免費試用額度）
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

# 載入環境變數
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


def _get(params: dict[str, Any]) -> dict[str, Any]:
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


def _extract_author_id_from_url(url: str) -> Optional[str]:
    """從 Google Scholar URL 提取 user= 參數（author_id）。"""
    if not url:
        return None
    m = re.search(r"[?&]user=([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _extract_year_from_snippet(snippet: str, publication: str) -> Optional[int]:
    """從 snippet 或 publication 字串中找年份（4 位數字）。"""
    for text in (publication, snippet):
        if not text:
            continue
        m = re.search(r"\b(20\d{2}|19\d{2})\b", text)
        if m:
            return int(m.group(1))
    return None


# ── 主要功能：搜尋 author_id ─────────────────────────────────────────────────

def search_professor_id(name: str, affiliation: str = "") -> Optional[str]:
    """
    透過 google_scholar engine 搜尋教授論文，從論文的 author 連結提取 author_id。

    Args:
        name:        教授全名
        affiliation: 學校名稱或關鍵字
    """
    query = f'"{name}" "{affiliation}"' if affiliation else f'"{name}"'
    print(f'  搜尋：{query!r}')

    try:
        params = {
            "engine": "google_scholar",
            "q": query,
            "hl": "en",
            "num": 10,
        }
        data = _get(params)
    except Exception as e:
        print(f"搜尋失敗：{e}")
        return None

    organic = data.get("organic_results", [])
    if not organic:
        print("搜尋無結果")
        return None

    name_parts = name.lower().split()
    candidates: List[Dict[str, Any]] = []

    for result in organic:
        authors = result.get("publication_info", {}).get("authors", [])
        for author in authors:
            author_name = author.get("name", "")
            link = author.get("link", "") or author.get("serpapi_scholar_link", "")
            
            author_id = _extract_author_id_from_url(link)
            if not author_id:
                # 嘗試從 serpapi_scholar_link 的 author_id 參數取
                m = re.search(r"author_id=([A-Za-z0-9_-]+)", link)
                author_id = m.group(1) if m else None
            
            if not author_id:
                continue

            # 匹配分數：完全詞匹配
            author_parts = author_name.lower().split()
            matched_parts = sum(1 for np in name_parts if np in author_parts)
            
            # 過濾：必須匹配大部分名稱
            if matched_parts < max(1, len(name_parts) - 1):
                continue
            
            # 建立候選人資訊，避免重複
            if not any(c["id"] == author_id for c in candidates):
                candidates.append({
                    "name": author_name,
                    "id": author_id,
                    "score": matched_parts,
                })

    if not candidates:
        print(f"無法從搜尋結果中找到 {name!r} 的 author_id。")
        return None

    # 按分數排序，最高分在前
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    
    if len(candidates) > 1 and candidates[1]["score"] == best["score"]:
        print(f"  ⚠️ 警告：有多位名稱匹配程度相同的候選人。")
        for c in candidates[:3]:
            print(f"    - {c['name']} (id={c['id']})")

    print(f"選定 author_id={best['id']} ({best['name']})")
    return best["id"]


def fetch_school_cs_professors(school: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    搜尋指定學校的教授列表。
    """
    query = f'site:scholar.google.com/citations?user= "{school}"'
    print(f"  搜尋 {school} 的教授：{query!r}")

    professors = []
    try:
        params = {
            "engine": "google",
            "q": query,
            "hl": "en",
            "num": limit if limit and limit <= 100 else 100,
        }
        data = _get(params)
    except Exception as e:
        print(f"學校教授搜尋失敗：{e}")
        return []

    for result in data.get("organic_results", []):
        title = result.get("title", "")
        name = title.split(" -")[0].strip()
        author_id = _extract_author_id_from_url(result.get("link", ""))
        
        if author_id:
            professors.append({
                "name": name,
                "author_id": author_id,
                "snippet": result.get("snippet", ""),
            })
        
        if limit and len(professors) >= limit:
            break
            
    print(f"  找到 {len(professors)} 位教授。")
    return professors


def fetch_author_profile(author_id: str) -> Dict[str, Any]:
    """
    取得教授 profile 基準資訊。
    由於 SerpAPI 免費方案限制，此處僅建立 metadata 容器。
    """
    return {"search_parameters": {"author_id": author_id}}


def fetch_papers_by_search(
    name: str,
    author_id: str,
    cutoff_year: int,
    max_papers: int = 20,
) -> List[Dict[str, Any]]:
    """
    精確搜尋教授近年論文。
    """
    params = {
        "engine": "google_scholar",
        "q": f'author:"{name}"',
        "hl": "en",
        "num": min(max_papers * 2, 20),
        "as_ylo": cutoff_year,
    }
    if author_id:
        params["as_sauthors"] = author_id

    try:
        data = _get(params)
    except Exception as e:
        print(f"論文搜尋失敗：{e}")
        return []

    papers = []
    for result in data.get("organic_results", []):
        pub_info = result.get("publication_info", {})
        summary = pub_info.get("summary", "")
        year = _extract_year_from_snippet(result.get("snippet", ""), summary)
        
        if year and year < cutoff_year:
            continue

        cited_by = result.get("inline_links", {}).get("cited_by", {})
        cited_val = cited_by.get("total", 0) if isinstance(cited_by, dict) else 0

        papers.append({
            "title":          result.get("title", ""),
            "link":           result.get("link", result.get("snippet_link", "")),
            "authors":        ", ".join(a.get("name", "") for a in pub_info.get("authors", [])) or summary,
            "publication":    summary,
            "year":           year or cutoff_year,
            "cited_by_value": cited_val,
            "snippet":        result.get("snippet", ""),
        })
        if len(papers) >= max_papers:
            break

    return papers


def fetch_recent_papers(
    author_id: str,
    professor_name: str = "",
    cutoff_year: Optional[int] = None,
    max_papers: int = 20,
) -> List[Dict[str, Any]]:
    """
    抓取教授近兩年發表的論文。
    """
    if not professor_name:
        print("錯誤：必須提供教授姓名以進行論文搜尋。")
        return []

    cutoff = cutoff_year if cutoff_year is not None else RECENT_YEARS_CUTOFF
    papers = fetch_papers_by_search(
        name=professor_name,
        author_id=author_id,
        cutoff_year=cutoff,
        max_papers=max_papers,
    )
    print(f"  取得 {cutoff} 年後論文：{len(papers)} 篇")
    return papers
