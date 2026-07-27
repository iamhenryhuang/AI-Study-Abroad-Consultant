"""安全清除 crawler 測試資料。

預覽：
    python -m data_crawler.cleanup --school-id gatech
    python -m data_crawler.cleanup --all

實際執行：
    python -m data_crawler.cleanup --school-id gatech --yes
    python -m data_crawler.cleanup --all --yes
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import db as dbm


ROOT = Path(__file__).resolve().parent
_SCHOOL_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _school_files(school_id: str) -> list[Path]:
    return [
        ROOT / "output" / f"{school_id}_result.json",
        ROOT / "output" / f"{school_id}_url_filter_review.json",
        ROOT / "output" / f"{school_id}_events.jsonl",
        ROOT / "url" / "all_url" / f"{school_id}.json",
        ROOT / "url" / "keep" / f"{school_id}.json",
        ROOT / "url" / "drop" / f"{school_id}.json",
    ]


def _all_generated_files() -> list[Path]:
    patterns = (
        ROOT / "output" / "*_result.json",
        ROOT / "output" / "*_url_filter_review.json",
        ROOT / "output" / "*_events.jsonl",
        ROOT / "url" / "all_url" / "*.json",
        ROOT / "url" / "keep" / "*.json",
        ROOT / "url" / "drop" / "*.json",
    )
    return sorted({path for pattern in patterns for path in pattern.parent.glob(pattern.name)})


def _checkpoint_tables(cur) -> list[str]:
    found = []
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is not None:
            found.append(table)
    return found


def cleanup_school(school_id: str) -> tuple[dict, list[Path]]:
    if not _SCHOOL_ID.fullmatch(school_id):
        raise ValueError("school_id 只能包含英文字母、數字、底線與連字號")
    conn = dbm.get_connection()
    stats = {"universities": 0, "checkpoints": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM universities WHERE school_id = %s", (school_id,))
            stats["universities"] = cur.rowcount
            thread_pattern = rf"^{re.escape(school_id)}(-|$)"
            for table in _checkpoint_tables(cur):
                cur.execute(f"DELETE FROM {table} WHERE thread_id ~ %s", (thread_pattern,))
                stats["checkpoints"] += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    removed = []
    for path in _school_files(school_id):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return stats, removed


def cleanup_all() -> tuple[dict, list[Path]]:
    conn = dbm.get_connection()
    stats = {"universities": 0, "checkpoint_tables": 0}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM universities")
            stats["universities"] = cur.fetchone()[0]
            cur.execute("TRUNCATE TABLE universities RESTART IDENTITY CASCADE")
            tables = _checkpoint_tables(cur)
            if tables:
                # 表名只可能來自上方固定白名單。
                cur.execute(f"TRUNCATE TABLE {', '.join(tables)}")
            stats["checkpoint_tables"] = len(tables)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    removed = []
    for path in _all_generated_files():
        path.unlink()
        removed.append(path)
    return stats, removed


def main() -> int:
    parser = argparse.ArgumentParser(description="清除 data_crawler DB/checkpoints/結果")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--school-id", help="只清除指定學校")
    target.add_argument("--all", action="store_true", help="清除所有 crawler 資料")
    parser.add_argument("--yes", action="store_true", help="確認永久刪除")
    args = parser.parse_args()

    if args.school_id and not _SCHOOL_ID.fullmatch(args.school_id):
        parser.error("school_id 只能包含英文字母、數字、底線與連字號")
    files = _all_generated_files() if args.all else _school_files(args.school_id)
    existing = [path for path in files if path.is_file()]
    label = "全部學校" if args.all else args.school_id
    if not args.yes:
        print(f"[PREVIEW] 目標：{label}")
        print("將清除 crawler DB 資料、LangGraph checkpoints，以及以下結果檔：")
        for path in existing:
            print(f"  - {path.relative_to(ROOT.parent)}")
        print("\n尚未刪除任何資料；確認後在同一指令加上 --yes。")
        return 0

    try:
        stats, removed = cleanup_all() if args.all else cleanup_school(args.school_id)
    except Exception as exc:
        print(f"清除失敗；請依錯誤確認是否有部分結果檔尚未刪除：{exc}")
        return 1
    print(f"已清除：{label}")
    print(f"DB：{stats}")
    print(f"結果檔：{len(removed)} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
