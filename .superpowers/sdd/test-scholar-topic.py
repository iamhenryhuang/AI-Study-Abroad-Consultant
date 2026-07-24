"""實測論文聚合路線：google_scholar 論文搜尋 → 聚合作者排名。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend" / "scripts"))

from professor_fetcher.fetcher import _get, _extract_author_id_from_url

def main():
    query = "machine learning Carnegie Mellon University"
    print(f"=== google_scholar 論文搜尋：{query!r} ===")
    try:
        data = _get({"engine": "google_scholar", "q": query, "hl": "en", "num": 20})
    except Exception as e:
        print(f"呼叫失敗：{type(e).__name__}: {e}")
        return

    organic = data.get("organic_results", [])
    print(f"回傳論文數：{len(organic)}")

    authors: dict[str, dict] = {}
    for r in organic:
        cited = (r.get("inline_links", {}).get("cited_by", {}).get("total", 0)) or 0
        for a in r.get("publication_info", {}).get("authors", []):
            link = a.get("link", "") or a.get("serpapi_scholar_link", "")
            aid = _extract_author_id_from_url(link)
            if not aid:
                continue
            slot = authors.setdefault(aid, {"name": a.get("name", ""), "id": aid, "count": 0, "cited": 0})
            slot["count"] += 1
            slot["cited"] += cited

    ranked = sorted(authors.values(), key=lambda x: (x["count"], x["cited"]), reverse=True)
    print(f"聚合出帶 author_id 的學者數：{len(ranked)}")
    for a in ranked[:10]:
        print(f"  - {a['name']} | id={a['id']} | 論文出現 {a['count']} 次 | 累計引用 {a['cited']}")

if __name__ == "__main__":
    main()
