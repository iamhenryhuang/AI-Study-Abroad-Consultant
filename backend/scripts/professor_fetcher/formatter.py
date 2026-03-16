"""
formatter.py — 將 SerpAPI Google Scholar 資料格式化為 /data/*.json 相容格式

目標格式（與現有 data/stanford.json 等完全一致）：
    {
        "https://scholar.google.com/citations?user={author_id}":
            "純文字：教授個人資訊 + 研究領域 + 近兩年論文摘要",

        "https://scholar.google.com/citations?view_op=view_citation&...":
            "純文字：單篇論文詳細資訊",
    }
"""

from __future__ import annotations

from typing import Any, Dict, List


def _clean(text: str) -> str:
    """整合空白字元並移除前後空格。"""
    if not text:
        return ""
    return " ".join(text.split())


def format_professor_to_json(
    profile_data: Dict[str, Any],
    recent_papers: List[Dict[str, Any]],
    school_name: str = "",
    professor_name: str = "",
    author_id: str = "",
) -> Dict[str, str]:
    """
    將教授 Profile 與論文列表轉換為相容的 URL-Text 映射。
    """
    result: Dict[str, str] = {}

    author_data = (profile_data or {}).get("author", {})
    search_params = (profile_data or {}).get("search_parameters", {})
    resolved_author_id = search_params.get("author_id", "") or author_id

    if not resolved_author_id:
        return result

    name = author_data.get("name", "") or professor_name or "Unknown Professor"
    affiliations = author_data.get("affiliations", "") or school_name
    email = author_data.get("email", "")
    interests = author_data.get("interests", [])
    interest_titles = [i.get("title", "") for i in interests if isinstance(i, dict)]
    interests_str = ", ".join(filter(None, interest_titles))

    # 1. 教授主頁資訊
    profile_url = f"https://scholar.google.com/citations?user={resolved_author_id}&hl=en"
    
    lines = [
        f"Professor: {name}",
        f"Affiliation: {affiliations}",
        f"School Context: {school_name}" if school_name != affiliations else "",
        f"Email: {email}" if email else "",
        f"Interests: {interests_str}" if interests_str else "",
    ]

    if recent_papers:
        years = [p["year"] for p in recent_papers if p.get("year")]
        cutoff = min(years) if years else 0
        lines.append(f"\nRecent Publications (Since {cutoff}):")
        for paper in recent_papers:
            lines.append(
                f"- [{paper['year']}] {paper['title']} "
                f"(Cited: {paper['cited_by_value']}, Pub: {paper['publication']})"
            )

    result[profile_url] = _clean("\n".join(filter(None, lines)))

    # 2. 個別論文詳細資訊
    for paper in recent_papers:
        paper_url = paper.get("link", "")
        if not paper_url:
            continue

        p_lines = [
            f"Paper Title: {paper['title']}",
            f"Author(s): {paper['authors']}",
            f"Professor: {name}",
            f"Institution: {affiliations}",
            f"Year: {paper['year']}",
            f"Publication: {paper['publication']}",
            f"Citations: {paper['cited_by_value']}",
            f"Abstract: {paper.get('snippet', '')}",
        ]
        result[paper_url] = _clean("\n".join(filter(None, p_lines)))

    return result
