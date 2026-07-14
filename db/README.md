# `db/` — 資料庫 schema 與載入腳本

本目錄放**建表 SQL、灌資料腳本、種子/來源 JSON**。

> `db/` = 資料庫的藍圖與種子（手動跑或給後端讀 schema）。
> `backend/scripts/db/` = 後端 runtime 存取 DB 的程式，兩者不同、不可混。

## Schema 總覽

`init_db.sql` 定義 9 張核心表（分「結構化」「內文」「品質控管」三類），另有 `migrations/` 加法式補上的 `applicant_reports`、以及 `init_experience.sql` 的 `user_experiences`。

**結構化資料（供 text-to-SQL 查詢）** —— 以 `programs` 為核心的主表-子表結構：
- `universities`：學校基本資料（school_id、name、domain）
- `programs`：每個學位項目一列（一校可有多個 program，如 CS MS / CS PhD），含 GPA、TOEFL/IELTS/Duolingo、GRE、學費、申請費、推薦信數、CV 等單值申請要求欄位
- `program_deadlines` / `program_scholarships` / `program_app_materials`：截止日 / 獎助 / 特殊申請材料（各為「一 program 可多筆」的子表，靠 `program_id` 關聯）
- `global_extractions`：尚未對應到正式 program 的全校／系級抽取結果

**內文資料（供全文檢索）**：
- `web_pages`：每個爬取頁面的清洗後全文
- `document_chunks`：全文切段後的 chunk，`fts_vector` 供全文檢索（trigger 自動生成）、`embedding` 為預留向量欄位

**品質控管**：
- `review_queue`：爬蟲抽取時驗證失敗（如幻覺）的欄位，進佇列等人工複查

## 三個資料源（三批表，刻意分開）

| 資料源 | 建表檔 | 資料來源 | 寫入方式 | 性質 |
|--------|--------|---------|---------|------|
| `programs` 家族（上述 9 表） | `init_db.sql` | schools_data.json／爬蟲 | `load_schools.py`／data_crawler | 官方申請要求，權威 |
| `applicant_reports` | `migrations/001_applicant_reports.sql` | GradCafe／一畝三分地 JSON | `load_applicant_reports.py`（批次） | 社群錄取回報，非官方、有樣本偏誤 |
| `user_experiences` | `init_experience.sql` | 前端表單 | `backend/.../experiences.py`（即時） | 使用者主動分享的第一手經驗 |

三者目標、欄位、寫入路徑都不同。

- **`applicant_reports`**：GradCafe / 一畝三分地的個別錄取/被拒案例（含 GPA、結果、背景），只用來回答「錄取者背景 / 某分數有無機會」這類問題。`fts_vector` 供全文檢索。生成答案時會標註「非官方經驗談」並禁止當成錄取門檻。
- **`user_experiences`**：查詢/寫入 SQL 直接寫在 `backend/scripts/db/experiences.py`（不另放 .sql）。

## 兩種 SQL 風格

- **`init_*.sql`** — 重建式（`init_db.sql` 會 DROP+CREATE 清空；`init_experience.sql` 冪等）。
- **`migrations/001_...sql`** — 加法式（`CREATE IF NOT EXISTS`，只增不改，**不會被 init-schema 清掉**）。

## 檔案

- `init_db.sql` — programs 家族主 schema（init-schema 重建用）
- `init_experience.sql` — 建 `user_experiences`
- `migrations/001_applicant_reports.sql` — 建 `applicant_reports` + 全文檢索 trigger
- `load_schools.py` / `load_applicant_reports.py` — 灌資料腳本
- `data/` — schools_data.json（測試種子，目前 5 校）、gradcafe_results.json、1point3.json

> 兩個社群 JSON 格式差很多（結構、欄位名都不同），`load_applicant_reports.py` 負責清洗成統一欄位。

## 注意

- 新增學校時記得同步更新 `backend/scripts/retriever/agent.py` 的 `_SCHOOL_ALIASES` 對照表，Decomposer 才能正確辨識學校縮寫/別名。
- 初始化與載入指令見專案根目錄 [README](../README.md)（推薦 `init-full` 一鍵建好三張表）。
