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

CREATE INDEX idx_uni_name_trgm ON universities USING gin (name gin_trgm_ops);


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
    fts_vector    tsvector, -- 全文檢索向量
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (page_id, chunk_index)
);

-- HNSW index: 提升 m 與 ef_construction 以優化召回率
CREATE INDEX idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 128);

-- FTS index: 加速關鍵字搜尋
CREATE INDEX idx_chunks_fts ON document_chunks USING GIN (fts_vector);

-- GIN index: 加速 metadata JSONB
CREATE INDEX idx_chunks_metadata ON document_chunks USING GIN (metadata);

-- 基礎過濾索引
CREATE INDEX idx_chunks_school   ON document_chunks(school_id);
CREATE INDEX idx_chunks_pagetype ON document_chunks(page_type);
CREATE INDEX idx_chunks_pageid   ON document_chunks(page_id);

-- 自動更新 fts_vector 的觸發器
CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE
ON document_chunks FOR EACH ROW EXECUTE FUNCTION
tsvector_update_trigger(fts_vector, 'pg_catalog.english', chunk_text);
