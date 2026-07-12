# Hybrid Search Fallback 升級設計

日期：2026-07-11
狀態：已核准（方案 A）

## 目標

把 agent 的文本檢索 fallback 從純 FTS 升級為 backup（v1.0.0）的混合檢索鏈：
**向量（BGE-M3）+ FTS 的 RRF 融合 → BGE CrossEncoder 重排序**，並保留純 FTS 作為降級路徑。

檢索路線維持現行架構不變：**text-to-SQL 優先，SQL 結果不足時才走文本檢索 fallback**。

## 背景與現況

- 現行 main 已是 text-to-SQL 架構：`sql_search`（查 `programs` 家族）→ Verifier 判不足 →
  `fulltext_search_node`（純 FTS 查 `document_chunks`）。
- `db/init_db.sql` 的 `document_chunks` 保留 `embedding vector(1024)` 欄位與 HNSW 索引，schema 免改。
- 寫入端已就緒：`data_crawler/backfill_embeddings.py` 可補齊向量（BGE-M3，1024 維，
  `normalize_embeddings=True`）。平常 pipeline 以 `ENABLE_EMBEDDING=off` 執行，所以
  **DB 中可能存在 `embedding IS NULL` 的 chunk**。
- `sentence-transformers` 已在 `requirements.txt`，無需新增依賴。
- backup 位置：`backup/AI-Study-Abroad-Consultant-1.0.0/backend/scripts/retriever/`
  （`hybrid_search.py`、`reranker.py`）與 `embedder/vectorize.py`。

## 設計（方案 A：升級 fallback 節點內部實作，graph 拓撲不動）

```
sql_search 不足 → fulltext 節點
                    └─ hybrid_search（向量+FTS+RRF → reranker）
                         └─ 任一環節不可用（模型未下載 / embedding 未 backfill / 執行錯誤）
                              → 自動降級回現行純 FTS（fulltext_search）
```

### 新增檔案（`backend/scripts/retriever/`）

1. **`vectorize.py`** — 查詢端 embedding
   - BGE-M3 `SentenceTransformer` singleton（延遲載入，首次呼叫才載模型）
   - env `BGE_EMBED_MODEL_PATH`（與 `data_crawler/backfill_embeddings.py` 同名變數，模型共用一份；
     未設定時預設 `BAAI/bge-m3` 線上下載）
   - `normalize_embeddings=True`（與寫入端一致，餘弦距離才有意義）
   - 提供 `embed_query(text: str) -> list[float]`

2. **`reranker.py`** — 從 backup 原樣移植
   - BGE-reranker-v2-m3 `CrossEncoder` singleton
   - env `BGE_RERANKER_MODEL_PATH`；沿用 backup 的 HF hub snapshot 解析與 SSL env 清理邏輯
   - `rerank(query, documents, top_n) -> list[dict]`，輸入文件需含 `chunk_text`，輸出附 `rerank_score`

3. **`hybrid_search.py`** — RRF 混合檢索，移植時修改三處：
   - `vector_matches` CTE 加 `WHERE embedding IS NOT NULL`（排除未 backfill 的列，避免 NULL 距離污染排名）
   - `conn.read_only = True`（與現行 `sql_search` / `fulltext_search` / `applicant_search` 統一）
   - 回傳 doc 形狀對齊現行 `fulltext_search`：`chunk_text` / `source_url` / `school_id` /
     `university_name`（下游 verifier / finalizer / `format_context_for_prompt` 零改動）
   - RRF 公式維持 backup 原樣：`1/(60+rank)`，向量與 FTS 兩路各取 initial_k = top_k*2 候選
   - reranker 失敗時退回 RRF 排序結果（backup 已有此 try/except，保留）

### 修改既有檔案

- **`agent.py` 的 `_fulltext_one_query`**：改為先試 `hybrid_search`，任何例外（模型載入失敗、
  查詢錯誤）即降級呼叫原 `fulltext_search`，並印出降級原因。`fulltext_search.py` 本身不動。
  Graph 的九個節點、路由、AgentState 全部不變。
- **`.env.example`**：新增 `BGE_EMBED_MODEL_PATH`、`BGE_RERANKER_MODEL_PATH`（附註解說明選配、
  未設定時的行為）。
- **`backend/README.md`**：fallback 說明更新為 hybrid，註明前置條件。

### 不改的東西

- `requirements.txt`（`sentence-transformers>=5.0` 已在）
- `db/init_db.sql`（schema 已相容）
- `fulltext_search.py`、`applicant_search.py`、`sql_search.py`
- LangGraph 圖結構與所有既有節點/路由

## 行為細節

- **embedding 全部未 backfill 時**：`vector_matches` 為空，RRF 自然退化為 FTS-only 排名，
  hybrid 仍可運作（不觸發降級）；reranker 照常重排 FTS 候選。
- **模型無法載入時**（未下載且無網路等）：拋例外 → `_fulltext_one_query` 捕捉 → 降級純 FTS，
  行為與升級前完全相同。
- **前置條件**（寫入 README）：向量檢索要發揮效果，需先跑
  `python -m data_crawler.backfill_embeddings`。

## 測試

`tests/` 新增 hybrid_search 單元測試：
- RRF SQL 組裝正確（含 school_id 過濾、`embedding IS NOT NULL`）
- mock embedding 模型，驗證回傳 doc 形狀對齊 `fulltext_search`
- 降級鏈：`vectorize` 拋例外時 `_fulltext_one_query` 走純 FTS
- reranker 失敗時退回 RRF 排序結果
