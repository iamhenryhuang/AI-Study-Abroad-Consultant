-- ════════════════════════════════════════════════════════════════
-- Study Abroad RAG — schema 以 data_crawler 的抽取/寫入能力為準
-- 真實資料來源：data_crawler（LangGraph 爬蟲）寫入
-- db/data/schools_data.json 僅為對應此 schema 的測試假資料
-- ════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;

-- 先清除舊表（注意刪除順序：被引用的最後刪）
DROP TABLE IF EXISTS review_queue            CASCADE;
DROP TABLE IF EXISTS document_chunks         CASCADE;
DROP TABLE IF EXISTS web_pages               CASCADE;
DROP TABLE IF EXISTS global_extractions       CASCADE;
DROP TABLE IF EXISTS program_app_materials   CASCADE;
DROP TABLE IF EXISTS program_scholarships    CASCADE;
DROP TABLE IF EXISTS program_deadlines       CASCADE;
DROP TABLE IF EXISTS programs                CASCADE;
DROP TABLE IF EXISTS universities            CASCADE;

-- 表定義本身在 db/schema_programs.sql（唯一權威來源，與 data_crawler 共用）。
-- 本檔只負責「先清空」；建表由 run.py init-schema 接著執行該共用檔完成。
-- 若手動用 psql 跑本檔，請記得接著跑：\i db/schema_programs.sql
