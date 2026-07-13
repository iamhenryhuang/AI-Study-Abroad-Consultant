# Study Abroad RAG：北美 CS 留學顧問

> 以 LangGraph 打造的結構化資料問答系統，協助北美 CS 碩士申請諮詢。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)

---

## 系統概覽

各校申請要求（GPA、TOEFL/IELTS、GRE、截止日期、學費、獎助）由 `data_crawler` 爬蟲抽取後，
以結構化資料存進 PostgreSQL；頁面全文則切段存入 `document_chunks` 供全文檢索。
使用者用自然語言提問，後端 Agent 先用 text-to-SQL 查結構化欄位，查不到再 fallback 到全文檢索，最後整理成答案。

### 系統架構

```mermaid
flowchart LR
    Client(["🧑 使用者<br/>經驗分享前端 / Swagger UI / Client"])

    subgraph Backend["backend container — FastAPI"]
        direction TB
        API["POST /api/chat<br/>SSE 串流端點"]
        Agent["LangGraph Agent<br/>(詳見下方 Agent 流程圖)"]
        API --> Agent
    end

    subgraph External["外部服務"]
        direction TB
        OpenAI[["🤖 OpenAI API<br/>分級模型：判斷型 gpt-4.1<br/>答案生成 gpt-4o"]]
        SerpAPI[["🔍 SerpAPI<br/>Google Scholar 教授資料"]]
    end

    DB[("🗄️ db container<br/>PostgreSQL + pgvector<br/>programs 家族（結構化）<br/>+ document_chunks（內文/全文檢索）<br/>+ applicant_reports（申請經驗回報）")]
    Crawler["🕷️ data_crawler<br/>LangGraph 爬蟲<br/>抽取結構化欄位 + 頁面全文"]

    Crawler -- "抽取後寫入" --> DB
    Client -- "① 自然語言問題" --> API
    Agent -- "② SQL 查詢 / LLM 呼叫" --> OpenAI
    Agent -- "③ 教授查詢" --> SerpAPI
    Agent -- "④ 唯讀 SQL / 全文 / 經驗檢索" --> DB
    Agent -- "⑤ SSE 事件串流<br/>(thinking/tool_call/answer)" --> Client

    style Crawler fill:#f3e8fd,stroke:#a142f4

    style Client fill:#e8f0fe,stroke:#4285f4
    style DB fill:#fef7e0,stroke:#f9ab00
    style OpenAI fill:#e6f4ea,stroke:#34a853
    style SerpAPI fill:#e6f4ea,stroke:#34a853
```

### Agent 內部流程（LangGraph StateGraph）

```mermaid
flowchart TD
    Start(["原始問題"]) --> D["🔍 decompose<br/>意圖判斷 + 子問題拆解"]

    D -->|needs_sql_search| S["📊 search<br/>text-to-SQL"]
    D -->|professor_query| E["🎓 extension_function<br/>SerpAPI 抓教授"]
    D -->|needs_experience| X["🗣️ experience_search<br/>applicant_reports 錄取經驗"]

    S --> V
    E --> V
    X --> V

    V{"🛡️ verify<br/>資料足夠？"}

    V -->|足夠| F["✍️ finalize<br/>生成答案 / 誠實告知"]
    V -->|"經驗題 · 有撈到案例"| F
    V -->|"不足 · 尚未全文檢索"| FT["🔎 fulltext<br/>document_chunks 全文檢索"]
    V -->|"仍不足 · 尚未重試"| R["🔁 refine<br/>改寫查詢"]
    V -->|"查無資料 / 已重試過"| F

    FT -.補資料後重判.-> V
    R -.重跑一輪.-> S

    F -->|生成過答案| C["🧐 critic<br/>幻覺複查"]
    F -->|誠實告知| End(["最終答案"])
    C --> End

    classDef nodeStyle fill:#f8f9fa,stroke:#5f6368,stroke-width:1.5px
    classDef decision fill:#fef7e0,stroke:#f9ab00,stroke-width:1.5px
    class D,S,E,X,FT,R,F,C nodeStyle
    class V decision
```

> 各節點的詳細行為見下方「路由規則」與 Verifier / Refiner / Critic 說明。

**路由規則**（三條檢索支線 `search` / `extension_function` / `experience_search` 依旗標並行觸發）：
| 問題類型 | professor_query | needs_sql_search | needs_experience | 執行路徑 |
|---|---|---|---|---|
| 一般申請要求（GPA/TOEFL/deadline） | 無 | true | false | `decompose → search → verify → finalize → critic` |
| SQL 結構化欄位查不到（如學分數） | 無 | true | false | `... → search → verify(不足) → fulltext → verify(足夠) → finalize → critic` |
| 純教授查詢（研究領域/論文） | 有 | false | false | `decompose → extension_function → verify → finalize → critic` |
| 教授 + 申請要求混合 | 有 | true | false | `decompose → (search ‖ extension_function) → verify → finalize → critic` |
| 錄取經驗 / 某分數有無機會 | 無 | 視情況 | true | `decompose → (search ‖ experience_search) → verify(經驗題短路放行) → finalize → critic` |
| 全文檢索後仍不足但曾找到資料 | - | - | - | `... → verify → fulltext → verify → refine → (search ‖ …) → verify → finalize` |

**分層檢索（fallback 順序）**：text-to-SQL 查結構化欄位 → 不足時 fulltext 對 `document_chunks` 全文檢索 → 仍不足時 refine 改寫重查（最多 1 輪）。觸發 fallback 的條件是「Verifier 判定資料不足」，不是「SQL 回傳 0 筆」，因此「SQL 查到 program 列但缺該欄位」（如學分數不在結構化欄位）也能正確觸發全文檢索。

> Verifier / Refiner / Critic 各節點的判斷標準、以及分層檢索的完整實作細節，見 [後端技術細節](backend/README.md)。

### 重點特色
- **分層檢索**：SQL 查結構化欄位 → 不足時全文檢索補內文 → 仍不足才改寫重查，兼顧精準與涵蓋率。
- **申請經驗回報**：問「錄取者背景 / 某分數有無機會」時查 GradCafe / 一畝三分地的錄取案例，並標註「非官方經驗談」、禁止當成錄取門檻。
- **不亂編答案**：檢索結果先經 Verifier 判斷是否文不對題，生成後再經 Critic 複查有無幻覺，有疑慮就誠實告知或附上警告。
- **教授即時查詢**：問題提到教授姓名時即時呼叫 SerpAPI 抓取研究領域 / 論文。
- **未收錄學校辨識**：分辨「學校不在收錄範圍」與「有收錄但查無該欄位」，給對應的誠實回覆。
- **模型分級**：判斷與結構化任務用 `gpt-4.1`、答案生成用 `gpt-4o`。
- **來源可追溯**：答案附上官網來源連結。
- **串流回應**：透過 SSE 即時回傳思考與檢索過程。

---

## 專案結構

```text
.
├── backend/                 # FastAPI、檢索邏輯、LangGraph Agent
│   ├── api.py                 # API 入口（SSE 串流）
│   └── scripts/
│       ├── db/                  # DB 連線與操作
│       ├── retriever/           # sql_search + fulltext_search + applicant_search（經驗）+ LangGraph agent
│       ├── generator/           # OpenAI 答案生成（分級模型）
│       └── professor_fetcher/   # SerpAPI 教授資料抓取
├── frontend/                # React/Vite：申請經驗上傳與依學校查詢
├── data_crawler/            # LangGraph 爬蟲：抓取頁面 → LLM 抽取結構化欄位 + 切段全文 → 寫入 DB（正式資料源）
├── crawler/                 # 舊版 Playwright 爬蟲 + 設定（root_url / 黑名單，data_crawler 沿用其設定）
├── db/                      # schema（init_db.sql）+ migrations（applicant_reports）+ 測試資料（data/）+ 載入腳本
└── requirements.txt          # 所有 Python 依賴（backend + data_crawler + crawler）
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
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/studyabroad
OPENAI_API_KEY=your_openai_api_key   # 判斷型任務與答案生成共用這把 key
OPENAI_MODEL=gpt-4.1                 # 判斷/結構型任務（decomposer/verifier/critic/text-to-SQL）
OPENAI_ANSWER_MODEL=gpt-4o           # 最終答案生成（面向使用者的長文，用強一點的模型）
SERPAPI_KEY=your_serpapi_key         # 教授查詢功能專用
```

> 模型分級：判斷與結構化任務使用 `gpt-4.1`；最終答案生成預設使用 `gpt-4o`。兩者皆可用上述環境變數各自覆寫。

> 註：Windows + Docker Desktop 下建議用 `127.0.0.1` 而非 `localhost`，避免 IPv6 解析 fallback 造成連線延遲。

### 4. 初始化資料庫

所有指令從專案根目錄執行，入口為 `backend/scripts/run.py`：

| 指令 | 說明 |
|------|------|
| `python backend/scripts/run.py init-full` | ⭐ 一鍵建好三張表：`setup` + `init-schema` + `load-schools` + `init-experience` + 社群回報載入 |
| `python backend/scripts/run.py init-all` | 一次完成 `setup` + `load-schools`（不含經驗表 / 社群回報） |
| `python backend/scripts/run.py setup` | 檢查連線，資料庫不存在則建立 |
| `python backend/scripts/run.py init-schema` | 依 `db/init_db.sql` 重建資料表（會清空重建） |
| `python backend/scripts/run.py init-experience` | 冪等建立使用者申請經驗表（不清除既有資料） |
| `python backend/scripts/run.py load-schools` | 灌入 `db/data/schools_data.json` 的測試資料 |
| `python backend/scripts/run.py verify-db` | 全局輸出所有學校/program 欄位、deadlines、獎助、材料、頁面摘要、chunks 與 review queue |
| `python backend/scripts/run.py clear-crawler-data --yes` | 清除所有爬蟲 DB 資料、checkpoints 與生成 JSON；保留 schema 與使用者經驗 |

```bash
python backend/scripts/run.py init-full   # 一鍵建好三張表 + 灌入資料（含社群回報）
python backend/scripts/run.py verify-db   # 確認資料已寫入
```

### 啟動經驗上傳與查詢網頁

先啟動 FastAPI：

```bash
uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
```

再開另一個終端啟動前端：

```bash
cd frontend
npm install
npm run dev
```

開啟 `http://localhost:5173`。開發模式會自動將 `/api` 代理至本機 8000 port；部署到不同網域時可在前端 `.env` 設定 `VITE_API_BASE_URL`，並在後端以 `CORS_ORIGINS` 設定允許的前端來源。

**（選配）載入申請經驗回報**：GradCafe / 一畝三分地的錄取案例，清洗後寫入獨立的 `applicant_reports` 表（加法式 migration，不影響上面的 programs 家族表）。

```bash
python db/load_applicant_reports.py --migrate   # 首次：建表 + 載入
python db/load_applicant_reports.py             # 之後重跑：只載入（去重 upsert）
```

### 5. 測試檢索與問答（CLI）

| 指令 | 說明 |
|------|------|
| `python backend/scripts/run.py search "問題"` | 只測 text-to-SQL：印出產生的 SQL 與查詢結果 |
| `python backend/scripts/run.py rag "問題"` | 單次 SQL 查詢 + LLM 生成答案（不跑 LangGraph 迴圈） |
| `python backend/scripts/run.py agent "問題"` | 完整跑一次 LangGraph Agent 流程（正式問答用這個） |

```bash
python backend/scripts/run.py agent "Compare Stanford and CMU GPA requirements"
python backend/scripts/run.py search "MIT TOEFL 最低幾分"
```

> `--max-steps N`：Agent 模式最大迭代次數（預設 5）。

---

## Docker 快速開始（推薦）

目前 `docker-compose.yml` 只有 `db` 與預留的 `backend` service，尚未加入 frontend service。經驗分享前端請先依上方步驟以 Vite 在本機啟動。

若要用 `professor_fetcher` 的 CLI 手動抓取教授資料並保留到本機，需在 compose 的 `backend` 取消註解那段 `./crawler/data:/app/crawler/data` volume（該目錄需可寫，勿設 `:ro`）；即時查詢（Agent 自動抓取）不寫檔，無此需求。

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

三個資料源、9 張核心表 + `applicant_reports` + `user_experiences`，schema 與各檔案用途詳見 [`db/README.md`](db/README.md)。

重點：正式資料由 `data_crawler/` 爬蟲寫入 `programs` 家族；`db/data/schools_data.json` 是測試假資料（目前 5 校）。新增學校時記得同步更新 `backend/scripts/retriever/agent.py` 的 `_SCHOOL_ALIASES`，Decomposer 才能辨識學校縮寫/別名。

---

## 文件連結
- [後端技術細節](backend/README.md) —— 分層檢索、品質控管節點、事件串流、教授查詢實作
- [爬蟲文件](data_crawler/README.md) —— data_crawler 資料擷取 pipeline 的用法、graph 結構、抽取邏輯
