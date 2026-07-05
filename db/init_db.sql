-- 先清除舊表
DROP TABLE IF EXISTS program_requirements CASCADE;
DROP TABLE IF EXISTS universities CASCADE;


CREATE TABLE universities (
    id          SERIAL PRIMARY KEY,
    school_id   VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    domain      VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- 每間學校的 CS 碩士申請結構化要求
CREATE TABLE program_requirements (
    id              SERIAL PRIMARY KEY,
    university_id   INTEGER UNIQUE NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
    program_name    VARCHAR(255) NOT NULL DEFAULT 'MS in Computer Science',

    -- GPA
    min_gpa         NUMERIC(3,2),          -- e.g. 3.30（滿分 4.0 換算）
    gpa_scale       NUMERIC(3,2) DEFAULT 4.00,

    -- 英語能力
    toefl_min       INTEGER,               -- 滿分 120
    ielts_min       NUMERIC(2,1),           -- 滿分 9.0
    duolingo_min    INTEGER,                -- 滿分 160
    english_waiver_note TEXT,               -- 免試條件說明

    -- GRE
    gre_required    BOOLEAN DEFAULT FALSE,
    gre_min_total   INTEGER,                -- 滿分 340
    gre_min_quant   INTEGER,                -- 滿分 170
    gre_min_verbal  INTEGER,                -- 滿分 170

    -- 截止日期
    deadline_fall   DATE,
    deadline_spring DATE,
    priority_deadline DATE,

    -- 學費 / 獎助學金
    tuition_per_year   INTEGER,             -- 年度學費（USD）
    funding_available  BOOLEAN DEFAULT FALSE, -- 是否有獎學金 / RA / TA 機會
    funding_note        TEXT,               -- 獎助學金說明（申請方式、覆蓋範圍等）

    -- 申請文件要求
    requires_sop              BOOLEAN DEFAULT TRUE,  -- 是否需要 Statement of Purpose
    num_recommendation_letters INTEGER,               -- 需要幾封推薦信
    requires_resume           BOOLEAN DEFAULT TRUE,  -- 是否需要履歷/CV

    source_url      TEXT,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_program_requirements_university ON program_requirements(university_id);
