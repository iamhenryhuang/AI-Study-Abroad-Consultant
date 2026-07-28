import sys
from pathlib import Path

# Ensure scripts directory is in path
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from db.operations import (clear_crawler_data, init_experience_schema, init_schema,
                           load_professors, load_schools, setup_db, verify)
from retriever.rag_pipeline import run_rag_pipeline, run_agent_pipeline

# live demo 鎖定的三所學校（root URL 定義於 crawler/setting/root_url.py）
DEMO_SCHOOLS = ("purdue", "gatech", "stanford")


def init_full() -> bool:
    """一鍵建好 DB 骨架與社群資料：連線 → 重建 programs 家族（空表）→
    使用者經驗表 → 社群申請回報（含 migration）。任一步失敗即中止。

    學校/系所資料不在此步驟寫入：改由 data_crawler 實際爬取 purdue / gatech /
    stanford 後寫入（見下方 init-demo 提示），故此處只 init-schema 建空表，
    不再灌 db/data/schools_data.json 的種子學校資料。
    教授名單需掛在既有 universities 上，因此排在爬蟲之後單獨執行 load-professors。"""
    import importlib.util

    # load_applicant_reports.py 位於專案根的 db/（非 backend package），以路徑載入
    _lar_path = SCRIPTS.parent.parent / "db" / "load_applicant_reports.py"
    _spec = importlib.util.spec_from_file_location("load_applicant_reports", _lar_path)
    _lar = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_lar)
    load_applicant_reports = _lar.load

    steps = [
        ("setup",              setup_db),
        ("init-schema",        init_schema),    # 重建 programs 家族（空表，等爬蟲填）
        ("init-experience",    init_experience_schema),
        ("load-applicant",     lambda: load_applicant_reports(apply_migration=True)),
    ]
    for name, fn in steps:
        print(f"\n── init-full：{name} ──")
        if fn() is False:
            print(f"init-full 於 {name} 失敗，已中止。")
            return False
    print("\ninit-full 完成：空 programs 家族 / user_experiences / applicant_reports 已就緒。")
    print("\n接著請爬取學校資料（三校各跑一次，開 embedding）：")
    for sid in DEMO_SCHOOLS:
        print(f"  python -m data_crawler.main --school-id {sid} --enable-embedding")
    print("爬完後再灌教授名單：")
    print("  python backend/scripts/run.py load-professors")
    return True

COMMANDS = {
    "setup":     ("檢查連線並建立資料庫",                       setup_db),
    "init-schema": ("依 db/init_db.sql 建表（重置資料表）",      init_schema),
    "init-experience": ("建立使用者申請經驗表（冪等、不清資料）", init_experience_schema),
    "load-schools": ("建表 + 灌入 db/data/schools_data.json 的種子學校假資料（demo 不用）", load_schools),
    "load-professors": ("灌入 db/data/professors.json 教授名單（需先有 universities）", load_professors),
    "verify-db": ("檢查 SQL 資料是否已寫入",                     verify),
    "clear-crawler-data": ("清除所有爬蟲 DB 資料/checkpoints/結果 JSON（需 --yes）", None),
    "search":    ("執行 text-to-SQL 查詢測試 [query]",           None),  # 特殊處理
    "rag":       ("單次 SQL 檢索 + 生成回答 [query]",            None),  # 特殊處理
    "agent":     ("Agentic RAG LangGraph Loop [query] [--max-steps N]", None),  # 特殊處理
    "init-all":  ("一次完成 setup + load-schools（不含經驗/回報表）", lambda: (setup_db() and load_schools())),
    "init-full": ("一鍵建好 DB 骨架：空 programs 家族 + 經驗表 + 社群回報（學校資料交給爬蟲）", init_full),
}


def main():
    # Windows may default redirected console output to cp950, while crawled
    # university pages legitimately contain symbols such as ≥ and em dashes.
    # Keep CLI output deterministic and avoid crashing after successful retrieval.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("可用指令:")
        for cmd, (desc, _) in COMMANDS.items():
            print(f"  {cmd:<14}  {desc}")
        sys.exit(1)

    cmd = sys.argv[1]
    _, runner = COMMANDS[cmd]

    if cmd == "clear-crawler-data":
        if "--yes" not in sys.argv[2:]:
            print("拒絕執行：此操作會永久清除所有爬蟲資料與結果檔案。")
            print("確認後請執行: python backend/scripts/run.py clear-crawler-data --yes")
            sys.exit(2)
        ok = clear_crawler_data()
    elif cmd in ["search", "rag", "agent"]:
        max_steps = 5
        if "--max-steps" in sys.argv:
            idx = sys.argv.index("--max-steps")
            if idx + 1 < len(sys.argv):
                try:
                    max_steps = int(sys.argv[idx + 1])
                except ValueError:
                    pass

        raw_args = sys.argv[2:]
        args_clean = []
        i = 0
        while i < len(raw_args):
            token = raw_args[i]
            if token == "--max-steps":
                i += 2
                continue
            args_clean.append(token)
            i += 1

        query = " ".join(args_clean).strip()

        if not query:
            query = input("請輸入查詢問題: ").strip()
            if not query:
                print("未輸入問題，停止執行。")
                sys.exit(0)

        if cmd == "search":
            from retriever.sql_search import sql_search
            results, sql = sql_search(query)
            print(f"SQL: {sql}")
            for row in results:
                print(row)
            ok = bool(results)
        elif cmd == "agent":
            ok = run_agent_pipeline(query, max_steps=max_steps)
        else:
            ok = run_rag_pipeline(query)
    else:
        ok = runner()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
