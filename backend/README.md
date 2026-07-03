# Study Abroad Consultant — 後端

CS 碩士申請問答服務。使用 FastAPI、PostgreSQL、LangGraph、OpenAI 打造。

## 核心特色
- **LangGraph Agent 流程**：
  ```
  decompose → (sql_search | extension_function) → finalize
  ```
- **Text-to-SQL 檢索**：OpenAI 依白名單 schema（`universities`、`program_requirements`）產生唯讀 `SELECT` 查詢，執行前會檢查（單一語句、只能 `SELECT`、只能查白名單表格）。
- **教授即時查詢**：問題提到教授姓名時，`extension_function_node` 會呼叫 SerpAPI 並把結果併入最終回答。
- **不使用 embedding / 向量檢索**：所有檢索都是對 `program_requirements`（GPA、TOEFL/IELTS、GRE、截止日期）做結構化 SQL 查詢，通常一次就能查到，因此沒有 planner 重試迴圈。

## 技術棧
- API：FastAPI
- Agent 編排：LangGraph
- 模型：OpenAI（預設 `gpt-4o-mini`，可用 `OPENAI_MODEL` 調整）
- 資料庫：PostgreSQL（純結構化 schema，不需要向量擴充套件）

## 環境設定

### 何時需要進入 `.venv`？
要執行 backend 相關 Python 指令時（如安裝套件、跑 CLI、啟動 API server）都要先啟用 `.venv`。

```bash
# Git Bash (Windows)
source .venv/Scripts/activate
```

在 `backend/` 目錄下建立 `.env`：
```env
DATABASE_URL=postgresql://user:password@localhost:5432/db_name
OPENAI_API_KEY=your_openai_api_key    # decomposer / text-to-SQL / 答案生成都用這把 key
OPENAI_MODEL=gpt-4o-mini
SERPAPI_KEY=your_serpapi_key          # 教授查詢功能專用
```

## CLI 指令（從專案根目錄執行）

### 資料庫管理
| 指令 | 說明 |
| :--- | :--- |
| `python backend/scripts/run.py init-all` | 一次完成 setup + 灌入學校資料 |
| `python backend/scripts/run.py setup` | 檢查連線，資料庫不存在則建立 |
| `python backend/scripts/run.py init-schema` | 依 `db/init_db.sql` 重建資料表（會清空重建） |
| `python backend/scripts/run.py load-schools` | 建表 + 灌入 `db/schools_data.json` 的學校資料 |
| `python backend/scripts/run.py verify-db` | 檢查目前資料庫內容 |

### 檢索與問答
| 指令 | 說明 |
| :--- | :--- |
| `python backend/scripts/run.py search "問題"` | 只測試 text-to-SQL：印出產生的 SQL 與查詢結果 |
| `python backend/scripts/run.py rag "問題"` | 單次 SQL 查詢 + LLM 生成答案（不跑 LangGraph 迴圈） |
| `python backend/scripts/run.py agent "問題"` | 執行完整 LangGraph Agent 流程 |

**常用參數：**
- `--max-steps [N]`：Agent 模式最大迭代次數（預設 5）

```bash
python backend/scripts/run.py agent "MIT MSCS deadline?"
```

## Agent 事件串流

後端提供 `POST /api/chat`，以 SSE 方式將事件串流給客戶端。

事件類型：
- `thinking`
- `tool_call`
- `tool_result`
- `llm_call`
- `answer_chunk`
- `answer`
- `error`

對應 [backend/api.py](backend/api.py) 與 [backend/scripts/retriever/agent.py](backend/scripts/retriever/agent.py) 中發送的事件。

### 教授資料抓取

Agent 會在 Decomposer 階段自動偵測教授查詢意圖，並透過 `extension_function_node` 即時呼叫 SerpAPI，不需要手動預先抓取。

若要手動預先抓取某教授的資料：
```bash
python backend/scripts/professor_fetcher/run_fetch.py --name "Andrew Ng" --school "Stanford"
```

使用 `google_scholar_author` API（以 `author_id` 查詢），確保結果只屬於該教授本人。

## 資料庫 Schema

`db/init_db.sql` 定義兩張表：

- `universities(school_id, name, domain)`
- `program_requirements(school_id, min_gpa, toefl_min, ielts_min, gre_required, gre_min_total, gre_min_quant, gre_min_verbal, deadline_fall, deadline_spring, priority_deadline, source_url, ...)`

`db/schools_data.json` 目前收錄 5 間學校（CMU、MIT、Stanford、Caltech、Georgia Tech），由 `db/load_schools.py` 讀取後直接寫入資料庫。之後要接入真實資料時，只需要替換這份 JSON，schema 與 text-to-SQL agent 都不需要改動。
