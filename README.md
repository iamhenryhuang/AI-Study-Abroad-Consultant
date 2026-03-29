# Study Abroad RAG: North America CS Consultant

> LangGraph-powered RAG system for North American CS Master's admissions advising.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-v4-38B2AC.svg)](https://tailwindcss.com/)

---

## Overview

Study Abroad RAG is an intelligent advisory tool designed to simplify the complex process of researching North American CS Master's programs. Instead of manually scouring hundreds of university pages, users can ask the system specific questions about admission requirements, funding, faculty, and deadlines.

The system does not just retrieve passages. It runs a LangGraph-based agent workflow that can decide what to search, call retrieval tools step by step, and then synthesize a cited answer in Traditional Chinese.

### Key Features
- **LangGraph Agent Workflow**: Uses a LangGraph `StateGraph` to run the agent loop across decomposer, searcher, planner, and finalizer steps.
- **Real-Time Thinking**: Streams agent events such as `thinking`, `tool_call`, `tool_result`, and `answer` to the frontend.
- **High Precision**: Powered by **BGE-M3** embeddings and a **Cross-Encoder Reranker** for the best document retrieval.
- **Contextual Chunking**: Chunking strategy that preserves metadata and FAQ structures, sized by content type via `passed_types`.
- **Verified Sources**: Every claim includes a direct source URL to the university's official page.
- **Web Crawler**: Playwright-based crawler that scores and classifies pages by type before ingestion.

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
│   ├── save_result.py      # Write results to school_data/
│   ├── clean_json_data.py  # Post-crawl data cleaning
│   └── school_data/        # Crawled data ([{school_id, url, passed_types, data}])
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
GOOGLE_API_KEY=your_gemini_api_key
SERPAPI_KEY=your_serpapi_key          # only needed for professor_fetcher
BGE_EMBED_MODEL_PATH=path/to/bge-m3   # local model path (optional)
BGE_RERANKER_MODEL_PATH=path/to/bge-reranker-v2-m3
```

### 5. Crawl & Import Data

**Step 1 — Crawl university pages:**
```bash
cd crawler
python run_crawler.py
```
Output saved to `crawler/school_data/*.json`.

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
| `setup` | Check DB connection, create DB if missing |
| `import` | Init schema + chunk + embed + store `crawler/school_data/` |
| `verify-db` | Check DB contents |
| `search "QUERY"` | Hybrid vector + keyword search |
| `agent "QUERY"` | Full LangGraph agent workflow |

```bash
python backend/scripts/run.py search "MIT deadline" --school mit
python backend/scripts/run.py agent "Compare Stanford and CMU GPA requirements"
```

---

## Professor Data Fetcher

Fetch individual professor data from Google Scholar via SerpAPI:

```bash
python backend/scripts/professor_fetcher/run_fetch.py \
  --name "Andrew Ng" \
  --school "Stanford" \
  --embed
```

Results are saved to `crawler/school_data/{school_id}_professors.json` and optionally embedded into the DB with `--embed`.

---

## Documentation
- [Backend Documentation](backend/README.md)
- [Frontend Documentation](frontend/README.md)
