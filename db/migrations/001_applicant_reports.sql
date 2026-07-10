-- ════════════════════════════════════════════════════════════════
-- applicant_reports —— 網路社群的申請結果回報（經驗類資料，非官方）
--
-- 資料來源：
--   gradcafe  —— GradCafe（英文，結構化，995 筆）
--   1point3   —— 一畝三分地論壇（簡體中文，半結構化，596 筆）
--
-- 性質：這是個別申請者的「錄取/被拒回報」，主觀、有樣本偏誤、非權威。
--       與 programs（官方申請要求門檻）截然不同——它答的是「錄取者背景長怎樣」，
--       不是「學校規定要幾分」。RAG 引用時必須標明為非官方經驗談，
--       不得當成錄取門檻或保證（護欄在 backend 生成 prompt 內）。
--
-- 加法式 migration：CREATE IF NOT EXISTS，不動 db/init_db.sql，
-- 也不會被 run.py init-schema（重建 programs 家族）清掉。
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS applicant_reports (
    id            SERIAL PRIMARY KEY,
    source        VARCHAR(20) NOT NULL,        -- 'gradcafe' / '1point3'
    source_url    TEXT,

    -- 學校：原始校名一律保留；school_id 盡量對應到 universities 的別名體系，對不到留 NULL
    school_raw    TEXT NOT NULL,
    school_id     VARCHAR(100),                -- 對應 _SCHOOL_ALIASES（mit/cmu...），對不到為 NULL

    program       TEXT,
    degree_level  VARCHAR(20),                 -- masters / phd / mfa / other...

    -- 結果：兩來源的原始值正規化為統一集合
    decision      VARCHAR(20),                 -- accepted / rejected / waitlisted / interview
                                               -- / offer / ad_no_fund / ad_small_fund / other

    -- GPA：能解析成 4.0 制的存 gpa（供統計/篩選），原始字串一律留 gpa_raw（避免清洗失真）
    gpa           NUMERIC(4,2),
    gpa_raw       TEXT,

    season        VARCHAR(30),                 -- 'Fall 2026' 等（保留原字串）
    notes         TEXT,                        -- gradcafe.notes / 1point3 短 content

    fts_vector    tsvector,                    -- 全文檢索用（供 RAG fallback 撈到）
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (source, source_url)
);

CREATE INDEX IF NOT EXISTS idx_applicant_reports_school   ON applicant_reports(school_id);
CREATE INDEX IF NOT EXISTS idx_applicant_reports_source   ON applicant_reports(source);
CREATE INDEX IF NOT EXISTS idx_applicant_reports_decision ON applicant_reports(decision);
CREATE INDEX IF NOT EXISTS idx_applicant_reports_fts      ON applicant_reports USING GIN (fts_vector);

-- fts_vector 自動維護（simple config：語言無關，中文不會被英文 stemming 誤傷，與 document_chunks 一致）
-- 對 school_raw + program + notes 三欄建索引，讓「CMU CS 錄取」這類查詢命中
CREATE OR REPLACE FUNCTION applicant_reports_fts_trigger() RETURNS trigger AS $$
BEGIN
    NEW.fts_vector :=
        to_tsvector('pg_catalog.simple',
            coalesce(NEW.school_raw, '') || ' ' ||
            coalesce(NEW.program, '')    || ' ' ||
            coalesce(NEW.notes, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS applicant_reports_fts_update ON applicant_reports;
CREATE TRIGGER applicant_reports_fts_update
    BEFORE INSERT OR UPDATE ON applicant_reports
    FOR EACH ROW EXECUTE FUNCTION applicant_reports_fts_trigger();
