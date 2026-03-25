# 將 SerpAPI Google Scholar 資料格式化為 school_data/*.json 相容格式

from __future__ import annotations


def _clean(text: str) -> str:
    """整合空白字元並移除前後空格。"""
    if not text:
        return ""
    return " ".join(text.split())


def format_professor_to_json(
    profile_data: dict,
    recent_papers: list[dict],
    school_id: str = "",
    school_name: str = "",
    professor_name: str = "",
    author_id: str = "",
) -> list[dict]:
    """
    將教授 Profile 與論文列表轉換為 school_data 相容的 record 列表。

    每個 record 格式：
        {school_id, url, passed_types: [{type, score}], data}

    passed_types 中的 type 值：
        - professor_profile：教授主頁（Google Scholar citations 頁）
        - professor_paper  ：個別論文詳細頁
"""
    records: list[dict] = []

    author_data = (profile_data or {}).get("author", {})
    search_params = (profile_data or {}).get("search_parameters", {})
    resolved_author_id = search_params.get("author_id", "") or author_id

    if not resolved_author_id:
        return records

    name         = author_data.get("name", "") or professor_name or "Unknown Professor"
    affiliations = author_data.get("affiliations", "") or school_name
    email        = author_data.get("email", "")
    interests    = author_data.get("interests", [])
    interests_str = ", ".join(
        i.get("title", "") for i in interests if isinstance(i, dict) and i.get("title")
    )

    # ── 1. 教授主頁 ─────────────────────────────────────────────────────────────

    profile_url = f"https://scholar.google.com/citations?user={resolved_author_id}&hl=en"

    lines = [
        f"Professor: {name}",
        f"Affiliation: {affiliations}",
        f"School Context: {school_name}" if school_name != affiliations else "",
        f"Email: {email}" if email else "",
        f"Interests: {interests_str}" if interests_str else "",
    ]

    if recent_papers:
        years  = [p["year"] for p in recent_papers if p.get("year")]
        cutoff = min(years) if years else 0
        lines.append(f"\nRecent Publications (Since {cutoff}):")
        for paper in recent_papers:
            lines.append(
                f"- [{paper['year']}] {paper['title']} "
                f"(Cited: {paper['cited_by_value']}, Pub: {paper['publication']})"
            )

    records.append({
        "school_id":    school_id,
        "url":          profile_url,
        "passed_types": [{"type": "professor_profile", "score": 100}],
        "data":         _clean("\n".join(filter(None, lines))),
    })

    # ── 2. 個別論文詳細頁 ─────────────────────────────────────────────────────

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
        records.append({
            "school_id":    school_id,
            "url":          paper_url,
            "passed_types": [{"type": "professor_paper", "score": 100}],
            "data":         _clean("\n".join(filter(None, p_lines))),
        })

    return records
