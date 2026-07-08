# data_crawler — LangGraph 資料擷取與清洗 pipeline（v4）

把 `crawler/` 的四步批次腳本（url_crawler → score → save_result → clean_json_data）
改成「state 接 state」的 LangGraph pipeline，並把關鍵字評分換成 LLM 語意判斷。
**本資料夾完全獨立，不修改 `crawler/` / `backend/` / `db/` 的任何檔案**；
設定（`CONFIG`、黑名單、`SCHOOLS`、清洗關鍵字）透過 `settings_bridge.py` 直接讀取原始檔，單一來源不重複維護。

## 使用方式

```bash
pip install -r data_crawler/requirements.txt   # 專案已有 playwright/psycopg 等，只補缺的

# 先小範圍跑通再擴大（在專案根目錄執行）
python -m data_crawler.main --school-id purdue --max-depth 1 --max-pages 10 --dry-run

# 正式跑（寫 DB + PostgresSaver checkpoint，thread_id = school_id）
python -m data_crawler.main --school-id ucla --max-depth 2

# 中斷後續跑（PostgresSaver 從上次 checkpoint 繼續，BFS 每爬完一層存一次）
python -m data_crawler.main --school-id ucla --resume

# 其他開關
#   --skip-recent-hours 24   SKIP_RECENT：DB 內 last_extracted_at 24h 內就整校跳過
#   --enable-embedding       Node 12 embedding（預設關，靠 fts_vector 全文檢索）
#   --max-sufficiency-iterations 2   Node 9 動態補爬輪數上限

# embedding 事後補齊（獨立 backfill）
python -m data_crawler.backfill_embeddings [--school-id ucla]
```

`--dry-run`：不碰 DB，結果輸出到 `data_crawler/output/{school_id}_result.json`，checkpointer 用 MemorySaver。

## Graph 結構

```
主圖（SchoolState，thread_id = school_id，PostgresSaver）
  Node 1  init_crawl            查/建 universities.id、SKIP_RECENT（查 DB last_extracted_at）
  Node 2  discover_urls ⟲       BFS 一次一層（選項 B），每層結束即 checkpoint；
                                visited_urls 跟著 state 存，續跑不重爬
     └─ should_continue_bfs：還有佇列且未達 MAX_DEPTH/MAX_PAGES → 繼續下一層
  Node 4  scrape_page           Send fan-out（每頁一分支，semaphore 限流 NUM_WORKERS）
          collect_scraped       content-hash 去重（save_result.py 邏輯）＋去錯誤頁
  Node 5-8 process_page         Send fan-out 到頁面子圖（見下）
  Node 9  sufficiency_evaluator LLM 評估欄位覆蓋率；不足時挑同校網域的外部連結當種子
     └─ seed_more_urls          種子塞回同一個 url_queue → 回 Node 2 繼續爬
  Node 10 tagging（規則式：分類類型 + program codes）
  Node 11 chunking（structured_markdown 切；清洗沿用 clean_json_data 關鍵字）
  Node 12 embedding（ENABLE_EMBEDDING 開關，預設關；bge-m3 對齊 backend embedder）
  Node 13 db_writer
  Node 14 finalize_school

頁面子圖（ProcessState）
  Node 5  content_classification  LLM 多標籤分類（取代 score_page 關鍵字評分）；
                                  score_url_path 保留為 prompt 參考訊號，決定權在 LLM
     └─ 不相關 / faculty / 低信心 → discard_page（丟棄記錄）
  Node 6  identify_programs       對應 program_code（全校通用頁套用到既有 program）
  Node 7  structured_extraction   依 structured_markdown 抽取欄位，每欄附 source_excerpt
  Node 8  hallucination_validation 程式化逐欄位比對：excerpt 必須存在原文、
                                  數字/日期必須出現在 excerpt 內
     └─ 有問題且重試 ≤2 → 回 Node 7（帶驗證回饋）；超限 → 問題欄位進 review_queue
```

Node 3（爬取前 URL 相關性 LLM 過濾）依 v4 預設不做；`extract_links` 已補抓 anchor text
（`keep_anchors`），之後要做 Node 3 時資料已就緒。

## 分類清單（已與使用者定案：6+2、多標籤）

| 類型 | 說明 | 去向 |
|---|---|---|
| admissions | 申請要求/流程/資格（原 PAGE_HINTS.admissions） | Phase 4 抽取 |
| deadlines | 截止日期/時程（自 admissions 拆出，對應 program_deadlines） | Phase 4 抽取 |
| program | 學程/課程/學位要求（原 PAGE_HINTS.program） | Phase 4 抽取 |
| tuition | 學費/成本（原 PAGE_HINTS.tuition 拆出） | Phase 4 抽取 |
| scholarship | 獎學金/financial aid（自 tuition 拆出，對應 program_scholarships） | Phase 4 抽取 |
| faq | 常見問答（原 PAGE_HINTS.faq） | Phase 4 抽取 |
| faculty | 教授/人員頁（原 MINUS_KEYWORDS 的扣分對象） | 分類後丟棄（本次不處理教授） |
| other | 無升學相關資訊 | 分類後丟棄 |

## 舊 pipeline 邏輯的保留情況

| 來源 | 邏輯 | 去向 |
|---|---|---|
| url_crawler.py | normalize_url / is_same_root / filter_url（黑名單）/ BFS 去重 / 深度頁數上限 / delay / block_resources / 多 worker | `url_tools.py`、`browser.py`（原樣搬移，Node 2 改逐層回傳） |
| score.py | extract_page_content_with_js（15 種 DOM 來源 + 展開） | `browser.py` 完整保留，另加 structured_markdown（trafilatura）/ structured_tables / content_hash / PDF 分支（pymupdf） |
| score.py | score_page 關鍵字評分 | **移除**，改為 Node 5 LLM 分類；score_url_path 保留為 prompt 參考訊號 |
| score.py | deduplicate_text（n-gram 去重） | `text_clean.py` 原樣保留 |
| save_result.py | 只留通過門檻頁面 → 改為 LLM `is_relevant` + 信心門檻；content-hash 去重 → `collect_scraped` 原樣保留；error 頁過濾 → 保留 | `nodes_school.py` |
| clean_json_data.py | KEYWORDS_TO_REMOVE / remove_keywords | 沿用原清單（bridge 直接 import），在 chunking 與寫入 web_pages.raw_text 前套用 |
| run_crawler.py | SKIP_RECENT | Node 1 改查 DB `programs.last_extracted_at`（不查檔案 mtime） |

## LLM Provider

沿用專案既有呼叫方式：介面比照 `backend/scripts/generator/openai_client.py`
（openai SDK singleton + backend/.env）。三個 provider 可用 `LLM_PROVIDER=openai|groq|gemini`
指定，未指定時依序自動偵測（有 key 就用）：

1. `OPENAI_API_KEY` → OpenAI（`OPENAI_MODEL`，預設 gpt-4o-mini）✅ 已實測
2. `GROQ_API_KEY` → Groq 免費 API（OpenAI 相容端點；模型沿用
   `backend/scripts/retriever/analyzer.py` 的 `llama-3.3-70b-versatile`，`GROQ_MODEL` 覆寫）✅ 已實測
3. `GOOGLE_API_KEY` → Gemini 免費 API（OpenAI 相容端點；預設 `gemini-2.5-flash`，
   `GEMINI_MODEL` 覆寫。注意：此 key 的 gemini-2.0-flash 免費額度為 0）✅ 已實測

所有結構化呼叫走 JSON mode + temperature 0。
免費層限流保護：LLM 併發以 semaphore 限制（`LLM_MAX_CONCURRENCY`，預設 2），
429 時解析 API 建議秒數退避重試（最多 5 次）。

## 資料庫

- **migration 為加法式且冪等**（`db.py MIGRATION_SQL`，Node 1 自動執行）：
  只 `CREATE TABLE IF NOT EXISTS`（programs 家族 + review_queue）、
  `ADD COLUMN IF NOT EXISTS`（web_pages/document_chunks 補 program_id、三張子表補 updated_at），
  **不 DROP 不清資料**——現有 DB 是舊 schema（已有 web_pages/document_chunks 資料），直接升級可用。
- 子表寫入採 v4 3-1 的「查詢後決定 INSERT/UPDATE」：
  deadlines 用 `(program_id, deadline_type, semester)`、scholarships 用 `(program_id, name)`、
  materials 用 `(program_id, material_type)`；更新時寫 `updated_at`。
- programs 欄位「非 null 才覆蓋」，不會用空值蓋掉先前抽到的資料。
- 驗證失敗的欄位不進正式表，進 `review_queue`（reason=hallucination_detected），
  `status` 欄位供人工標記 pending/resolved/rejected。

## 已知限制

1. **教授相關功能整輪不處理**（依 v4 定案）：faculty 頁分類後直接丟棄，
   `professors`/`professor_papers` 兩張表不寫入。
2. **全校通用頁的歸屬**：Node 6 判定 school_wide 的頁面會把欄位套用到該校 DB 既有的
   program（都沒有時預設 `CS MS`）。全校通用門檻通常確實適用各 program，但仍是近似做法。
3. **重跑同一 thread 會累積 state**：LangGraph 累加型 channel 無法重置，
   `main.py` 已自動偵測——非 `--resume` 且 thread 已存在時改開 `{school_id}-{timestamp}` 新 thread。
4. **LLM 免費額度限流**：Groq 免費層 TPM=12000，大量頁面 fan-out 時仍可能觸發限流；
   已做併發上限 + 建議秒數退避（實測 6 頁 fan-out 可自動恢復），但整體速度受免費額度制約。
   Node 9 的補爬種子同樣受 `MAX_PAGES` 總頁數上限保護——頁數預算用完時種子不會再被爬。
5. **semester 正規化**：LLM 偶爾回傳頁面上的舊學期（如 fall_2023），
   pipeline 不篡改原文數據，需靠 review 或之後加規則過濾舊學期。
6. **無 robots.txt 檢查**（與舊版相同，黑名單為人工維護）。
7. **PDF**：黑名單原本擋掉 `.pdf`（`IGNORED_EXTENSIONS`），BFS 階段沿用此行為，
   所以 PDF 分支目前只在 Node 9 種子 URL 或未來調整 `filter_url(allow_pdf=True)` 時生效。
8. **checkpoint 續跑粒度**：BFS 逐層 checkpoint；scrape/process fan-out 為單一 superstep，
   中斷時該 superstep 內已完成的分支結果會保留（reducer 已寫入），未完成的分支重跑。
