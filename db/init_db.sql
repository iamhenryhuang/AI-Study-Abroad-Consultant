-- ════════════════════════════════════════════════════════════════
-- Study Abroad RAG — schema 以 data_crawler 的抽取/寫入能力為準
-- 真實資料來源：data_crawler（LangGraph 爬蟲）寫入
-- db/schools_data.json 僅為對應此 schema 的測試假資料
-- ════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;

-- 先清除舊表（注意刪除順序：被引用的最後刪）
DROP TABLE IF EXISTS review_queue            CASCADE;
DROP TABLE IF EXISTS document_chunks         CASCADE;
DROP TABLE IF EXISTS web_pages               CASCADE;
DROP TABLE IF EXISTS program_app_materials   CASCADE;
DROP TABLE IF EXISTS program_scholarships    CASCADE;
DROP TABLE IF EXISTS program_deadlines       CASCADE;
DROP TABLE IF EXISTS programs                CASCADE;
DROP TABLE IF EXISTS universities            CASCADE;


-- ════════════════════════════════════════════════════════════════
-- 1. universities  —— 學校基本資料
-- ════════════════════════════════════════════════════════════════
CREATE TABLE universities (
    id          SERIAL PRIMARY KEY,
    school_id   VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    domain      VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ════════════════════════════════════════════════════════════════
-- 2. programs  —— 學位 program（1 個 university 可有多個 program）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE programs (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,

    -- 識別資訊
    degree_type   VARCHAR(20),                  -- MS / PhD / MEng / MPS / MBA
    program_code  VARCHAR(50) NOT NULL,         -- "CS MS" / "CS PhD"
    program_name  VARCHAR(200),                 -- "Master of Science in Computer Science"
    department    VARCHAR(100),                 -- "Computer Science"

    -- 語言要求
    toefl_min          INTEGER,
    toefl_ibt_min      INTEGER,
    ielts_min          NUMERIC(3,1),
    duolingo_min       INTEGER,
    language_waiver    TEXT,

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
    transcript_format        VARCHAR(50),
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
    application_system    VARCHAR(50),

    -- 抽取 metadata
    source_urls           TEXT[],
    extraction_confidence NUMERIC(3,2),
    last_extracted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (university_id, program_code)
);

CREATE INDEX idx_programs_university ON programs(university_id);
CREATE INDEX idx_programs_degree     ON programs(degree_type);


-- ════════════════════════════════════════════════════════════════
-- 3. program_deadlines  —— 截止日期（一個 program 可有多筆）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE program_deadlines (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    deadline_type  VARCHAR(30),                 -- early / regular / international / rolling
    deadline_date  DATE,
    semester       VARCHAR(20),                 -- fall_2026 / spring_2027
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP
);

CREATE INDEX idx_deadlines_program ON program_deadlines(program_id);


-- ════════════════════════════════════════════════════════════════
-- 4. program_scholarships  —— 獎學金（一個 program 可有多筆）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE program_scholarships (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    name           VARCHAR(200),
    amount_usd     INTEGER,
    coverage       VARCHAR(100),                -- full_tuition / partial / stipend_only
    eligibility    TEXT,
    auto_consider  BOOLEAN,
    source_url     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP
);

CREATE INDEX idx_scholarships_program ON program_scholarships(program_id);


-- ════════════════════════════════════════════════════════════════
-- 5. program_app_materials  —— 申請材料的多值補充項
--    （programs 表已含常見單值欄位；此表存「不只一份」的特殊材料）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE program_app_materials (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    material_type  VARCHAR(50),                 -- additional_essay / portfolio / video / writing_sample / other
    requirement    TEXT,
    word_limit     INTEGER,
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP
);

CREATE INDEX idx_materials_program ON program_app_materials(program_id);


-- ════════════════════════════════════════════════════════════════
-- 6. web_pages  —— 原始爬蟲頁面
-- ════════════════════════════════════════════════════════════════
CREATE TABLE web_pages (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    program_id    INTEGER REFERENCES programs(id) ON DELETE SET NULL,
    url           TEXT UNIQUE NOT NULL,
    passed_types  JSONB NOT NULL,
    raw_text      TEXT NOT NULL,
    char_count    INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_web_pages_university   ON web_pages(university_id);
CREATE INDEX idx_web_pages_program      ON web_pages(program_id);
CREATE INDEX idx_web_pages_passed_types ON web_pages USING GIN (passed_types);


-- ════════════════════════════════════════════════════════════════
-- 7. document_chunks  —— chunk 全文 + 選配向量
-- ════════════════════════════════════════════════════════════════
CREATE TABLE document_chunks (
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
CREATE INDEX idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 200);

-- FTS index：加速關鍵字搜尋
CREATE INDEX idx_chunks_fts ON document_chunks USING GIN (fts_vector);

-- GIN index：passed_types 多類型過濾
CREATE INDEX idx_chunks_passed_types ON document_chunks USING GIN (passed_types);

-- 單欄過濾索引
CREATE INDEX idx_chunks_school  ON document_chunks(school_id);
CREATE INDEX idx_chunks_pageid  ON document_chunks(page_id);
CREATE INDEX idx_chunks_program ON document_chunks(program_id);

-- 自動更新 fts_vector 的觸發器（simple：語言無關，避免中文被 stop words / stemming 過濾）
CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE
ON document_chunks FOR EACH ROW EXECUTE FUNCTION
tsvector_update_trigger(fts_vector, 'pg_catalog.simple', chunk_text);


-- ════════════════════════════════════════════════════════════════
-- 8. review_queue  —— data_crawler 驗證失敗欄位的人工複查佇列
-- ════════════════════════════════════════════════════════════════
CREATE TABLE review_queue (
    id             SERIAL PRIMARY KEY,
    university_id  INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id        INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    program_code   VARCHAR(50),
    field_name     VARCHAR(100),
    field_value    TEXT,
    reason         VARCHAR(50),                 -- hallucination_detected 等
    source_excerpt TEXT,
    status         VARCHAR(20) DEFAULT 'pending',  -- pending / resolved / rejected
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_review_queue_university ON review_queue(university_id);
CREATE INDEX idx_review_queue_status     ON review_queue(status);
