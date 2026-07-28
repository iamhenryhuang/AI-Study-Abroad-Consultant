# Study Abroad Consultant — 後端技術細節

> 快速開始、CLI 指令、資料庫 schema、架構圖請見專案根目錄 [README](../README.md)。
> 本文只補充後端實作的技術細節。

## 分層檢索策略

Agent 的檢索分三層 fallback，觸發條件是「Verifier 判定資料不足」（不是「SQL 回傳 0 筆」）：

1. **Text-to-SQL 主檢索**（`retriever/sql_search.py`）：查 `programs` 及其子表的結構化欄位。執行前檢查白名單表格、只能 `SELECT`、單一語句。
2. **混合檢索 fallback**（`retriever/hybrid_search.py`）：SQL 不足時對 `document_chunks` 做混合檢索——BGE-M3 向量 + FTS（`fts_vector`）以 RRF（k=60）融合，再經 BGE-reranker-v2-m3 重排序，補上結構化欄位涵蓋不到的內文細節（如學分數、課程規定）。**自動降級**：模型未下載、`sentence-transformers` 缺失或 DB 向量查詢失敗時，降級為純 FTS（`retriever/fulltext_search.py`），行為與升級前相同；embedding 未 backfill 時 RRF 自然退化為 FTS-only 排名（不觸發降級）。
3. **改寫重查**（Refiner）：混合檢索仍不足且尚未重試時，改寫查詢再跑一輪 SQL（最多 1 輪）。

因為觸發條件是「Verifier 判不足」，所以「SQL 查到 program 列但缺該欄位」也能正確 fallback 到混合檢索，而不只是「完全查無此校」才觸發。

**向量檢索前置條件**：`document_chunks.embedding`（vector 1024 維）+ HNSW 索引已在 schema 就緒；向量半邊要生效需先跑 `python -m data_crawler.backfill_embeddings` 補齊向量。模型路徑由 env `BGE_EMBED_MODEL_PATH` / `BGE_RERANKER_MODEL_PATH` 指定（未設定時從 HuggingFace 線上下載）。

**本機沒裝 embedding/reranker 模型時**：設 `ENABLE_HYBRID_SEARCH=false`（`.env`），fallback 會直接跳過向量檢索、不嘗試載入模型，直接走純 FTS，避免每次查詢都先花時間載入模型才失敗降級。預設 `true`。

## 申請經驗檢索（意圖觸發的並行支線）

與上面三層 fallback 不同，經驗檢索由 Decomposer 的意圖判斷（`needs_experience`）觸發，走獨立的並行節點 `experience_search`（`retriever/applicant_search.py`），查 `applicant_reports` 表：

- 只在使用者問「錄取機會 / 錄取者背景 / 案例 / 某分數有無機會」這類問題時才觸發；問官方申請要求時不查。
- 這是非官方、有樣本偏誤的經驗談。generator 的 `_SYSTEM_PROMPT` 有硬性護欄：引用須標註「網路申請經驗回報（非官方）」，禁止把個案當成錄取門檻或保證。
- 經驗題在 Verifier 會短路放行（其「資訊點是否被觸及」的檢查對無標準答案的經驗題不適用），確保撈到案例就能作答。

## 模型分級

判斷/結構型任務（decomposer、verifier、critic、text-to-SQL）用 `OPENAI_MODEL`（預設 `gpt-4.1`）；最終答案生成用 `OPENAI_ANSWER_MODEL`（預設 `gpt-4o`）。判斷型呼叫 `call_llm` 固定 `temperature=0` 以求輸出穩定。皆可用環境變數覆寫。

## 品質控管節點（Verifier / Refiner / Critic）

**Verifier**：檢索完、生成前判斷資料是否足以回答。只攔截明顯「文不對題」或「完全無關」的資料（例如同名教授撈錯人、查詢命中錯誤學校），允許部分足夠或能合理推論的資料通過（缺的細節由 finalize 生成階段自然告知使用者）。它本身不重試，只輸出 `is_sufficient` 與 `insufficiency_reason` 供路由決定下一步。

**Refiner**：Verifier 判不足、且「曾檢索到資料」、且全文檢索也做過後才觸發。參考 `insufficiency_reason` 重新改寫子問題或修正 `professor_query` 的學校資訊，最多重試 1 輪，避免無限迴圈；若原問題前提本身錯誤（例如問的教授根本不在該校），重試後仍會誠實拒答而非產生幻覺答案。

**Critic**：只在 finalize 真的呼叫 LLM 生成過答案時才執行，檢查答案內容是否有具體、可查證的陳述在參考資料中找不到根據，發現問題就在答案後面附上警告文字，不重新生成——短路的誠實告知路徑（查無資料、資料不足）不需要跑 Critic。

## Agent 事件串流

`POST /api/chat` 以 SSE 將事件串流給客戶端，事件類型：
`thinking` / `tool_call` / `tool_result` / `llm_call` / `answer_chunk` / `answer` / `error`

對應 [api.py](api.py) 與 [scripts/retriever/agent/runtime.py](scripts/retriever/agent/runtime.py)、[scripts/retriever/agent/nodes/](scripts/retriever/agent/nodes/) 中發送的事件。

**實作細節（`contextvars` 而非 `threading.local`）**：`_emit` 透過 `state.py` 的 `_on_event_var`（`contextvars.ContextVar`）取得目前請求的 `on_event` callback。LangGraph 用 `ThreadPoolExecutor` 執行並行節點時（例如 `search` 與 `experience_search` 同時跑），會在提交任務前用 `contextvars.copy_context()` 複製當前 context 給子執行緒，`ContextVar` 因此能正確跨執行緒傳遞；改用 `threading.local()` 會導致子執行緒讀不到主執行緒設定的值，`thinking`/`tool_call`/`tool_result` 事件被靜默吞掉（但最終答案不受影響，因為檢索邏輯本身在子執行緒內仍正常執行）。**日後若要新增執行狀態（如 `on_event`、`cancel_event`）務必用 `ContextVar`，不要用 `threading.local`。**

## 教授查詢實作

`extension_function_node`（`retriever/agent/nodes/retrieval.py`）處理兩種互斥的教授查詢意圖，Decomposer 在同一次意圖判斷中決定要走哪一種：

1. **指名查詢（`professor_query`）**：問題中有明確教授姓名時觸發，即時呼叫 SerpAPI 的 `google_scholar_author` API（以 `author_id` 查詢，確保結果只屬於該教授本人），抓取研究領域與最新論文。每次重新抓取、不寫檔快取；只有下方 CLI 手動抓取會把結果存到 `crawler/data/{school_id}_professors.json`。
2. **名單查詢（`professor_list_query`）**：問題只給學校、沒有指名教授（如「某校有哪些教授」「某校有沒有做 XX 的教授」）時觸發，查 `professors` 表（`retriever/professor_list_search.py`），回傳姓名、職稱（若有）、研究領域、官方頁面連結。這是種子資料，不會即時抓取，收錄範圍見 [`db/README.md`](../db/README.md)。

兩者互斥：Decomposer 判斷到明確教授姓名時，`professor_list_query` 一律為 `null`，避免同一題重複觸發兩條路徑。使用者可以先問名單、再針對其中一位接續問研究細節，自然銜接到指名查詢路徑。

手動預先抓取（指名查詢用）：
```bash
python backend/scripts/professor_fetcher/run_fetch.py --name "Andrew Ng" --school "Stanford"
```

新增/更新教授名單種子資料：
```bash
python db/load_professors.py   # 讀 db/data/professors.json，upsert 進 professors 表
```

## 目錄結構

```
backend/scripts/
├── db/                  # DB 連線與 setup/init-schema/verify-db 操作
├── retriever/           # sql_search（text-to-SQL）+ hybrid_search（向量+FTS 混合，降級純 FTS）
│                        #  + applicant_search（申請經驗）+ professor_list_search（教授名單）
│                        #  + LangGraph agent
├── generator/           # OpenAI 答案生成（分級模型）
└── professor_fetcher/   # SerpAPI 教授研究細節即時抓取（指名查詢）
```
