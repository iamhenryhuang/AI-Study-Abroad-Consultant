# Study Abroad RAG：北美 CS 留學顧問

> 以 LangGraph 打造的結構化資料問答系統，協助北美 CS 碩士申請諮詢。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)

---

## 系統概覽

各校申請要求（GPA、TOEFL/IELTS、GRE、截止日期）以結構化資料存在 PostgreSQL 中；
使用者用自然語言提問，後端 Agent 會自動產生 SQL 查詢資料庫並整理成答案。

**核心流程（LangGraph）**：
```
decompose（拆解問題/偵測學校/偵測教授查詢）
   ├─ sql_search（text-to-SQL 查資料庫）
   └─ extension_function（若問到教授，呼叫 SerpAPI 即時抓資料）
        └─ finalize（OpenAI 彙整成繁中答案，附來源連結）
```

### 重點特色
- **Text-to-SQL 檢索**：OpenAI 依 schema 產生唯讀 SQL，執行前會做白名單表格 / 唯讀 / 單一語句檢查，不會誤刪改資料。
- **教授即時查詢**：問題提到具體教授姓名時，Agent 會呼叫 SerpAPI 即時抓取教授資料。
- **串流回應**：透過 SSE 傳送 `thinking`、`tool_call`、`tool_result`、`llm_call`、`answer_chunk`、`answer`、`error` 事件。
- **來源可追溯**：答案中會附上官網來源連結。
- **不需要 embedding / 向量資料庫**：整套系統已移除 embedding pipeline，資料庫是純 PostgreSQL 結構化表格。

---

## 專案結構

```text
.
├── backend/                 # FastAPI、檢索邏輯、LangGraph Agent
│   ├── api.py                 # API 入口（SSE 串流）
│   └── scripts/
│       ├── db/                  # DB 連線與操作
│       ├── retriever/           # sql_search（text-to-SQL）+ LangGraph agent
│       ├── generator/           # OpenAI 答案生成
│       └── professor_fetcher/   # SerpAPI 教授資料抓取
├── crawler/                 # Playwright 爬蟲（原始網頁蒐集，供人工整理用）
├── db/                      # PostgreSQL schema（init_db.sql）+ 學校資料（load_schools.py）
└── requirements.txt          # 所有 Python 依賴（backend + crawler）
```

---

## 快速開始（本機開發，不用 Docker）

### 1. 前置需求
- Python 3.10+
- PostgreSQL

### 2. 建立虛擬環境並安裝依賴

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash (Windows)
pip install -r requirements.txt
```

### 3. 設定環境變數

建立 `backend/.env`：

```env
DATABASE_URL=postgresql://user:password@localhost:5432/study_abroad_rag
OPENAI_API_KEY=your_openai_api_key   # decomposer / text-to-SQL / 答案生成都用這把 key
OPENAI_MODEL=gpt-4o-mini
SERPAPI_KEY=your_serpapi_key         # 教授查詢功能專用
```

### 4. 初始化資料庫（建表 + 灌入學校資料）

```bash
python backend/scripts/run.py setup          # 若資料庫不存在則建立
python backend/scripts/run.py load-schools   # 建表 + 灌入 db/schools_data.json 的學校資料
```

### 5. 啟動服務

```bash
python -m uvicorn backend.api:app --reload --port 8000
```

---

## Docker 快速開始（推薦）

目前 `docker-compose.yml` 只有兩個 service：`db`（PostgreSQL）與 `backend`（FastAPI + Agent）。**沒有 frontend service**——整個專案已改為 API-only，前端已從 repo 移除。

`backend` 額外掛載 `./crawler/data:/app/crawler/data:ro`，供 `professor_fetcher` 讀寫教授資料快取用。

### 1. 準備環境變數

```bash
cp .env.example backend/.env
```

編輯 `backend/.env`，至少填入：

```env
OPENAI_API_KEY=your_openai_api_key
SERPAPI_KEY=your_serpapi_key
POSTGRES_PASSWORD=postgres
```

### 2. 第一次啟動（會 build image）

```bash
docker compose up --build
```

只 build 不啟動（例如純粹想驗證 Dockerfile 改動有沒有問題）：

```bash
docker compose build backend
```

### 3. 灌入學校資料（第一次啟動後執行一次）

```bash
docker compose exec backend python backend/scripts/run.py load-schools
```

### 4. 服務端點

| 服務 | URL |
|------|-----|
| 後端 API | http://localhost:8000 |
| API 文件（Swagger UI） | http://localhost:8000/docs |

### 5. 呼叫 API

服務只有一個問答端點 `POST /api/chat`，以 SSE（Server-Sent Events）串流回應：

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Compare Stanford and CMU GPA requirements", "max_steps": 5}'
```

會依序收到多個 `data: {...}` 事件，最後一個 `type: "answer"` 是完整答案：

```
data: {"type": "thinking", "step": 1}
data: {"type": "tool_call", "tool": "sql_search", "args": {...}}
data: {"type": "tool_result", "tool": "sql_search", "preview": "..."}
data: {"type": "answer", "text": "### [GPA 要求]\n- Stanford: 3.50 ...\n- CMU: 3.50 ..."}
```

也可以直接打開 http://localhost:8000/docs 用 Swagger UI 互動式測試，或用 Postman／Insomnia 等工具（記得開啟 SSE / streaming response 支援）。

若不透過 HTTP API，也可以直接在容器內用 CLI 問答（不需要另外起 client）：

```bash
docker compose exec backend python backend/scripts/run.py agent "MIT deadline?"
```

### 6. 之後啟動 / 常用指令

```bash
docker compose up                 # 之後啟動（不重新 build）
docker compose down                # 停止所有服務
docker compose down -v             # 停止並清除資料庫資料（連學校資料一起清掉）
docker compose logs -f backend      # 查看後端 log
docker compose exec backend python backend/scripts/run.py agent "Compare Stanford and CMU"
```

---

## 系統怎麼用（CLI 指令一覽）

所有指令從專案根目錄執行，入口為 `backend/scripts/run.py`：

| 指令 | 說明 |
|------|------|
| `init-all` | 一次完成 `setup` + `load-schools` |
| `setup` | 檢查連線，資料庫不存在則建立 |
| `init-schema` | 依 `db/init_db.sql` 重建資料表結構（會清空重建） |
| `load-schools` | 建表 + 灌入 `db/schools_data.json` 的學校資料 |
| `verify-db` | 檢查目前資料庫內容 |
| `search "問題"` | 只測試 text-to-SQL：印出產生的 SQL 與查詢結果 |
| `rag "問題"` | 單次 SQL 查詢 + 生成答案（不跑 LangGraph 迴圈） |
| `agent "問題"` | 完整跑一次 LangGraph Agent 流程（正式問答用這個） |

範例：

```bash
python backend/scripts/run.py agent "Compare Stanford and CMU GPA requirements"
python backend/scripts/run.py search "MIT TOEFL 最低幾分"
```

Docker 環境下同樣指令加上 `docker compose exec backend` 前綴即可：

```bash
docker compose exec backend python backend/scripts/run.py agent "MIT deadline?"
```

---

## 教授資料查詢

**1. 即時查詢（推薦）**：問題中直接提到教授姓名，Agent 會自動偵測意圖並呼叫 SerpAPI 即時抓取，不需要手動操作。

**2. 手動預先抓取**：

```bash
python backend/scripts/professor_fetcher/run_fetch.py \
  --name "Andrew Ng" \
  --school "Stanford"
```

結果會存到 `crawler/data/{school_id}_professors.json`。

---

## 資料庫 Schema

`db/init_db.sql` 定義兩張表：

- `universities`：學校基本資料（school_id、name、domain）
- `program_requirements`：每校一筆申請要求，含 GPA、TOEFL/IELTS/Duolingo、GRE、各截止日期、來源連結

`db/schools_data.json` 目前收錄 30 間北美 CS 研究所（CMU、MIT、Stanford、Berkeley、UIUC 等），由 `db/load_schools.py` 讀取後直接寫入資料庫，之後要接入真實資料時只需替換這份 JSON，schema 與 text-to-SQL Agent 不需要改動。

新增學校時記得同步更新 `backend/scripts/retriever/agent.py` 的 `_SCHOOL_ALIASES` 對照表，Decomposer 才能正確辨識學校縮寫/別名。

---

## 文件連結
- [後端文件](backend/README.md)
