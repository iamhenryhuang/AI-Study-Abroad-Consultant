CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ════════════════════════════════════════════════════════════════
-- 先清除舊表（注意刪除順序：被引用的最後刪）
-- ════════════════════════════════════════════════════════════════
DROP TABLE IF EXISTS document_chunks         CASCADE;
DROP TABLE IF EXISTS web_pages               CASCADE;
DROP TABLE IF EXISTS professor_papers        CASCADE;
DROP TABLE IF EXISTS professors              CASCADE;
DROP TABLE IF EXISTS program_app_materials   CASCADE;
DROP TABLE IF EXISTS program_scholarships    CASCADE;
DROP TABLE IF EXISTS program_deadlines       CASCADE;
DROP TABLE IF EXISTS programs                CASCADE;
DROP TABLE IF EXISTS universities            CASCADE;


-- ════════════════════════════════════════════════════════════════
-- 1. universities  —— 學校基本資料（保留原樣）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE universities (
    id          SERIAL PRIMARY KEY,
    school_id   VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    domain      VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ════════════════════════════════════════════════════════════════
-- 2. programs  —— 學位 program（核心新表，以 program 為單位）
--    1 個 university 可有多個 program（CS MS / CS PhD / ECE MS ...）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE programs (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,

    -- 識別資訊
    degree_type   VARCHAR(20),                  -- MS / PhD / MEng / MPS / MBA
    program_code  VARCHAR(50) NOT NULL,         -- "CS MS" / "CS PhD" / "ECE MS"
    program_name  VARCHAR(200),                 -- "Master of Science in Computer Science"
    department    VARCHAR(100),                 -- "Computer Science"

    -- 5. 語言要求
    toefl_min          INTEGER,
    toefl_ibt_min      INTEGER,
    ielts_min          NUMERIC(3,1),
    duolingo_min       INTEGER,
    language_waiver    TEXT,                    -- 豁免條件描述

    -- 6. GRE 要求
    gre_required       VARCHAR(20),             -- required / optional / not_accepted
    gre_quant_min      INTEGER,
    gre_verbal_min     INTEGER,
    gre_awa_min        NUMERIC(2,1),

    -- 7. GPA 要求
    gpa_min            NUMERIC(3,2),
    gpa_scale          VARCHAR(10),             -- "4.0" / "100"
    gpa_note           TEXT,

    -- 8. 申請材料（單值欄位放這，多值拆 program_app_materials）
    transcript_copies        INTEGER,
    transcript_format        VARCHAR(50),       -- electronic / paper / WES_required / mixed
    rec_letter_count         INTEGER,
    sop_word_limit           INTEGER,
    sop_prompt               TEXT,
    cv_required              BOOLEAN,
    writing_sample_required  BOOLEAN,
    application_fee_usd      INTEGER,
    fee_waiver_available     BOOLEAN,
    fee_waiver_criteria      TEXT,

    -- 9. 學費
    tuition_per_year_usd  INTEGER,
    tuition_note          TEXT,                 -- "in-state / out-of-state 差異等說明"

    -- 10. 申請方式
    application_url       TEXT,
    application_system    VARCHAR(50),          -- GradCAS / Slate / self_hosted

    -- 抽取 metadata
    source_urls           TEXT[],               -- LLM 抽取自哪些 page URL
    extraction_confidence NUMERIC(3,2),         -- 0.00 - 1.00
    last_extracted_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    extra                 JSONB,                -- 未來新增欄位的暫存區
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (university_id, program_code)
);

CREATE INDEX idx_programs_university ON programs(university_id);
CREATE INDEX idx_programs_degree     ON programs(degree_type);
CREATE INDEX idx_programs_extra      ON programs USING GIN (extra);


-- ════════════════════════════════════════════════════════════════
-- 3. program_deadlines  —— 4. 截止日期（一個 program 可有多筆）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE program_deadlines (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    deadline_type  VARCHAR(30),                 -- early / regular / international / rolling
    deadline_date  DATE,
    semester       VARCHAR(20),                 -- fall_2026 / spring_2027
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_deadlines_program ON program_deadlines(program_id);
CREATE INDEX idx_deadlines_date    ON program_deadlines(deadline_date);


-- ════════════════════════════════════════════════════════════════
-- 4. program_scholarships  —— 3 & 9. 獎學金（一個 program 可有多筆）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE program_scholarships (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    name           VARCHAR(200),
    amount_usd     INTEGER,
    coverage       VARCHAR(100),                -- full_tuition / partial / stipend_only
    eligibility    TEXT,
    auto_consider  BOOLEAN,                     -- 是否自動考慮（true）/ 需另外申請（false）
    source_url     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_scholarships_program ON program_scholarships(program_id);


-- ════════════════════════════════════════════════════════════════
-- 5. program_app_materials  —— 8. 申請材料的多值補充項
--    （programs 表已含常見單值欄位；此表存「不只一份」的特殊材料）
--    例如：portfolio、video essay、補充 essay、特殊 writing sample
-- ════════════════════════════════════════════════════════════════
CREATE TABLE program_app_materials (
    id             SERIAL PRIMARY KEY,
    program_id     INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    material_type  VARCHAR(50),                 -- additional_essay / portfolio / video / writing_sample
    requirement    TEXT,
    word_limit     INTEGER,
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_materials_program ON program_app_materials(program_id);


-- ════════════════════════════════════════════════════════════════
-- 6. professors  —— 2. 教授名單與基本資料
--    fetched_from + expires_at 配合 SerpAPI fallback 的 cache 機制
-- ════════════════════════════════════════════════════════════════
CREATE TABLE professors (
    id               SERIAL PRIMARY KEY,
    university_id    INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    program_id       INTEGER REFERENCES programs(id) ON DELETE SET NULL,   -- 主要 affiliated program

    name             VARCHAR(200) NOT NULL,
    name_normalized  VARCHAR(200),              -- e.g. "Fei-Fei Li" → "feifei li" 供模糊查詢
    title            VARCHAR(100),              -- Professor / Assistant Professor / Lecturer
    email            VARCHAR(200),
    homepage_url     TEXT,
    photo_url        TEXT,

    research_areas   TEXT[],                    -- ["computer vision", "robotics"]
    research_summary TEXT,                      -- 一段話描述

    -- Cache 機制：fetched_from='serpapi' 的記錄帶 expires_at；DB-native 則 NULL = 永久
    fetched_from     VARCHAR(50),               -- crawler / serpapi / manual
    fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at       TIMESTAMP,                 -- NULL = 不過期；否則過期重抓
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (university_id, name_normalized)
);

CREATE INDEX idx_professors_university ON professors(university_id);
CREATE INDEX idx_professors_program    ON professors(program_id);
CREATE INDEX idx_professors_name       ON professors(name_normalized);
CREATE INDEX idx_professors_areas      ON professors USING GIN (research_areas);
CREATE INDEX idx_professors_expires    ON professors(expires_at);


-- ════════════════════════════════════════════════════════════════
-- 7. professor_papers  —— 教授論文
-- ════════════════════════════════════════════════════════════════
CREATE TABLE professor_papers (
    id            SERIAL PRIMARY KEY,
    professor_id  INTEGER NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    venue         VARCHAR(100),                 -- NeurIPS 2024 / CVPR 2023 / arXiv
    year          INTEGER,
    url           TEXT,
    abstract      TEXT,
    citations     INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_papers_professor ON professor_papers(professor_id);
CREATE INDEX idx_papers_year      ON professor_papers(year);


-- ════════════════════════════════════════════════════════════════
-- 8. web_pages  —— 原始爬蟲頁面（保留原欄位 + 新增 program_id）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE web_pages (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    program_id    INTEGER REFERENCES programs(id) ON DELETE SET NULL,   -- 新增：LLM 抽取後填入
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
-- 9. document_chunks  —— RAG 向量庫（保留原欄位 + 新增 program_id）
-- ════════════════════════════════════════════════════════════════
CREATE TABLE document_chunks (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id       INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    program_id    INTEGER REFERENCES programs(id) ON DELETE SET NULL,   -- 新增：跟著 web_pages 一起填入
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

-- 自動更新 fts_vector 的觸發器
-- 使用 simple（語言無關）而非 english，避免中文被 stop words / stemming 過濾掉
CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE
ON document_chunks FOR EACH ROW EXECUTE FUNCTION
tsvector_update_trigger(fts_vector, 'pg_catalog.simple', chunk_text);
