import os
import sys
from pathlib import Path

# Suppress library noise (TensorFlow, Protobuf, etc.)
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

import logging
# 強制設定 tensorflow 的日誌等級為 ERROR，這能擋住 Python 層級的 WARNING
logging.getLogger('tensorflow').setLevel(logging.ERROR)

import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
# 針對特定庫的過期警告進一步壓制
warnings.filterwarnings("ignore", module="tensorflow")
warnings.filterwarnings("ignore", module="tf_keras")

# Ensure scripts directory is in path
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from db.operations import import_json, setup_db, verify
from embedder.pipeline import run_pipeline
from embedder.verifier import verify_embeddings
from retriever.search import run_search
from retriever.rag_pipeline import run_rag_pipeline, run_agent_pipeline

COMMANDS = {
    "setup":     ("檢查連線並建立資料庫",                   setup_db),
    "import":    ("建表 + 切片 + 向量化並匯入 crawler/school_data/*.json", import_json),
    "verify-db": ("檢查 SQL 資料是否已寫入",                 verify),
    "verify-vdb":("檢查向量資料庫狀態",                      verify_embeddings),
    "embed":     ("切片 + 向量化並寫入 document_chunks",     run_pipeline),
    "search":    ("執行向量檢索測試 [query] [--school]",      None),  # 特殊處理
    "rag":       ("執行完整 RAG 流程 [query] [--school]",    None),  # 特殊處理
    "agent":     ("Agentic RAG LangGraph Loop [query] [--max-steps N]", None),  # 特殊處理
    "init-all":  ("一次完成 setup + import",                 lambda: (setup_db() and import_json())),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__.strip())
        print("\n可用指令:")
        for cmd, (desc, _) in COMMANDS.items():
            print(f"  {cmd:<12}  {desc}")
        sys.exit(1)

    cmd = sys.argv[1]
    _, runner = COMMANDS[cmd]

    # search / rag 需要特殊處理（自訂 query 與旗標）
    if cmd in ["search", "rag", "agent"]:
        # 解析旗標
        max_steps = 5
        school_id = None
        if "--max-steps" in sys.argv:
            idx = sys.argv.index("--max-steps")
            if idx + 1 < len(sys.argv):
                try:
                    max_steps = int(sys.argv[idx + 1])
                except ValueError:
                    pass
        if "--school" in sys.argv:
            idx = sys.argv.index("--school")
            if idx + 1 < len(sys.argv):
                school_id = sys.argv[idx + 1]

        # 取出 query（排除旗標與其參數值）
        raw_args = sys.argv[2:]
        args_clean = []
        i = 0
        while i < len(raw_args):
            token = raw_args[i]
            if token in {"--school", "--max-steps"}:
                i += 2
                continue
            args_clean.append(token)
            i += 1

        query = " ".join(args_clean).strip()

        if not query:
            query = input(f"請輸入查詢問題: ").strip()
            if not query:
                print("未輸入問題，停止執行。")
                sys.exit(0)

        if cmd == "search":
            ok = run_search(query, school_id=school_id)
        elif cmd == "agent":
            ok = run_agent_pipeline(query, max_steps=max_steps)
        else:
            ok = run_rag_pipeline(
                query,
                school_id=school_id,
            )
    else:
        ok = runner()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
