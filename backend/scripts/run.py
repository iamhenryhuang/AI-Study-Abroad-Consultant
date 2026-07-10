import sys
from pathlib import Path

# Ensure scripts directory is in path
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from db.operations import init_experience_schema, init_schema, load_schools, setup_db, verify
from retriever.rag_pipeline import run_rag_pipeline, run_agent_pipeline

COMMANDS = {
    "setup":     ("檢查連線並建立資料庫",                       setup_db),
    "init-schema": ("依 db/init_db.sql 建表（重置資料表）",      init_schema),
    "init-experience": ("建立使用者申請經驗表（冪等、不清資料）", init_experience_schema),
    "load-schools": ("建表 + 灌入 db/data/schools_data.json 的學校資料", load_schools),
    "verify-db": ("檢查 SQL 資料是否已寫入",                     verify),
    "search":    ("執行 text-to-SQL 查詢測試 [query]",           None),  # 特殊處理
    "rag":       ("單次 SQL 檢索 + 生成回答 [query]",            None),  # 特殊處理
    "agent":     ("Agentic RAG LangGraph Loop [query] [--max-steps N]", None),  # 特殊處理
    "init-all":  ("一次完成 setup + load-schools",               lambda: (setup_db() and load_schools())),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("可用指令:")
        for cmd, (desc, _) in COMMANDS.items():
            print(f"  {cmd:<14}  {desc}")
        sys.exit(1)

    cmd = sys.argv[1]
    _, runner = COMMANDS[cmd]

    if cmd in ["search", "rag", "agent"]:
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
