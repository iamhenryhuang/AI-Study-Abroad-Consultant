# `db/` — 資料庫 schema 與載入腳本

本目錄放**建表 SQL、灌資料腳本、種子/來源 JSON**。

> `db/` = 資料庫的藍圖與種子（手動跑或給後端讀 schema）。
> `backend/scripts/db/` = 後端 runtime 存取 DB 的程式，兩者不同、不可混。

## Schema 總覽

`schema_programs.sql` 定義 10 張核心表（分「結構化」「內文」「品質控管」「教授名單」四類），另有 `migrations/` 加法式補上的 `applicant_reports`、以及 `init_experience.sql` 的 `user_experiences`。

**結構化資料（供 text-to-SQL 查詢）** —— 以 `programs` 為核心的主表-子表結構：
- `universities`：學校基本資料（school_id、name、domain）
- `programs`：每個學位項目一列（一校可有多個 program，如 CS MS / CS PhD），含 GPA、TOEFL/IELTS/Duolingo、GRE、學費、申請費、推薦信數、CV 等單值申請要求欄位
- `program_deadlines` / `program_scholarships` / `program_app_materials`：截止日 / 獎助 / 特殊申請材料（各為「一 program 可多筆」的子表，靠 `program_id` 關聯）
- `global_extractions`：尚未對應到正式 program 的全校／系級抽取結果

**內文資料（供全文檢索）**：
- `web_pages`：每個爬取頁面的清洗後全文
- `document_chunks`：全文切段後的 chunk，`fts_vector` 供全文檢索（trigger 自動生成）、`embedding` 為預留向量欄位

**教授名單（供 professor_list_query 查詢）**：
- `professors`：教授姓名、職稱（title，可為 NULL）、研究領域（research_areas，陣列）、官方頁面連結，靠 `university_id` 關聯 `universities`。手動整理的種子資料，非 data_crawler 產出（data_crawler 目前不處理 faculty 頁）。

**品質控管**：
- `review_queue`：爬蟲抽取時驗證失敗（如幻覺）的欄位，進佇列等人工複查

## 四個資料源（四批表，刻意分開）

| 資料源 | 建表檔 | 資料來源 | 寫入方式 | 性質 |
|--------|--------|---------|---------|------|
| `programs` 家族（上述 9 表，不含 `professors`） | `schema_programs.sql` | schools_data.json／爬蟲 | `load_schools.py`／data_crawler | 官方申請要求，權威 |
| `professors` | `schema_programs.sql` | 手動整理（WebFetch 官網逐一驗證） | `load_professors.py` | 教授名單種子資料，非爬蟲產出 |
| `applicant_reports` | `migrations/001_applicant_reports.sql` | GradCafe／一畝三分地 JSON | `load_applicant_reports.py`（批次） | 社群錄取回報，非官方、有樣本偏誤 |
| `user_experiences` | `init_experience.sql` | 前端表單 | `backend/.../experiences.py`（即時） | 使用者主動分享的第一手經驗 |

四者目標、欄位、寫入路徑都不同。

- **`professors`**：目前收錄 Georgia Tech / Purdue / Stanford，每校約 30 位。`title` 欄位若原始資料未查證到職稱則為 NULL，寫入與生成答案時皆不得自行推測填入，避免幻覺。`UNIQUE (university_id, name)` 保證同校同名教授不重複，重跑 `load_professors.py` 是 upsert（更新既有列，不會產生孤兒資料）。
- **`applicant_reports`**：GradCafe / 一畝三分地的個別錄取/被拒案例（含 GPA、結果、背景），只用來回答「錄取者背景 / 某分數有無機會」這類問題。`fts_vector` 供全文檢索。生成答案時會標註「非官方經驗談」並禁止當成錄取門檻。
- **`user_experiences`**：查詢/寫入 SQL 直接寫在 `backend/scripts/db/experiences.py`（不另放 .sql）。

> 載入後實際筆數：gradcafe 995、1point3 591。1point3 的來源 JSON 有 596 筆，
> 其中 5 筆是重複貼文（同一 thread URL），已由 `UNIQUE (source, source_url)` 去重——
> 差額是正常的，不是資料遺失。

## 建表：改欄位只改 `schema_programs.sql`

`programs` 家族的表定義**只有一份**，在 `schema_programs.sql`（只建表、不刪表、全冪等）。
兩條路徑都引用它，因此不會漂移：

| 路徑 | 做什麼 |
|------|--------|
| `run.py init-schema` | 先跑 `init_db.sql`（只剩 DROP）再跑 `schema_programs.sql` → 重建、清空 |
| 爬蟲 `ensure_migrations()` | 先跑 `schema_programs.sql` 再跑 `MIGRATION_SQL`（只剩 ALTER 升級） → 加法式、不動資料 |

另兩張表各自獨立：`init_experience.sql`（冪等）、`migrations/001_...sql`（加法式，
**不會被 init-schema 清掉**）。

## `updated_at` 由 DB 維護

四張子表（`program_deadlines` / `program_scholarships` / `program_app_materials` /
`global_extractions`）的 `updated_at` 由 `set_updated_at()` trigger 自動補值。
**寫入端不需要、也不應該自己帶這個欄位**——過去靠寫入端自行帶值，`load_schools.py`
的 INSERT 漏帶，導致資料全是 NULL。

## 檔案

- `schema_programs.sql` — **programs 家族的表定義（唯一權威來源）**，與 data_crawler 共用
- `init_db.sql` — 只有 DROP 區塊（init-schema 重建時先清空，再跑上面那支）
- `init_experience.sql` — 建 `user_experiences`
- `migrations/001_applicant_reports.sql` — 建 `applicant_reports` + 全文檢索 trigger
- `load_schools.py` / `load_professors.py` / `load_applicant_reports.py` — 灌資料腳本
- `data/` — schools_data.json（測試種子，目前 5 校）、professors.json（教授名單種子，目前 3 校）、gradcafe_results.json、1point3.json

> 兩個社群 JSON 格式差很多（結構、欄位名都不同），`load_applicant_reports.py` 負責清洗成統一欄位。

## 注意

- 新增學校時記得同步更新 `backend/scripts/retriever/agent/state.py` 的 `_SCHOOL_ALIASES` 對照表，Decomposer 才能正確辨識學校縮寫/別名。
- 初始化與載入指令見專案根目錄 [README](../README.md)（推薦 `init-full` 一鍵建好三張表）。
- `applicant_reports.school_id` **刻意沒有 FK** 指向 `universities`：社群資料涵蓋的學校
  遠多於 `universities` 現有的 5 校（目前有 21 個 school_id 對不到，如 nyu 135 筆、
  uiuc 128 筆），設 FK 會讓這些資料無法載入。代價是查詢時**不能用 `JOIN universities`**
  撈這張表，否則會漏掉一千多筆；請直接以 `applicant_reports.school_id` 比對。
