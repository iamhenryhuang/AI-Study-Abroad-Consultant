"""
run_fetch.py — 教授 Google Scholar 資料抓取 CLI 入口

功能：
  抓取單一教授的 Google Scholar 資料：研究領域與近兩年論文
  格式化成與 /data/*.json 完全相容的格式：{ "url": "純文字" }
  可選擇直接跑 embedding pipeline 寫入資料庫

使用方式：
  python -m backend.scripts.professor_fetcher.run_fetch --name "Andrew Ng" --school "Stanford"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, Optional

# ── Path Setup ────────────────────────────────────────────────────────────────
CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
ROOT_DIR = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from professor_fetcher.fetcher import (
    search_professor_id,
    fetch_author_profile,
    fetch_recent_papers,
)
from professor_fetcher.formatter import format_professor_to_json

DATA_DIR = ROOT_DIR / "data"

# ── Utils ───────────────────────────────────────────────────────────────────

def infer_school_id(school_name: str) -> str:
    """從學校名字推斷 ID。"""
    name = school_name.lower()
    if "stanford" in name: return "stanford"
    if "cmu" in name or "carnegie" in name: return "cmu"
    if "mit" in name: return "mit"
    if "berkeley" in name: return "berkeley"
    if "caltech" in name: return "caltech"
    if "georgia" in name or "gatech" in name: return "gatech"
    if "illinois" in name or "uiuc" in name: return "uiuc"
    if "cornell" in name: return "cornell"
    if "ucla" in name: return "ucla"
    if "ucsd" in name: return "ucsd"
    if "washington" in name: return "uw"
    if "nccu" in name: return "nccu"
    return name.split()[0].replace(".", "")


def fetch_one(
    name: str,
    school: str,
    author_id: str = "",
    cutoff_year: Optional[int] = None,
    max_papers: int = 20,
    delay: float = 1.0,
) -> Optional[Dict[str, str]]:
    """執行單人抓取流程。"""
    print(f"\n>>> Processing: {name} @ {school}")
    
    # 1. Author ID
    if not author_id:
        author_id = search_professor_id(name, affiliation=school)
        time.sleep(delay)
    
    if not author_id:
        print(f"!!! Skipping {name}: Author ID not found.")
        return None

    # 2. Papers
    papers = fetch_recent_papers(
        author_id=author_id,
        professor_name=name,
        cutoff_year=cutoff_year,
        max_papers=max_papers,
    )
    time.sleep(delay)

    # 3. Format
    profile = fetch_author_profile(author_id)
    return format_professor_to_json(
        profile_data=profile,
        recent_papers=papers,
        school_name=school,
        professor_name=name,
        author_id=author_id,
    )


def save_result(data: Dict[str, str], school_id: str) -> Path:
    """將結果合併寫入 JSON。"""
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"{school_id}_professors.json"
    
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except:
            pass
    
    existing.update(data)
    out_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"--- Saved to {out_path} (Total entries: {len(existing)})")
    return out_path


def run_embed(json_path: Path):
    """執行入庫 pipeline。"""
    try:
        from embedder.pipeline import run_pipeline
        print(f"\n>>> Running embedding pipeline for {json_path.name}...")
        run_pipeline(data_dirname="data")
    except Exception as e:
        print(f"!!! Embedding failed: {e}")

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Professor Data Fetcher (Single Professor)")
    
    parser.add_argument("--name", required=True, help="Professor full name")
    parser.add_argument("--school", required=True, help="Affiliated school name")
    parser.add_argument("--author-id", default="", help="Known Google Scholar ID")
    parser.add_argument("--school-id", help="Explicit school_id (e.g. stanford)")
    
    parser.add_argument("--cutoff-year", type=int, help="Min year for papers")
    parser.add_argument("--max-papers", type=int, default=20)
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds delay between API steps")
    parser.add_argument("--embed", action="store_true", help="Run embedding after fetch")

    args = parser.parse_args()

    sid = args.school_id or infer_school_id(args.school)
    res = fetch_one(args.name, args.school, args.author_id, args.cutoff_year, args.max_papers, args.delay)
    
    if res:
        path = save_result(res, sid)
        if args.embed:
            run_embed(path)
    else:
        print("Done (No results).")

if __name__ == "__main__":
    main()