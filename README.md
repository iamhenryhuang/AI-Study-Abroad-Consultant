# Study Abroad RAG：北美 CS 留學顧問

以 FastAPI、LangGraph 與 PostgreSQL 建構的留學申請問答系統。目前正式收錄 Georgia Tech、Purdue、Stanford 三校，支援官方申請條件、社群錄取經驗、選校推薦與教授查詢。

## 系統架構

```mermaid
flowchart LR
    User[使用者] --> API[FastAPI / SSE]
    API --> Agent[LangGraph Agent]
    Crawler[data_crawler] --> DB[(PostgreSQL + pgvector)]
    Agent --> DB
    Agent --> LLM[OpenAI]
    Agent --> Search[SerpAPI]
```

- `data_crawler`：爬取官網、抽取申請欄位、切分全文並寫入 DB。
- PostgreSQL：保存結構化申請條件、官方頁面、RAG chunks、申請經驗與教授名單。
- Agent：依問題查結構化資料、全文、社群案例、推薦結果或教授資訊。
- OpenAI 不可用時會降級為本機規則、全文檢索與資料庫摘錄，不中斷 API。

## Agent 內部流程（LangGraph StateGraph）

```mermaid
flowchart TD
    Q[使用者問題] --> D[辨識意圖與學校]
    D --> R[SQL / 教授 / 經驗 / 推薦檢索]
    R --> V{資料足夠？}
    V -->|否| F[document_chunks 全文檢索]
    F --> V
    V -->|是| A[生成或摘錄答案]
    A --> C[品質檢查]
    C --> E[回傳 SSE 答案]
```

檢索重點：結構化申請條件優先查 `programs` 家族；缺少欄位時查 `document_chunks`；錄取經驗查 `applicant_reports`；教授名單查 `professors`，指名教授可使用 SerpAPI。

## 快速開始

### 1. 啟動 PostgreSQL

```bash
docker compose up -d db
```

預設對外 port 是 `5434`。

### 2. 建立 Python 環境

```bash
python -m venv .venv
source .venv/Scripts/activate       # Git Bash
# .\.venv\Scripts\Activate.ps1    # PowerShell
pip install -r requirements.txt
```

後續請確認終端顯示 `(.venv)`；未啟用環境可能出現缺少 `langgraph.checkpoint.postgres` 等套件錯誤。

### 3. 設定 `backend/.env`

```env
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5434/studyabroad
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1
OPENAI_ANSWER_MODEL=gpt-4o
SERPAPI_KEY=your_serpapi_key
```

### 4. 初始化並寫入資料

順序不可調換：`init-full` 會重建 crawler 資料表，只能在爬蟲之前執行。

```bash
# 1. 建立空 schema，載入 GradCafe / 一畝三分地資料
python backend/scripts/run.py init-full

# 2. 正式爬取三校；未加 --dry-run 時會自動寫入 DB
python -m data_crawler.main --school-id gatech   --max-depth 1 --max-pages 10
python -m data_crawler.main --school-id purdue   --max-depth 1 --max-pages 10
python -m data_crawler.main --school-id stanford --max-depth 1 --max-pages 10

# 3. universities 建立後才能載入教授
python backend/scripts/run.py load-professors

# 4. 檢查 programs、pages、chunks 與 review queue
python backend/scripts/run.py verify-db
```

> 三校爬完後不要再執行 `init-full`、`init-schema` 或 `load-schools`，否則 `universities`、`programs`、`web_pages`、`document_chunks` 與 `professors` 會被清除。

`--max-pages` 是上限，不保證剛好保留指定頁數。Crawler 同時會產生 `data_crawler/output/` 稽核檔，但正式資料以 DB 為準；該目錄已由 Git 忽略。

### 5. 選配：補向量

不產生 embedding 仍可使用 PostgreSQL 全文檢索。需要向量混合檢索時再執行：

```bash
python -m data_crawler.backfill_embeddings --batch-size 16
```

## 啟動與測試

啟動 API：

```bash
uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
```

主要端點：

| 方法 | 路徑 | 用途 |
|---|---|---|
| `POST` | `/api/chat` | SSE Agent 問答 |
| `GET` | `/api/health` | 健康檢查 |
| `POST` | `/api/experiences` | 新增申請經驗 |
| `GET` | `/api/experiences` | 查詢申請經驗 |

CLI 測試：

```bash
python backend/scripts/run.py agent "Gatech MSCS 的 TOEFL 和 IELTS 最低要求是多少？"
python backend/scripts/run.py agent "Purdue CS 碩士需要提交 GRE 嗎？"
python backend/scripts/run.py agent "Stanford CS MS 申請費是多少？"
python backend/scripts/run.py agent "比較 Gatech、Purdue 和 Stanford 的 TOEFL 與 IELTS 要求。"
python backend/scripts/run.py agent "Purdue CS 錄取者通常有什麼 GPA、TOEFL 和 GRE 背景？"
python backend/scripts/run.py agent "Stanford CS MS 被拒絕的申請者通常是什麼背景？"
python backend/scripts/run.py agent "我的 GPA 3.8、TOEFL 105、GRE 325，請推薦衝刺、主申和保底學校。"
python backend/scripts/run.py agent "Stanford 有哪些做 AI 或機器學習教授？"
python backend/scripts/run.py agent "Stanford 的 Fei-Fei Li 研究方向是什麼？"
python backend/scripts/run.py agent "Stanford 有哪些使用者分享經驗？"
```

完整測試：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## 資料來源

| 資料 | 來源 | DB |
|---|---|---|
| 三校官方申請要求 | `data_crawler` | `programs` 家族、`web_pages`、`document_chunks` |
| GradCafe／一畝三分地 | 清洗後 JSON | `applicant_reports` |
| 使用者分享 | API／前端表單 | `user_experiences` |
| 三校教授名單 | `db/data/professors.json` | `professors` |
| 指名教授研究資訊 | SerpAPI | 即時查詢 |

## 專案結構

```text
backend/       FastAPI、LangGraph Agent、檢索與答案生成
data_crawler/  學校官網 crawler 與 DB writer
crawler/       root URL、舊爬蟲與 GradCafe 工具
db/            schema、migration、社群與教授載入腳本
frontend/      React/Vite 前端
tests/         後端與 crawler 測試
```

詳細文件：

- [後端](backend/README.md)
- [資料庫](db/README.md)
- [爬蟲](data_crawler/README.md)
