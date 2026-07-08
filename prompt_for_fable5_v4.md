# 任務：建立 AI Study Abroad Consultant 的 LangGraph 資料擷取與清洗框架（v4）

## 開始前必讀（依重要性排序）

1. `crawler/score.py` ✅ 已分析（見下方）
2. `crawler/url_crawler.py` ✅ 已分析（見下方）
3. `crawler/run_crawler.py` ✅ 已分析（見下方）
4. `crawler/setting/parameter.py` — **未提供**，內含 `CONFIG`（MAX_DEPTH/MAX_PAGES/NUM_WORKERS/CRAWL_DELAY_MS/STRIP_QUERY/USER_AGENT 等）與 `THRESHOLDS`/`PAGE_HINTS`/`MINUS_KEYWORDS`/`URL_PATH_HINTS`。**請 Fable 5 自行讀取**，不要用本文件猜測的欄位名稱去硬套。
5. `crawler/setting/blacklist.py` — **未提供**，內含 `IGNORED_EXTENSIONS`、`BLACKLIST_PATH_FRAGMENTS`。請自行讀取。
6. `crawler/setting/root_url.py` — **未提供**，內含 `SCHOOLS`（每校的 `school_id` + `roots`）。請自行讀取，確認 school_id 與 `universities.school_id` 對得起來。
7. `crawler/save_result.py`（`save_school_results`）與 `crawler/clean_json_data.py`（`clean`）— **未提供**，這兩支是目前 pipeline 最後兩步，可能已經包含有用的清洗邏輯（例如去除重複、過濾低分結果）。**請先讀懂再決定哪些邏輯要保留進新的 LangGraph 節點**，不要整個丟棄重寫。

若讀完後發現與本文件描述不一致，一律以實際程式碼為準，並回報差異。

---

## 一、現有 pipeline 全貌（三支檔案串起來看）

檔案在/home/ckt1022/AI-Study-Abroad-Consultant/crawler之下，可以參考
```
run_crawler.py（多學校 orchestration，ThreadPoolExecutor max_workers=3）
  └─ crawl_school(school)  對每間學校依序執行：
       1. crawl_one_school()      ← url_crawler.py：BFS 找出所有值得爬的 URL，寫 {school_id}_keep.json / _drop.json
       2. score_one_school()      ← score.py：對 keep.json 裡每個 URL 開 Playwright 擷取全文 + 關鍵字評分分類
       3. save_school_results()   ← save_result.py（未讀）：把評分結果存成 {school_id}_data.json
       4. clean()                 ← clean_json_data.py（未讀）：清洗資料（細節未知）
  + SKIP_RECENT 機制：資料檔在 N 小時內更新過就跳過整間學校（增量更新概念，值得沿用）
```

**新版 LangGraph 要做的事**：把這四步從「檔案接檔案」的批次腳本，改成「state 接 state」的 graph，並在第 2 步把關鍵字評分換成 LLM 語意判斷，第 3-4 步的邏輯視實際內容併入 Node 8/Node 13。

### url_crawler.py 分析

| 功能 | 對應函式 | 狀態 |
|---|---|---|
| URL normalize（統一大小寫、去 trailing slash、依 `CONFIG.STRIP_QUERY` 決定要不要去 query、去 fragment）| `normalize_url` | ✅ 已有，直接沿用 |
| 同 root 範圍限制（不只同網域，還限制在同一個 root path 底下）| `is_same_root` | ✅ 已有 |
| 副檔名/黑名單路徑過濾 | `filter_url`（讀 `blacklist.py`）| ✅ 已有 |
| BFS 去重（`visited` set）| `crawl_tree` | ✅ 已有 |
| 深度上限、總頁數上限 | `CONFIG.MAX_DEPTH` / `CONFIG.MAX_PAGES` | ✅ 已有 |
| 請求間 delay | `CONFIG.CRAWL_DELAY_MS`（在 `run_worker` 內）| ✅ 已有（**v2 說沒有是錯的，這裡更正**）|
| 加速：封鎖圖片/字型/CSS 等資源 | `block_resources` | ✅ 已有 |
| 多 root 聚合成單一學校結果 | `crawl_one_school` | ✅ 已有 |

**目前缺的東西**：
- `extract_links` 只回傳連結網址，**沒有保留 anchor text**。如果之後想在爬取「前」用 LLM 判斷 URL 值不值得爬（省 Playwright 開銷），需要 anchor text 當判斷依據，目前拿不到。→ 這是可選的優化，不是必要項，見下方 Node 3 說明。
- 沒有 `robots.txt` 檢查（黑名單是人工維護的路徑關鍵字，不是自動讀 robots.txt）。是否需要補上，你可以之後再評估，不影響主流程。
- URL 發現階段完全是規則式過濾（黑名單），**沒有語意判斷**——語意判斷目前全部發生在 `score.py` 的 `score_page`（爬完全文之後）。這點呼應你「第二點」的需求：語意判斷要從關鍵字評分改成 LLM。
- 幫我將硬透過關鍵字判斷、過濾的部分改為LLM節點進行語意判斷

---

## 二、節點設計（v3）

### Phase 1：URL 發現 — 直接包裝 `url_crawler.py`，不重寫

**Node 1 — `init_crawl`**
初始化 state、查/建 `universities.id`。

**Node 2 — `discover_urls`**（採用選項 B）
把 `crawl_tree` 的邏輯搬進 LangGraph 節點，改成逐層（per depth）執行並回傳，而不是整棵樹一次爬完才回來：
- 每爬完一層（一個 depth），就把這層結果（含 `keep`/`drop`/`external`）寫回 state 並觸發 checkpoint，長時間爬取可以中斷續跑
- `visited_url_hashes` 需要跟著 state 一起被 checkpoint，確保續跑時不會重爬
- 保留原本 `run_worker` 的多執行緒 + `block_resources`（封鎖圖片/字型/CSS）邏輯，只是把「回傳結果」的方式從寫檔案改成回傳 dict/更新 state
- 因為改成逐層執行，之後 Node 9（sufficiency check）判斷資料不足時，可以直接把新的種子 URL 塞回同一個 `url_queue`，用同一棵 BFS 樹繼續往下爬，不用重新呼叫整個 `crawl_one_school`
- `normalize_url`／`is_same_root`／`filter_url`（黑名單）這些函式邏輯不變，直接搬過來用即可，不用重寫

**條件邊 `should_continue_bfs`**：深度未達 `CONFIG.MAX_DEPTH`（或 Node 9 動態調整後的深度預算）且還有待爬 URL 且未達 `CONFIG.MAX_PAGES` → 回 Node 2 爬下一層；否則 → Phase 2

**Node 3 — `url_relevance_filter`（可選，預設不做）**
目前 `extract_links` 沒有 anchor text，若要做「爬取前」的 LLM 判斷需要先修改 `url_crawler.py` 補上 anchor text 擷取。**除非你覺得目前爬到的 URL 數量太多、Playwright 開銷太大，否則先跳過這個節點**，維持現況（黑名單過濾完就全部爬），語意判斷留到 Node 5（爬完內容後用 LLM 判斷）就好。

### Phase 2：擷取（包裝 `score.py` 的擷取部分，分類部分抽出去給 LLM）

**Node 4 — `scrape_page`**
包裝 `extract_page_content_with_js`（完整保留，這部分做得很好），新增：
- PDF 分支（`pymupdf`/`pdfplumber`）
- `structured_tables`（表格轉 markdown，保留欄位對應，見下方原因）
- `structured_markdown`（用 `page.content()` 在展開完成後跑一次 `trafilatura`，保留標題階層與表格）
- `content_hash`

> 為什麼要多做這兩份：現有 `full_text` 是 15 種來源攤平合併（`" ".join([...])`），拿來給 LLM 判斷「該欄位在原文哪裡」或切 chunk 時，段落順序與表格欄位對應都已經丟失。`structured_markdown` 只是同一份已展開 DOM 的另一種呈現方式，擷取成本幾乎不變（DOM 已經展開好了，只是多跑一次轉換），但能大幅提升後面抽取與驗證的準確度。

### Phase 3：分類（★ 這裡是你要求改的地方）

**Node 5 — `content_classification`（LLM 節點，取代 `score_page` 的關鍵字評分）**
輸入：`full_text`（或 `structured_markdown` 節錄），輸出：
- 是否含升學相關資訊（bool）
- 細分類：**Fable 5 讀完 `setting/parameter.py` 裡 `PAGE_HINTS` 的 key 之後，先把目前有哪些類型、以及新的 LLM 分類要不要沿用/增減，整理成一份簡短清單回來跟你討論，取得你確認後才寫死進 prompt/schema，不要直接自己套用或自己新增類型。**
- 信心分數

`score_url_path`（純粹看 URL 路徑加分，跟內文關鍵字評分是兩回事）**可以保留**當一個很便宜的訊號放進 LLM prompt 裡當參考（例如「這個 URL 路徑包含 /admission/，方便你參考」），但**最終分類決定權在 LLM**，不是規則分數。這樣符合你「希望改以LLM節點進行語意評分」的需求，同時不浪費 URL 路徑這個已經很可靠的訊號。

**條件邊 `is_relevant_content`**：不相關 → 丟棄記錄；相關 → 進 Node 6
> faculty 類型頁面這次不特別處理（教授資料整體跳過，見下方），若分類為 faculty 且找不到對應 `program_code`，可直接視為不足以進入 Phase 4，丟棄記錄即可。

### Phase 4：結構化抽取與驗證（同 v2，對應實際 schema）

**Node 6 — `identify_programs`**：判斷頁面對應哪個/哪些 `program_code`。

**Node 7 — `structured_extraction`**：依 `structured_markdown` 抽取 `programs`/`program_deadlines`/`program_scholarships`/`program_app_materials` 欄位，每個欄位附 `source_excerpt`。

> ~~Node 6b（教授資料抽取）與 professor_papers/SerpAPI 節點~~ 已依你的指示整個拿掉，這次 pipeline 不處理 `professors`/`professor_papers` 這兩張表，即使爬到 faculty 頁面也直接跳過不進 Phase 4。這兩張表保留在 DB schema 裡，之後有需要再另外開一輪處理。

**Node 8 — `hallucination_validation`**：逐欄位比對原文，數字/日期/金額類特別小心。

**條件邊 `extraction_quality_check`**：信心過低且未達重試上限 → 回 Node 7；超過上限 → 寫入 `review_queue`（見下方說明）；通過 → 進 Phase 5

### Phase 5：自主判斷資料是否足夠

**Node 9 — `sufficiency_evaluator`**：同 v2。**這個節點能不能真的動態補爬，取決於 Node 2 選 A 還是選 B**（見上方）。

### Phase 6：標籤、chunk、embedding（可關）、寫入 DB

**Node 10 — `tagging`**、**Node 11 — `chunking`**（用 `structured_markdown` 切）、**Node 12 — `embedding`**（`ENABLE_EMBEDDING` 開關，同 v2，程式碼片段不變）— 內容同 v2，不重複貼。

**Node 13 — `db_writer`**：見下方「三、資料庫寫入策略」的具體決定。

**Node 14 — `finalize_school`**：比照 `run_crawler.py` 現有的 SKIP_RECENT / 摘要表機制，新版建議：在 Node 1 就檢查該校資料是否「近期已更新」（沿用 `school_data_is_recent` 的邏輯，改成查 DB 裡 `web_pages`/`programs` 的 `last_extracted_at` 而不是查檔案 mtime），決定要不要整校跳過。

---

## 三、資料庫寫入策略（原本的第 3、4 點，直接給你預設方案）

### 3-1. `program_deadlines` / `program_scholarships` / `program_app_materials` 沒有 UNIQUE 約束的問題

**白話解釋**：假設某大學的申請截止日頁面被爬了兩次（例如你重跑一次 pipeline 想更新資料），現在的 schema 沒有東西可以擋住「同一筆截止日被插入兩次」，`program_deadlines` 表就會出現兩筆一模一樣（或內容衝突）的資料。之後你查「這個 program 的截止日」就會查到重複的結果。

**預設方案（直接採用，不用你選）**：寫入前先查詢是否已有相同 `(program_id, deadline_type, semester)` 的記錄（`program_scholarships` 用 `(program_id, name)`，`program_app_materials` 用 `(program_id, material_type)`）：
- 有 → `UPDATE`（更新 amount/note 等內容，並更新一個 `updated_at` — **這三張表目前沒有 `updated_at` 欄位，建議加上**，方便追蹤是不是被更新過）
- 沒有 → `INSERT`

不用改 UNIQUE 約束、不用加 `ON CONFLICT`，用應用層邏輯處理就好，改動最小。

### 3-2. `review_queue` 表的用途

**白話解釋**：Node 8（驗證）如果發現 LLM 抽取的資料跟原文對不起來，或信心分數太低，現在的設計是「重試 2-3 次還是不行就標記」。標記完之後這筆資料要放哪裡？如果直接丟掉，你會不知道有哪些頁面資料其實有問題但沒進 DB；如果硬塞進 `programs` 表，又會污染正式資料（例如把一個不確定的學費數字寫進去，之後被當真的用）。

`review_queue` 就是一個「暫存區」，專門放這些不確定的資料，讓你之後可以人工看一眼、決定要不要手動改正確再放進正式表。

**預設方案**：先加這張表，成本很低（就是多一張表），有資料進來才會用到，之後真的用不到也不影響其他功能：
```sql
CREATE TABLE review_queue (
    id           SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id      INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    program_code VARCHAR(50),
    field_name   VARCHAR(100),
    field_value  TEXT,
    reason       VARCHAR(50),      -- low_confidence / hallucination_detected / extraction_failed
    source_excerpt TEXT,
    status       VARCHAR(20) DEFAULT 'pending',   -- pending / resolved / rejected
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 四、LLM Provider

你們專案裡已經有接免費 LLM API 的既有程式碼。**請 Fable 5 先在專案內搜尋**（例如找 `llm_client`、`chat_completion`、`.env` 裡的 API key 變數名稱、或任何 import 了 openai/anthropic/google-generativeai/groq 之類 SDK 的檔案），找到既有的呼叫方式後**直接沿用同一套**，不要另外重新接一個新的 provider 或新的呼叫介面。如果專案內找不到任何既有的 LLM 呼叫程式碼，才回來問我要用哪個。

---

## 五、實作與交付要求（同 v2，重申關鍵幾點）

1. `StateGraph` + 條件邊；Node 4/5 用 `Send` API 做並行 fan-out（取代原本的 `ThreadPoolExecutor` worker 包法）。
2. checkpointer 用 `PostgresSaver`，`thread_id` 對應 `school_id`。
3. `ENABLE_EMBEDDING` 開關落實在 Node 12，並提供獨立 backfill 腳本（見 v2 內容，不變）。
4. 安全上限沿用 `CONFIG` 裡既有的 `MAX_DEPTH`/`MAX_PAGES`，新增 `max_sufficiency_iterations`。
5. CLI 入口可指定單一 `school_id`，先用小範圍（`max_depth=2` 或沿用 `CONFIG` 的小值）跑通再擴大。
6. 完成後說明：`save_result.py`/`clean_json_data.py` 裡哪些邏輯被保留進新節點、找到的既有 LLM API 呼叫方式是什麼、最終定案的 `PAGE_HINTS` 分類類型清單、以及還沒解決的已知限制。

## 執行前務必跟我確認的事項

1. **`PAGE_HINTS` 分類類型清單**：讀完 `setting/parameter.py` 後，先整理出目前有哪些類型、LLM 節點打算沿用/增減哪些，回來跟使用者討論定案，再繼續往下實作 Node 5 之後的節點。這是唯一一個開工前一定要停下來確認的項目，其餘都已定案（見下方摘要）。

## 已定案事項（不用再問，除非讀程式碼後發現衝突）

- Node 2 採用**選項 B**（BFS 邏輯搬進 graph，逐層 checkpoint，支援 Node 9 動態補爬）
- **教授相關功能（`professors` 基本資料抽取 + `professor_papers`/SerpAPI）本次完全不處理**，faculty 頁面分類後直接丟棄，不進 Phase 4
- `program_deadlines`/`program_scholarships`/`program_app_materials` 採「查詢後決定 insert/update」（見第三節 3-1）
- 新增 `review_queue` 表（見第三節 3-2）
- LLM provider 沿用專案內既有的免費 API 串接（見第四節）
- `ENABLE_EMBEDDING` 開關，預設關閉，靠 `fts_vector` 全文檢索頂著
