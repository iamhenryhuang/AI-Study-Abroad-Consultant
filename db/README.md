# `db/`：Schema 與資料載入

`db/` 保存 PostgreSQL schema、migration、來源 JSON 與批次載入腳本。後端 runtime 的連線與操作程式則在 `backend/scripts/db/`。

## 核心資料表

| 類型 | 資料表 | 用途 |
|---|---|---|
| 學校與申請條件 | `universities`、`programs` | 學校、學程及 TOEFL、IELTS、GRE、GPA、費用、推薦信、SOP 等欄位 |
| 一對多申請資料 | `program_deadlines`、`program_scholarships`、`program_app_materials` | 截止日、獎助與特殊材料 |
| 官方原文 | `web_pages`、`document_chunks` | 清洗後頁面與 RAG chunks；`fts_vector` 自動建立，`embedding` 可後補 |
| 補充證據 | `program_evidence`、`global_extractions` | 不適合強制正規化，但仍有來源依據的內容 |
| 品質控管 | `review_queue` | 驗證未通過、等待人工檢查的欄位 |
| 教授 | `professors` | 姓名、職稱、研究領域與官方連結 |
| 社群經驗 | `applicant_reports`、`user_experiences` | GradCafe／一畝三分地及使用者分享 |

`schema_programs.sql` 是 crawler 相關資料表的唯一 schema 來源。`init_db.sql` 負責 DROP，`data_crawler/db.py` 的 migration 只做加法式升級。

## 寫入來源

| 資料來源 | 寫入方式 | 主要資料表 |
|---|---|---|
| 三校官方網站 | `python -m data_crawler.main ...` | `universities`、`programs` 家族、`web_pages`、`document_chunks` |
| 教授種子 | `python backend/scripts/run.py load-professors` | `professors` |
| GradCafe／一畝三分地 | `python db/load_applicant_reports.py --migrate` | `applicant_reports` |
| 使用者表單 | FastAPI experiences endpoint | `user_experiences` |

教授資料以 `universities.school_id` 關聯，因此必須先爬完學校再執行 `load-professors`。同校同名教授以 `UNIQUE (university_id, name)` upsert。

`applicant_reports.school_id` 不設 FK，因為社群案例涵蓋的學校比正式 `universities` 多；查詢時直接使用它的 `school_id`，不要強制 JOIN `universities`。

## 初始化順序

```bash
# 只在最前面執行一次
python backend/scripts/run.py init-full

python -m data_crawler.main --school-id gatech   --max-depth 1 --max-pages 10
python -m data_crawler.main --school-id purdue   --max-depth 1 --max-pages 10
python -m data_crawler.main --school-id stanford --max-depth 1 --max-pages 10

python backend/scripts/run.py load-professors
python backend/scripts/run.py verify-db
```

重要行為：

- `init-full`：重建 crawler schema，並載入社群申請回報。
- `init-schema`：DROP 並重建 crawler 資料表。
- `load-schools`／`init-all`：載入舊測試種子，會先重建 crawler schema；正式三校流程不使用。
- `init-experience`：冪等建立 `user_experiences`，不清除既有資料。
- `clear-crawler-data --yes`：清除 crawler DB 資料與 checkpoints。

三校爬完後不要再執行 `init-full`、`init-schema`、`load-schools` 或 `init-all`，否則學校、頁面、chunks 與教授關聯會消失。

## Crawler 寫入內容

`data_crawler` 最後的 `db_writer` 會：

1. 依 `school_id` 建立或取得 `universities`。
2. Upsert `INTERNATIONAL_CS_MASTERS` 及可驗證的結構化欄位。
3. 寫入 deadlines、scholarships、materials 與 evidence。
4. 保存官方頁面全文到 `web_pages`。
5. 以頁面為單位更新 `document_chunks`。
6. 將驗證失敗的值寫入 `review_queue`，不當成可信結構化欄位。

未加 `--dry-run` 且 `DATABASE_URL` 存在時會自動寫 DB；不需要再從 `data_crawler/output` 匯入。Output 只供稽核，已由 Git 忽略。

## 主要檔案

- `schema_programs.sql`：crawler schema 唯一來源。
- `init_db.sql`：重建前的 DROP。
- `init_experience.sql`：使用者經驗表。
- `migrations/001_applicant_reports.sql`：社群申請回報表與 FTS。
- `load_applicant_reports.py`：清洗並 upsert 社群資料。
- `load_professors.py`：upsert 三校教授名單。
- `data/professors.json`：教授種子。
- `data/schools_data.json`：舊測試資料，正式 crawler 流程不使用。

新增學校時，除了 crawler root URL，也要更新 `backend/scripts/retriever/agent/state.py` 的學校別名。
