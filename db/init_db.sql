CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 先清除舊表
DROP TABLE IF EXISTS document_chunks CASCADE;
DROP TABLE IF EXISTS web_pages CASCADE;
DROP TABLE IF EXISTS universities CASCADE;


CREATE TABLE universities (
    id          SERIAL PRIMARY KEY,
    school_id   VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    domain      VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE web_pages (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    url           TEXT UNIQUE NOT NULL,
    page_type     VARCHAR(100),
    raw_text      TEXT NOT NULL,
    char_count    INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_web_pages_university ON web_pages(university_id);
CREATE INDEX idx_web_pages_page_type  ON web_pages(page_type);


CREATE TABLE document_chunks (
    id            SERIAL PRIMARY KEY,
    university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE,
    page_id       INTEGER REFERENCES web_pages(id) ON DELETE CASCADE,
    school_id     VARCHAR(100) NOT NULL,
    source_url    TEXT NOT NULL,
    page_type     VARCHAR(100),
    chunk_index   INTEGER NOT NULL,
    chunk_text    TEXT NOT NULL,
    embedding     vector(1024),
    metadata      JSONB,
    fts_vector    tsvector,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (page_id, chunk_index)
);

-- HNSW index：m=32 增強圖連通性，ef_construction=200 提升建構品質，兩者都能提高召回率
CREATE INDEX idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 32, ef_construction = 200);

-- FTS index：加速關鍵字搜尋
CREATE INDEX idx_chunks_fts ON document_chunks USING GIN (fts_vector);

-- GIN index：加速 metadata JSONB 查詢
CREATE INDEX idx_chunks_metadata ON document_chunks USING GIN (metadata);

-- 單欄過濾索引
CREATE INDEX idx_chunks_school   ON document_chunks(school_id);
CREATE INDEX idx_chunks_pagetype ON document_chunks(page_type);
CREATE INDEX idx_chunks_pageid   ON document_chunks(page_id);

-- 複合過濾索引：school_id + page_type 常一起使用，避免 bitmap AND
CREATE INDEX idx_chunks_school_pagetype ON document_chunks(school_id, page_type);

-- 自動更新 fts_vector 的觸發器
-- 使用 simple（語言無關）而非 english，避免中文被英文 stop words / stemming 過濾掉
CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE
ON document_chunks FOR EACH ROW EXECUTE FUNCTION
tsvector_update_trigger(fts_vector, 'pg_catalog.simple', chunk_text);
