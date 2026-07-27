"""CLI 入口。

範例（建議先小範圍跑通再擴大）：
    python -m data_crawler.main --school-id purdue --max-depth 1 --max-pages 10 --dry-run
    python -m data_crawler.main --school-id ucla --max-depth 2
    python -m data_crawler.main --school-id ucla --resume            # 從 checkpoint 續跑
    python -m data_crawler.main --school-id ucla --enable-embedding  # 開 embedding

checkpointer：
    有 DATABASE_URL 且非 --dry-run → PostgresSaver（thread_id = school_id）
    --dry-run 或無 DATABASE_URL   → MemorySaver（無法跨行程續跑）
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / "backend" / ".env")

RECURSION_LIMIT = 300


def parse_args():
    ap = argparse.ArgumentParser(description="AI Study Abroad Consultant — LangGraph 資料擷取 pipeline")
    ap.add_argument("--school-id", required=True, help="setting/root_url.py 裡的 school_id（單一學校）")
    ap.add_argument("--root-url", action="append", default=None,
                    help="只爬指定 root，覆寫 root_url.py；可重複傳入多次")
    ap.add_argument("--max-depth", type=int, default=None, help="BFS 深度上限（預設沿用 CONFIG.MAX_DEPTH）")
    ap.add_argument("--max-pages", type=int, default=None, help="總頁數上限（預設沿用 CONFIG.MAX_PAGES）")
    ap.add_argument("--max-sufficiency-iterations", type=int, default=0,
                    help="已停用自動補爬；保留參數僅供舊指令相容")
    ap.add_argument("--skip-recent-hours", type=int, default=0,
                    help=">0 時啟用 SKIP_RECENT：DB 內 last_extracted_at 在 N 小時內就整校跳過")
    ap.add_argument("--enable-embedding", action="store_true", help="開啟 Node 12 embedding（預設關）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不寫 DB；結果輸出到 data_crawler/output/{school_id}_result.json")
    ap.add_argument("--resume", action="store_true", help="從上次 checkpoint 續跑（僅 PostgresSaver 有效）")
    ap.add_argument("--thread-id", default=None, help="覆寫 thread_id（預設 = school_id）")
    return ap.parse_args()


def main():
    args = parse_args()

    # 延後 import：讓 --help 不用等 playwright/langgraph 載入
    from .graph import build_school_graph

    initial_state = {
        "school_id": args.school_id,
        "root_urls_override": args.root_url,
        "max_depth": args.max_depth,
        "max_pages": args.max_pages,
        "max_sufficiency_iterations": args.max_sufficiency_iterations,
        "skip_recent_hours": args.skip_recent_hours,
        "enable_embedding": args.enable_embedding
                            or os.getenv("ENABLE_EMBEDDING", "").lower() in ("1", "true", "on"),
        "dry_run": args.dry_run,
    }

    thread_id = args.thread_id or args.school_id
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    graph_input = None if args.resume else initial_state

    database_url = os.getenv("DATABASE_URL")
    use_postgres = bool(database_url) and not args.dry_run

    if use_postgres:
        from datetime import datetime
        from langgraph.checkpoint.postgres import PostgresSaver
        with PostgresSaver.from_conn_string(database_url) as checkpointer:
            checkpointer.setup()
            app = build_school_graph(checkpointer)
            # 非 resume 的全新執行：若同 thread 已有舊紀錄，另開新 thread，
            # 避免累加型 state（page_results 等）跨執行重複累積
            if not args.resume and checkpointer.get(config) is not None:
                thread_id = f"{args.school_id}-{datetime.now():%Y%m%d%H%M%S}"
                print(f"ℹ️ thread「{args.school_id}」已有舊執行紀錄，本次改用 thread_id={thread_id}"
                      f"（續跑舊紀錄請加 --resume）")
                config = {"configurable": {"thread_id": thread_id},
                          "recursion_limit": RECURSION_LIMIT}
            final_state = app.invoke(graph_input, config)
    else:
        if args.resume:
            print("[WARN] --resume 需要 PostgresSaver（DATABASE_URL 且非 --dry-run），改為重新執行")
        from langgraph.checkpoint.memory import MemorySaver
        app = build_school_graph(MemorySaver())
        final_state = app.invoke(initial_state, config)

    summary = final_state.get("summary", {})
    print(f"\n[OK] 完成：{summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
