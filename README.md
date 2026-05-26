# Study Abroad RAG: North America CS Consultant

> LangGraph-powered RAG system for North American CS Master's admissions advising.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-v4-38B2AC.svg)](https://tailwindcss.com/)

---

## Overview

Study Abroad RAG is a practical QA system for North American CS Master's applications.  
It replaces manual page-by-page browsing with one interface for requirements, deadlines, professor information, and supporting sources.

The backend uses a LangGraph workflow: analyze intent, choose tools, retrieve relevant data, then produce a Traditional Chinese answer with citations.

### Key Features
- **Agentic Retrieval Workflow**: `decompose -> extension_function -> search -> plan -> finalize`.
- **Alternative School Recommendation**: Supports backup-school suggestions from user profile (GPA/TOEFL/GRE) and admission statistics/experience data.
- **Professor Runtime Fetch**: For named professor queries, agent calls SerpAPI in the extension path and merges results into final context.
- **Hybrid Search + Rerank**: Vector search (BGE-M3) + PostgreSQL FTS (RRF) + Cross-Encoder reranker.
- **Chunk Compression**: Gemini keeps only query-relevant sentences while preserving source metadata.
- **Streaming Responses**: Frontend receives `thinking`, `tool_call`, `tool_result`, `llm_call`, `answer_chunk`, `answer`, `error` events.
- **Traceable Output**: Answers include source URLs from official university pages whenever available.
- **Crawler + Ingestion Pipeline**: Playwright crawler, scoring/classification, then chunk/embed/store.

---

## Architecture

```text
.
├── backend/                # FastAPI, retrieval pipeline, LangGraph agent
│   ├── api.py              # API server with SSE support
│   └── scripts/            # Core RAG & ingestion logic
│       ├── db/             # DB connection & operations
│       ├── embedder/       # Chunking, vectorization, pipeline
│       ├── retriever/      # Search, reranker, agent
│       ├── generator/      # Gemini answer generation
│       └── professor_fetcher/  # SerpAPI Google Scholar fetcher
├── crawler/                # Playwright-based web crawler
│   ├── run_crawler.py      # Crawler entry point
│   ├── url_crawler.py      # URL discovery & page fetching
│   ├── score.py            # Page scoring & classification
│   ├── save_result.py      # Write results to data/
│   ├── clean_json_data.py  # Post-crawl data cleaning
│   └── data/               # Crawled data ([{school_id, url, passed_types, data}])
├── frontend/               # React + Tailwind v4 + Vite
│   ├── src/components/     # UI components
│   └── src/hooks/          # Real-time streaming hooks
├── db/                     # PostgreSQL schema (init_db.sql)
└── requirements.txt        # All Python dependencies (backend + crawler)
```

---

## Quick Start

### 1. Prerequisites
- **Python 3.10+**
- **Node.js 18+**
- **PostgreSQL** with the [pgvector](https://github.com/pgvector/pgvector) extension

### 2. Python Virtual Environment

From the project root:

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash (Windows)
source .venv/bin/activate     # macOS / Linux
```

After activation your prompt shows `(.venv)`. Use `deactivate` to exit.

### 3. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

If you see `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`, recreate the venv:

```bash
rm -rf .venv
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

### 4. Environment Variables

Create `backend/.env`:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/study_abroad_rag
GOOGLE_API_KEY=your_gemini_api_key      # answer generation
GROQ_API_KEY=your_qroq_key              # intent analysis
COMPRESS_KEY=your_gemini_api_key        # chunk compression (can reuse the same key)
SERPAPI_KEY=your_serpapi_key            # professor fetch only
BGE_EMBED_MODEL_PATH=path/to/bge-m3     # local model path (optional)
BGE_RERANKER_MODEL_PATH=path/to/bge-reranker-v2-m3
```

### 5. Crawl & Import Data

**Step 1 — Crawl university pages:**
```bash
cd crawler
python run_crawler.py
```
Output saved to `crawler/data/*.json`.

**Step 2 — Import into DB (init schema + embed + store):**
```bash
python backend/scripts/run.py import
```

### 6. Start Services

**Terminal A — Backend:**
```bash
python -m uvicorn backend.api:app --reload --port 8000
```

**Terminal B — Frontend:**
```bash
cd frontend
npm install
npm run dev
```

---

## Docker Quick Start

> 使用 Docker 可跳過 Python 環境、PostgreSQL 安裝與 model 下載設定，推薦用於快速部署或跨機器複現。

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS) 或 Docker Engine (Linux)

### 1. 準備 .env

`.env` 放在 `backend/` 目錄（與本地開發一致）：

```bash
cp .env.example backend/.env
```

編輯 `backend/.env`，填入以下必要欄位：

```env
GOOGLE_API_KEY=your_gemini_api_key
SERPAPI_KEY=your_serpapi_key
GROQ_API_KEY=your_groq_api_key
POSTGRES_PASSWORD=postgres        # 可自行更改
```

### 2. 第一次啟動

```bash
docker compose up --build
```

**第一次啟動會自動下載兩個 model**（約 3.4 GB），需要幾分鐘：

| Model | 用途 | 大小 |
|-------|------|------|
| BAAI/bge-m3 | Embedding | ~2.3 GB |
| BAAI/bge-reranker-v2-m3 | Reranker | ~1.1 GB |

下載完後 model 存入 Docker named volume (`hf_models`)，**之後重啟不會重新下載**。

### 3. 之後的啟動

```bash
docker compose up
```

### 4. 匯入資料

資料庫 schema 在 `db/init_db.sql` 會在 db container 第一次啟動時自動建立。

若要匯入爬蟲資料（需先確保 `crawler/data/` 有 JSON 檔案）：

```bash
docker compose exec backend python backend/scripts/run.py import
```

### 5. 服務端點

| 服務 | URL |
|------|-----|
| Frontend | http://localhost |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

### 常用指令

```bash
# 停止所有服務
docker compose down

# 停止並清除資料庫資料（model 不受影響）
docker compose down -v

# 查看 backend log
docker compose logs -f backend

# 進入 backend container 執行 CLI 工具
docker compose exec backend python backend/scripts/run.py agent "Compare Stanford and CMU"
```

---

## CLI Tools

Manage the pipeline from `backend/scripts/run.py`:

| Command | Description |
|---------|-------------|
| `init-all` | Run `setup` + full import in one command |
| `setup` | Check DB connection, create DB if missing |
| `import` | Init schema + chunk + embed + store `crawler/data/` |
| `embed` | Incremental chunk + embed into DB |
| `verify-db` | Check DB contents |
| `verify-vdb` | Check vector DB and index status |
| `search "QUERY"` | Hybrid vector + keyword search |
| `rag "QUERY"` | Standard RAG (search -> rerank -> answer) |
| `agent "QUERY"` | Full LangGraph agent workflow |

Common flags:
- `--school [sid]`: Restrict `search`/`rag` to a specific school.
- `--max-steps [N]`: Control agent recursion limit for `agent` command.

```bash
python backend/scripts/run.py search "MIT deadline" --school mit
python backend/scripts/run.py agent "Compare Stanford and CMU GPA requirements"
```

---

## Professor Data Fetcher

There are two ways to get professor data:

**1. Agent (runtime)** — when a query mentions a specific professor name, the agent auto-detects the intent in the Decomposer step, calls SerpAPI live via `extension_function`, and merges fetched docs into final answer context.

**2. CLI (pre-fetch + embed)** — manually fetch and embed a professor's data:

```bash
python backend/scripts/professor_fetcher/run_fetch.py \
  --name "Andrew Ng" \
  --school "Stanford" \
  --embed
```

Results are saved to `crawler/data/{school_id}_professors.json` and optionally embedded into the DB with `--embed`.

---

## Documentation
- [Backend Documentation](backend/README.md)
- [Frontend Documentation](frontend/README.md)
