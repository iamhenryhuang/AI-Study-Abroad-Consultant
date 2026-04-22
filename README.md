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
