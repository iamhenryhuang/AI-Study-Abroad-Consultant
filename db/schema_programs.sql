-- ════════════════════════════════════════════════════════════════
-- programs 家族 —— 共用表定義（唯一權威來源）
--
-- 這個檔案「只建表、不刪表」，全部寫成冪等（IF NOT EXISTS），因此兩邊都能引用：
--   db/init_db.sql        先 DROP 既有表再引用本檔 → 重建式，清空重來
--   data_crawler/db.py    直接引用本檔               → 加法式，不動既有資料
--
-- 過去這些定義在上述兩處各寫一份、靠註解提醒同步，容易漂移。
-- 改欄位請只改這裡，兩邊自動一致。
-- ════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;

-- ════════════════════════════════════════════════════════════════
-- 1. universities  —— 學校基本資料
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS universities (
    id          SERIAL PRIMARY KEY,
    school_id   VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    domain      VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ════════════════════════════════════════════════════════════════
-- 2. programs  —— 招生目標
-- 目前 data_crawler 每校只寫一筆國際 CS 碩士目標；UNIQUE 設計仍保留未來擴充能力。
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS programs (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,

    -- 識別資訊
    degree_type   VARCHAR(20),                  -- MS / PhD / MEng / MPS / MBA
    program_code  TEXT NOT NULL,                -- 目前固定為 INTERNATIONAL_CS_MASTERS
    program_name  VARCHAR(200),                 -- 目前為 International Computer Science Master's
    department    VARCHAR(100),                 -- "Computer Science"

    -- 語言要求
    toefl_min          INTEGER,
    toefl_ibt_min      INTEGER,
    toefl_ibt_new_scale_min NUMERIC(2,1),       -- 2026-01-21 起 TOEFL iBT 1–6 新制 overall
    toefl_section_requirements TEXT,             -- TOEFL 各分項門檻，合併為字串
    ielts_min          NUMERIC(3,1),
    duolingo_min       INTEGER,
    language_waiver    TEXT,
    english_test_notes TEXT,                     -- 英文檢定效期、形式、例外等特殊規定

    -- GRE 要求
    gre_required       VARCHAR(20),             -- required / optional / not_accepted
    gre_quant_min      INTEGER,
    gre_verbal_min     INTEGER,
    gre_awa_min        NUMERIC(2,1),

    -- GPA 要求
    gpa_min            NUMERIC(3,2),
    gpa_scale          VARCHAR(10),
    gpa_note           TEXT,

    -- 申請材料（單值欄位放這，多值拆 program_app_materials）
    transcript_copies        INTEGER,
    transcript_format        TEXT,
    rec_letter_count         INTEGER,
    sop_word_limit           INTEGER,
    sop_prompt               TEXT,
    cv_required              BOOLEAN,
    writing_sample_required  BOOLEAN,
    application_fee_usd      INTEGER,
    fee_waiver_available     BOOLEAN,
    fee_waiver_criteria      TEXT,

    -- 學費
    tuition_per_year_usd  INTEGER,
    tuition_note          TEXT,

    -- 申請方式
    application_url       TEXT,
    application_system    TEXT,

    -- 抽取 metadata
    source_urls           TEXT[],
    extraction_confidence NUMERIC(3,2),
    last_extracted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (university_id, program_code)
);

CREATE INDEX IF NOT EXISTS idx_programs_university ON programs(university_id);
CREATE INDEX IF NOT EXISTS idx_programs_degree     ON programs(degree_type);


-- ────────────────────────────────────────────────────────────────
-- updated_at 自動維護
--   子表的 updated_at 過去靠各寫入端自己帶 CURRENT_TIMESTAMP，只要有一個
--   寫入路徑漏帶就會留下 NULL（load_schools.py 的 INSERT 即如此）。
--   改由 trigger 統一維護，任何寫入端都不必再管這個欄位。
-- ────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;


-- ════════════════════════════════════════════════════════════════
-- 3. program_deadlines  —— 截止日期（一個 program 可有多筆）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS program_deadlines (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    deadline_type  VARCHAR(30),                 -- early / regular / international / rolling
    deadline_date  DATE,                        -- 舊版相容：等同 application_close_date
    application_open_date   DATE,
    application_close_date  DATE,
    decision_release_date   DATE,
    semester       VARCHAR(20),                 -- fall_2026 / spring_2027
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deadlines_program ON program_deadlines(program_id);

DROP TRIGGER IF EXISTS program_deadlines_set_updated_at ON program_deadlines;
CREATE TRIGGER program_deadlines_set_updated_at
    BEFORE INSERT OR UPDATE ON program_deadlines
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ════════════════════════════════════════════════════════════════
-- 4. program_scholarships  —— 獎學金（一個 program 可有多筆）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS program_scholarships (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    name           VARCHAR(200),
    amount_usd     INTEGER,
    coverage       VARCHAR(100),                -- full_tuition / partial / stipend_only
    eligibility    TEXT,
    auto_consider  BOOLEAN,
    source_url     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scholarships_program ON program_scholarships(program_id);

DROP TRIGGER IF EXISTS program_scholarships_set_updated_at ON program_scholarships;
CREATE TRIGGER program_scholarships_set_updated_at
    BEFORE INSERT OR UPDATE ON program_scholarships
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ════════════════════════════════════════════════════════════════
-- 5. program_app_materials  —— 申請材料的多值補充項
--    （programs 表已含常見單值欄位；此表存「不只一份」的特殊材料）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS program_app_materials (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    material_type  TEXT,                        -- additional_essay / portfolio / video / writing_sample / other
    requirement    TEXT,
    word_limit     INTEGER,
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_materials_program ON program_app_materials(program_id);

DROP TRIGGER IF EXISTS program_app_materials_set_updated_at ON program_app_materials;
CREATE TRIGGER program_app_materials_set_updated_at
    BEFORE INSERT OR UPDATE ON program_app_materials
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 無法安全正規化為單一數字／日期的招生證據。保留上下文供後續 RAG 判斷，
-- 不因結構化驗證失敗而遺失 GPA、英文門檻、deadline 等重要資訊。
CREATE TABLE IF NOT EXISTS program_evidence (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    category       VARCHAR(30) NOT NULL,
    field_name     TEXT NOT NULL DEFAULT 'general',
    evidence_kind  VARCHAR(30) NOT NULL DEFAULT 'context_note',
    evidence_text  TEXT NOT NULL,
    source_excerpt TEXT,
    source_url     TEXT NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (program_id, category, field_name, source_url)
);

CREATE INDEX IF NOT EXISTS idx_program_evidence_program
    ON program_evidence(program_id);
CREATE INDEX IF NOT EXISTS idx_program_evidence_category
    ON program_evidence(category);

DROP TRIGGER IF EXISTS program_evidence_set_updated_at ON program_evidence;
CREATE TRIGGER program_evidence_set_updated_at
    BEFORE INSERT OR UPDATE ON program_evidence
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ════════════════════════════════════════════════════════════════
-- 6. web_pages  —— 原始爬蟲頁面
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS web_pages (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    program_id    INTEGER REFERENCES programs(id) ON DELETE SET NULL,
    url           TEXT UNIQUE NOT NULL,
    passed_types  JSONB NOT NULL,
    raw_text      TEXT NOT NULL,
    char_count    INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_web_pages_university   ON web_pages(university_id);
CREATE INDEX IF NOT EXISTS idx_web_pages_program      ON web_pages(program_id);
CREATE INDEX IF NOT EXISTS idx_web_pages_passed_types ON web_pages USING GIN (passed_types);

-- 全校／CS 系級抽取結果：即使尚未識別出正式 program 也保留結構化資料
CREATE TABLE IF NOT EXISTS global_extractions (
    id             SERIAL PRIMARY KEY,
    university_id  INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    page_id         INTEGER NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
    scope           VARCHAR(30) NOT NULL,
    program_code    TEXT NOT NULL,
    extraction      JSONB NOT NULL,
    confidence      NUMERIC(3,2),
    source_url      TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (page_id, program_code)
);
CREATE INDEX IF NOT EXISTS idx_global_extractions_university ON global_extractions(university_id);

DROP TRIGGER IF EXISTS global_extractions_set_updated_at ON global_extractions;
CREATE TRIGGER global_extractions_set_updated_at
    BEFORE INSERT OR UPDATE ON global_extractions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ════════════════════════════════════════════════════════════════
-- 7. document_chunks  —— chunk 全文 + 選配向量
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS document_chunks (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id       INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    program_id    INTEGER REFERENCES programs(id) ON DELETE SET NULL,
    school_id     VARCHAR(100) NOT NULL,
    source_url    TEXT NOT NULL,
    passed_types  JSONB NOT NULL,
    chunk_index   INTEGER NOT NULL,
    chunk_text    TEXT NOT NULL,
    embedding     vector(1024),
    fts_vector    tsvector,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (page_id, chunk_index)
);

-- HNSW index：m=32 增強圖連通性，ef_construction=200 提升建構品質
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 200);

-- FTS index：加速關鍵字搜尋
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING GIN (fts_vector);

-- GIN index：passed_types 多類型過濾
CREATE INDEX IF NOT EXISTS idx_chunks_passed_types ON document_chunks USING GIN (passed_types);

-- 單欄過濾索引
CREATE INDEX IF NOT EXISTS idx_chunks_school  ON document_chunks(school_id);
CREATE INDEX IF NOT EXISTS idx_chunks_pageid  ON document_chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_chunks_program ON document_chunks(program_id);

-- 自動更新 fts_vector 的觸發器（simple：語言無關，避免中文被 stop words / stemming 過濾）
DROP TRIGGER IF EXISTS tsvectorupdate ON document_chunks;
CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE
ON document_chunks FOR EACH ROW EXECUTE FUNCTION
tsvector_update_trigger(fts_vector, 'pg_catalog.simple', chunk_text);


-- ════════════════════════════════════════════════════════════════
-- 8. review_queue  —— data_crawler 驗證失敗欄位的人工複查佇列
-- ════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS review_queue (
    id             SERIAL PRIMARY KEY,
    university_id  INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id        INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    program_code   TEXT,
    field_name     VARCHAR(100),
    field_value    TEXT,
    reason         VARCHAR(50),                 -- hallucination_detected 等
    source_excerpt TEXT,
    status         VARCHAR(20) DEFAULT 'pending',  -- pending / resolved / rejected
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_queue_university ON review_queue(university_id);
CREATE INDEX IF NOT EXISTS idx_review_queue_status     ON review_queue(status);
