"""實測 SerpAPI google_scholar_profiles engine（按機構查學者）是否可用。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend" / "scripts"))

from professor_fetcher.fetcher import _get

def main():
    print("=== 測試 google_scholar_profiles: mauthors='Carnegie Mellon University' ===")
    try:
        data = _get({
            "engine": "google_scholar_profiles",
            "mauthors": "Carnegie Mellon University",
            "hl": "en",
        })
    except Exception as e:
        print(f"呼叫失敗：{type(e).__name__}: {e}")
        return

    profiles = data.get("profiles", [])
    print(f"回傳 profiles 數：{len(profiles)}")
    for p in profiles[:10]:
        name = p.get("name", "")
        aid = p.get("author_id", "")
        affil = p.get("affiliations", "")
        interests = ", ".join(i.get("title", "") for i in p.get("interests", [])[:4])
        print(f"  - {name} | id={aid} | {affil} | 領域: {interests}")

    if not profiles:
        print("（無 profiles 欄位，印出回傳的頂層 keys 供判斷）")
        print("keys:", list(data.keys()))
        if "error" in data:
            print("error:", data["error"])

if __name__ == "__main__":
    main()
