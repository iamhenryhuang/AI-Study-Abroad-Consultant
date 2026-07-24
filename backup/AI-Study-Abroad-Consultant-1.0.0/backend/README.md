# Study Abroad Consultant — Backend

Backend service for CS graduate admissions QA. Built with FastAPI, PostgreSQL (pgvector), LangGraph, and Gemini.

## Core Features
- **LangGraph Agentic RAG**: A LangGraph `StateGraph` orchestrates:
  ```
  decompose → extension_function → search → plan → finalize
  ```
- **Hybrid Search**: Semantic vector search (BGE-M3) + PostgreSQL FTS, merged by **Reciprocal Rank Fusion (RRF)**.
- **Reranking**: Secondary ranking via BGE-Reranker-v2-m3 (Cross-Encoder).
- **Professor Fetch (runtime)**: If a query names a professor, `extension_function_node` calls SerpAPI and merges results into final context.
- **Alternative Recommendations**: Supports backup-school recommendation and attainability checks based on profile + admissions data.
- **Context-Aware Chunking (v4)**:
    - Automatically injects school and page-type metadata into every chunk to prevent vector space collision.
    - Pre-processing cleans web noise (cookie notices, navigation fragments).
    - FAQ-specific splitting keeps Q&A pairs intact using regex synchronization.
- **Chunk Compression**: After retrieval, Gemini compresses each chunk to only the sentences relevant to the query. Source metadata (`source_url`, `school_id`, `passed_types`) is preserved and merged back after compression.

## Alternative Recommendation Flow (Key Points)
When the user asks for backup schools (or profile suggests target school risk), the flow is:

1. Intent analysis extracts profile and whether alternatives are needed.
2. Candidate backup schools are recommended from admissions statistics + forum experience data.
3. For each recommended school, the retriever tries official DB docs first; if unavailable, it falls back to experience-only docs.
4. Final answer merges official requirements, experience snippets, and source metadata.

## Tech Stack
- API: FastAPI
- Agent Orchestration: LangGraph
- Model: Google Gemini 2.5 Flash
- Database: PostgreSQL + pgvector (HNSW Indexing)
- Embedder: BAAI/bge-m3 (1024-dim)
- Reranker: BAAI/bge-reranker-v2-m3

## Setup

### When should I enter `.venv`?
Activate `.venv` whenever you are going to run backend Python commands, for example:
- Installing packages (`python -m pip install -r requirements.txt`)
- Running CLI (`python backend/scripts/run.py ...`)
- Running API server (`python backend/api.py`)

If your terminal prompt already shows `(.venv)`, you are already inside the virtual environment.

### Open project and enter `.venv`
Run these commands from the project root.

```bash
# Git Bash (Windows)
source .venv/Scripts/activate
```

Exit the virtual environment with:

```bash
deactivate
```

Create `.env` in the `backend/` directory:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/db_name
GOOGLE_API_KEY=your_gemini_key        # answer generation
GROQ_API_KEY=your_qroq_key            # intent analysis
COMPRESS_KEY=your_gemini_key          # chunk compression (can be the same key)
SERPAPI_KEY=your_serpapi_key          # professor fetch only

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
| `python backend/scripts/run.py import` | Rebuild schema and re-import all JSON files in `crawler/data/`. |
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
- `llm_call`
- `answer_chunk`
- `answer`
- `error`

This is the contract consumed by the frontend chat UI and mirrors the events emitted inside [backend/api.py](backend/api.py) and [backend/scripts/retriever/agent.py](backend/scripts/retriever/agent.py).

### Professor Fetcher

The agent detects professor queries automatically at runtime (via Decomposer → `extension_function_node` → SerpAPI). No manual pre-fetching is required for query-time lookups.

To pre-fetch and embed a professor's data into the DB:
```bash
python backend/scripts/professor_fetcher/run_fetch.py --name "Andrew Ng" --school "Stanford" --embed
```

Uses `google_scholar_author` API (by `author_id`) to ensure results are scoped to that specific professor only.

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
