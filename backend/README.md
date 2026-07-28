# Backend：FastAPI 與 LangGraph Agent

後端提供 SSE 問答 API、申請經驗 API，以及結構化、全文、社群、推薦與教授檢索。安裝與資料初始化請先看專案根目錄 [README](../README.md)。

## API

```bash
uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
```

| 方法 | 路徑 | 用途 |
|---|---|---|
| `POST` | `/api/chat` | 執行 Agent，透過 SSE 回傳過程與答案 |
| `GET` | `/api/health` | 健康檢查 |
| `POST` | `/api/experiences` | 新增使用者申請經驗 |
| `GET` | `/api/experiences` | 依條件查詢使用者經驗 |

`/api/chat` 主要事件類型：`thinking`、`tool_call`、`tool_result`、`llm_call`、`answer_chunk`、`answer`、`error`。

## Agent 檢索

Agent 依問題組合下列資料來源：

| 類型 | 實作 | 資料來源 |
|---|---|---|
| 結構化申請要求 | `retriever/sql_search.py` | `programs` 與子表 |
| 官方頁面全文 | `retriever/fulltext_search.py` | `document_chunks.fts_vector` |
| 向量混合檢索 | `retriever/hybrid_search.py` | embedding + FTS + RRF |
| 錄取經驗 | `retriever/applicant_search.py` | `applicant_reports` |
| 選校推薦 | `retriever/recommend.py` | 社群案例與使用者成績 |
| 教授名單 | `retriever/professor_list_search.py` | `professors` |
| 指名教授 | `professor_fetcher/` | SerpAPI |

一般申請問題先嘗試 text-to-SQL；結果不足或 LLM 不可用時改查官方全文。Embedding 不是全文檢索的必要條件：沒有向量仍可使用 PostgreSQL FTS。

向量檢索需先補齊 embedding：

```bash
python -m data_crawler.backfill_embeddings --batch-size 16
```

不使用本機 embedding/reranker 時，可在 `backend/.env` 設定：

```env
ENABLE_HYBRID_SEARCH=false
```

## LLM fallback

第一次遇到永久 quota 錯誤後，process 內的 circuit breaker 會停止後續 OpenAI 請求，避免每個 Agent 節點重複回傳 429。系統接著使用：

- 本機規則辨識學校、教授、經驗與推薦意圖。
- SQL 不可用時改查 `document_chunks` 全文。
- Verifier 直接採用已檢索資料。
- 最終答案改為附來源的資料庫摘錄。

每次重新啟動 process，第一次 LLM 呼叫仍會重新確認 provider 是否可用。

## 品質控管

- **Verifier**：判斷檢索資料是否與問題相關。
- **Full-text fallback**：結構化欄位不足時補查官方內文。
- **Refiner**：資料不足且仍有可用內容時，最多改寫重查一次。
- **Critic**：只有 LLM 真的生成答案時才檢查是否缺少資料依據。
- **Extractive fallback**：LLM 不可用時直接呈現 DB 內容與來源，不執行 Critic。

社群申請經驗屬非官方資料，答案會標示樣本偏誤，不應解讀成錄取門檻或保證。

## 教授查詢

教授功能分兩種：

1. 問「Stanford 有哪些 AI 教授」：查 `professors` 種子表。
2. 問「Fei-Fei Li 的研究方向」：透過 SerpAPI 查指定教授的研究資訊。

教授種子依 `universities.school_id` 掛載，因此必須先爬完三校：

```bash
python backend/scripts/run.py load-professors
```

正常結果應為 89 位；若顯示「學校不存在」，先用 `verify-db` 確認該校是否在 `universities`，並確認爬完後沒有再次執行 `init-full` 或 `init-schema`。

## CLI

```bash
python backend/scripts/run.py search "Gatech TOEFL 要求"
python backend/scripts/run.py rag "Purdue IELTS 要求"
python backend/scripts/run.py agent "Stanford 有哪些 AI 教授？"
python backend/scripts/run.py verify-db
```

正式功能測試以 `agent` 為主；`search` 只測 text-to-SQL，`rag` 是較簡單的單次檢索流程。

## 目錄

```text
backend/
├── api.py                    FastAPI 與 SSE
└── scripts/
    ├── db/                   DB 連線、初始化與驗證
    ├── generator/            OpenAI client、prompt 與答案生成
    ├── professor_fetcher/    SerpAPI 教授查詢
    └── retriever/
        ├── agent/            LangGraph state、nodes、runtime
        ├── sql_search.py
        ├── fulltext_search.py
        ├── hybrid_search.py
        ├── applicant_search.py
        └── recommend.py
```

執行完整測試：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```
