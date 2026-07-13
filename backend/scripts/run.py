import sys
from pathlib import Path

# Ensure scripts directory is in path
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from db.operations import (clear_crawler_data, init_experience_schema, init_schema,
                           load_schools, setup_db, verify)
from retriever.rag_pipeline import run_rag_pipeline, run_agent_pipeline


def init_full() -> bool:
    """一鍵從零建好整個 DB：連線 → programs 家族 → 種子學校 →
    使用者經驗表 → 社群申請回報（含 migration）。任一步失敗即中止。"""
    import importlib.util

    # load_applicant_reports.py 位於專案根的 db/（非 backend package），以路徑載入
    _lar_path = SCRIPTS.parent.parent / "db" / "load_applicant_reports.py"
    _spec = importlib.util.spec_from_file_location("load_applicant_reports", _lar_path)
    _lar = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_lar)
    load_applicant_reports = _lar.load

    steps = [
        ("setup",              setup_db),
        ("init-schema",        init_schema),
        ("load-schools",       load_schools),
        ("init-experience",    init_experience_schema),
        ("load-applicant",     lambda: load_applicant_reports(apply_migration=True)),
    ]
    for name, fn in steps:
        print(f"\n── init-full：{name} ──")
        if fn() is False:
            print(f"init-full 於 {name} 失敗，已中止。")
            return False
    print("\ninit-full 完成：programs / user_experiences / applicant_reports 皆已就緒。")
    return True

COMMANDS = {
    "setup":     ("檢查連線並建立資料庫",                       setup_db),
    "init-schema": ("依 db/init_db.sql 建表（重置資料表）",      init_schema),
    "init-experience": ("建立使用者申請經驗表（冪等、不清資料）", init_experience_schema),
    "load-schools": ("建表 + 灌入 db/data/schools_data.json 的學校資料", load_schools),
    "verify-db": ("檢查 SQL 資料是否已寫入",                     verify),
    "clear-crawler-data": ("清除所有爬蟲 DB 資料/checkpoints/結果 JSON（需 --yes）", None),
    "search":    ("執行 text-to-SQL 查詢測試 [query]",           None),  # 特殊處理
    "rag":       ("單次 SQL 檢索 + 生成回答 [query]",            None),  # 特殊處理
    "agent":     ("Agentic RAG LangGraph Loop [query] [--max-steps N]", None),  # 特殊處理
    "init-all":  ("一次完成 setup + load-schools（不含經驗/回報表）", lambda: (setup_db() and load_schools())),
    "init-full": ("一鍵建好整個 DB：programs + 經驗表 + 社群回報",   init_full),
}


def main():
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
