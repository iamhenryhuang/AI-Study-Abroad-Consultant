# Study Abroad Consultant — Backend

Python-based RAG (Retrieval-Augmented Generation) system for US CS Graduate School admissions consulting. Built with FastAPI, PostgreSQL (pgvector), and Google Gemini.

## Core Features
- **Hybrid Search**: Combines semantic vector search (BGE-M3) with PostgreSQL full-text search (FTS), fused using **Reciprocal Rank Fusion (RRF)**.
- **LangGraph Agentic RAG**: A LangGraph `StateGraph` orchestrates the Gemini-driven agent loop across model, tool, and forced-final-answer nodes.
- **Context-Aware Chunking (v4)**:
    - Automatically injects school and page-type metadata into every chunk to prevent vector space collision.
    - Pre-processing cleans web noise (cookie notices, navigation fragments).
    - FAQ-specific splitting keeps Q&A pairs intact using regex synchronization.
- **Professor Intelligence**: Integrated SerpAPI tool for fetching researcher interests and papers from Google Scholar.
- **Reranking**: Secondary ranking via BGE-Reranker-v2-m3 (Cross-Encoder) for precision.

## Tech Stack
- API: FastAPI
- Agent Orchestration: LangGraph
- Model: Google Gemini 2.5 Flash
- Database: PostgreSQL + pgvector (HNSW Indexing)
- Embedder: BAAI/bge-m3 (1024-dim)
- Reranker: BAAI/bge-reranker-v2-m3

## Setup
Create `.env` in the `backend/` directory:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/db_name
GOOGLE_API_KEY=your_gemini_key
SERPAPI_KEY=your_serpapi_key

# Optional: Local model paths
BGE_EMBED_MODEL_PATH=/path/to/bge-m3
BGE_RERANKER_MODEL_PATH=/path/to/bge-reranker-v2-m3
```

## CLI Usage (Run from project root)

### Database Management
| Command | Action |
| :--- | :--- |
| `python backend/scripts/run.py init-all` | Run setup + full import (Resets all tables). |
| `python backend/scripts/run.py setup` | Check connection and create database. |
| `python backend/scripts/run.py import` | Rebuild schema and re-import all JSON files in `data/`. |
| `python backend/scripts/run.py embed` | Incremental import: Chunk and embed data without resetting tables. |
| `python backend/scripts/run.py verify-db` | Check database stats and school distribution. |
| `python backend/scripts/run.py verify-vdb` | Check vector counts and HNSW index health. |

### Retrieval & RAG
| Command | Action |
| :--- | :--- |
| `python backend/scripts/run.py search "QUERY"` | Test hybrid retrieval and view raw scores. |
| `python backend/scripts/run.py rag "QUERY"` | Execute standard RAG pipeline (Search -> Rerank -> LLM). |
| `python backend/scripts/run.py agent "QUERY"` | Execute the LangGraph agent workflow (multi-step reasoning with tool calls). |

**Common Flags:**
- `--school [sid]`: Filter results to a specific school (e.g., `cmu`, `mit`).
- `--max-steps [N]`: Set max iterations for Agentic mode (Default: 5).

Example:

```bash
python backend/scripts/run.py agent "MIT MSCS deadline?" --max-steps 2
```

Notes:
- The CLI entrypoint now strips both flag names and flag values from the query string, so `--max-steps 2` will not pollute the user query.
- This command path was smoke-tested successfully after the LangGraph migration.

## Agent Event Stream

The backend exposes `POST /api/chat`, which streams JSON events to the frontend.

Event types:
- `thinking`
- `tool_call`
- `tool_result`
- `answer`
- `error`

This is the contract consumed by the frontend chat UI and mirrors the events emitted inside [backend/api.py](backend/api.py) and [backend/scripts/retriever/agent.py](backend/scripts/retriever/agent.py).

### Professor Fetcher
```bash
# Basic fetch
python -m backend.scripts.professor_fetcher.run_fetch --name "Ming-Feng Tsai" --school "NCCU"

# Fetch + Immediate Embedding
python -m backend.scripts.professor_fetcher.run_fetch --name "Andrew Ng" --school "Stanford" --embed
```

## Chunking Strategy
Chunks are dynamically sized based on identified URL path types:

| Page Type | Chunk Size | Overlap | Strategy |
| :--- | :--- | :--- | :--- |
| **FAQ** | 1800 | 360 | Regex-based QA pair alignment. |
| **Admission** | 1500 | 300 | Process-focused context preservation. |
| **Checklist** | 1000 | 200 | Granular attribute extraction. |
| **Prof Profile**| 1800 | 360 | Researcher bio preservation. |
| **Prof Paper** | 800 | 160 | Abstract-centric windowing. |
| **General** | 1400 | 280 | Fallback default. |
