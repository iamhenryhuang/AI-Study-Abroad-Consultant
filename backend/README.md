# Study Abroad Consultant — 後端技術細節

> 快速開始、CLI 指令、資料庫 schema、架構圖請見專案根目錄 [README](../README.md)。
> 本文只補充後端實作的技術細節。

## 分層檢索策略

Agent 的檢索分三層 fallback，觸發條件是「Verifier 判定資料不足」（不是「SQL 回傳 0 筆」）：

1. **Text-to-SQL 主檢索**（`retriever/sql_search.py`）：查 `programs` 及其子表的結構化欄位。執行前檢查白名單表格、只能 `SELECT`、單一語句。
2. **全文檢索 fallback**（`retriever/fulltext_search.py`）：SQL 不足時對 `document_chunks` 做 PostgreSQL 原生全文檢索（`fts_vector`），補上結構化欄位涵蓋不到的內文細節（如學分數、課程規定）。**不需要 embedding model** —— `fts_vector` 由 DB trigger 在 chunk 寫入時自動生成。
3. **改寫重查**（Refiner）：全文檢索仍不足且尚未重試時，改寫查詢再跑一輪 SQL（最多 1 輪）。

因為觸發條件是「Verifier 判不足」，所以「SQL 查到 program 列但缺該欄位」也能正確 fallback 到全文檢索，而不只是「完全查無此校」才觸發。

**語意向量檢索為預留能力**：`document_chunks.embedding`（vector 1024 維）+ HNSW 索引已在 schema 就緒但目前未使用；接上 embedding model 即可啟用，無需改 schema。

## 品質控管節點（Verifier / Refiner / Critic）

**Verifier**：檢索完、生成前判斷資料是否足以回答。只攔截明顯「文不對題」或「完全無關」的資料（例如同名教授撈錯人、查詢命中錯誤學校），允許部分足夠或能合理推論的資料通過（缺的細節由 finalize 生成階段自然告知使用者）。它本身不重試，只輸出 `is_sufficient` 與 `insufficiency_reason` 供路由決定下一步。

**Refiner**：Verifier 判不足、且「曾檢索到資料」、且全文檢索也做過後才觸發。參考 `insufficiency_reason` 重新改寫子問題或修正 `professor_query` 的學校資訊，最多重試 1 輪，避免無限迴圈；若原問題前提本身錯誤（例如問的教授根本不在該校），重試後仍會誠實拒答而非產生幻覺答案。

**Critic**：只在 finalize 真的呼叫 LLM 生成過答案時才執行，檢查答案內容是否有具體、可查證的陳述在參考資料中找不到根據，發現問題就在答案後面附上警告文字，不重新生成——短路的誠實告知路徑（查無資料、資料不足）不需要跑 Critic。

## Agent 事件串流

`POST /api/chat` 以 SSE 將事件串流給客戶端，事件類型：
`thinking` / `tool_call` / `tool_result` / `llm_call` / `answer_chunk` / `answer` / `error`

對應 [api.py](api.py) 與 [scripts/retriever/agent.py](scripts/retriever/agent.py) 中發送的事件。

## 教授查詢實作

`extension_function_node` 在 Decomposer 偵測到教授意圖時，即時呼叫 SerpAPI 的 `google_scholar_author` API（以 `author_id` 查詢，確保結果只屬於該教授本人）。抓取結果快取到 `crawler/data/{school_id}_professors.json`。

手動預先抓取：
```bash
python backend/scripts/professor_fetcher/run_fetch.py --name "Andrew Ng" --school "Stanford"
```

## 目錄結構

```
backend/scripts/
├── db/                  # DB 連線與 setup/init-schema/verify-db 操作
├── retriever/           # sql_search（text-to-SQL）+ fulltext_search（全文檢索）+ LangGraph agent
├── generator/           # OpenAI 答案生成
└── professor_fetcher/   # SerpAPI 教授資料抓取
```
