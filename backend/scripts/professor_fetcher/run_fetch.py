# Google Scholar 資料抓取 CLI 入口

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ── Path Setup ────────────────────────────────────────────────────────────────
CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
ROOT_DIR    = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from professor_fetcher.fetcher import (
    search_professor_id,
    fetch_author_profile_and_recent_papers,
)
from professor_fetcher.formatter import format_professor_to_json

SCHOOL_DATA_DIR = ROOT_DIR.parent / "crawler" / "data"

# ── Utils ────────────────────────────────────────────────────────────────────

def infer_school_id(school_name: str) -> str:
    """從學校名字推斷 ID。"""
    name = school_name.lower()
    if "stanford" in name:              return "stanford"
    if "cmu" in name or "carnegie" in name: return "cmu"
    if "mit" in name:                   return "mit"
    if "caltech" in name:               return "caltech"
    if "georgia" in name or "gatech" in name: return "gatech"
    if "ucla" in name:                  return "ucla"
    if "ucsd" in name:                  return "ucsd"
    if "irvine" in name or "uci" in name: return "uci"
    if "amherst" in name or "umass" in name: return "umass"
    if "purdue" in name:                return "purdue"
    if "washington university" in name: return "washu"
    if "toronto" in name:               return "utoronto"
    return name.split()[0].replace(".", "")


# ── Core Flow ────────────────────────────────────────────────────────────────

def fetch_one(
    name: str,
    school: str,
    school_id: str,
    author_id: str = "",
    cutoff_year: int | None = None,
    max_papers: int = 20,
    delay: float = 1.0,
) -> list[dict] | None:
    """執行單人抓取流程，回傳新格式 record 列表。"""
    print(f"\n>>> Processing: {name} @ {school}")

    if not author_id:
        author_id = search_professor_id(name, affiliation=school)
        time.sleep(delay)

    if not author_id:
        print(f"!!! Skipping {name}: Author ID not found.")
        return None

    profile, papers = fetch_author_profile_and_recent_papers(
        author_id=author_id,
        professor_name=name,
        cutoff_year=cutoff_year,
        max_papers=max_papers,
    )
    time.sleep(delay)

    return format_professor_to_json(
        profile_data=profile,
        recent_papers=papers,
        school_id=school_id,
        school_name=school,
        professor_name=name,
        author_id=author_id,
    )


def save_result(records: list[dict], school_id: str) -> Path:
    """將 record 列表合併寫入 school_data/{school_id}_professors.json。"""
    SCHOOL_DATA_DIR.mkdir(exist_ok=True)
    out_path = SCHOOL_DATA_DIR / f"{school_id}_professors.json"

    existing: list[dict] = []
    if out_path.exists():
        try:
            raw = json.loads(out_path.read_text(encoding="utf-8"))
            existing = raw if isinstance(raw, list) else []
        except Exception:
            pass

    # 以 URL 為 key 去重，新資料蓋舊資料
    merged_by_url = {r["url"]: r for r in existing}
    for r in records:
        merged_by_url[r["url"]] = r

    merged = list(merged_by_url.values())
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"--- Saved to {out_path} (Total entries: {len(merged)})")
    return out_path


def run_embed(json_path: Path) -> None:
    """執行入庫 pipeline（school_data/ 格式）。"""
    try:
        from embedder.pipeline import run_pipeline
        print(f"\n>>> Running embedding pipeline for {json_path.name}...")
        run_pipeline(data_dirname="crawler/data")
    except Exception as e:
        print(f"!!! Embedding failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Professor Data Fetcher (Single Professor)")
    parser.add_argument("--name",        required=True, help="Professor full name")
    parser.add_argument("--school",      required=True, help="Affiliated school name")
    parser.add_argument("--author-id",   default="",   help="Known Google Scholar ID")
    parser.add_argument("--school-id",                 help="Explicit school_id (e.g. stanford)")
    parser.add_argument("--cutoff-year", type=int,     help="Min year for papers")
    parser.add_argument("--max-papers",  type=int, default=20)
    parser.add_argument("--delay",       type=float, default=1.0, help="Seconds delay between API steps")
    parser.add_argument("--embed",       action="store_true", help="Run embedding after fetch")

    args = parser.parse_args()

    sid  = args.school_id or infer_school_id(args.school)
    res  = fetch_one(
        args.name, args.school, sid,
        args.author_id, args.cutoff_year, args.max_papers, args.delay,
    )

    if res:
        path = save_result(res, sid)
        if args.embed:
            run_embed(path)
    else:
        print("Done (No results).")


if __name__ == "__main__":
    main()
