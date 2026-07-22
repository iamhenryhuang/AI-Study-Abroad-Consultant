-- 使用者主動上傳的申請經驗；與外部匯入的 applicant_reports 分開保存。
CREATE TABLE IF NOT EXISTS user_experiences (
    id               BIGSERIAL PRIMARY KEY,
    graduate_school  TEXT NOT NULL,
    country          VARCHAR(100) NOT NULL,
    apply_school     TEXT NOT NULL,
    apply_program    TEXT NOT NULL,
    -- NUMERIC(5,2)：百分制的 100 需要 3 位整數，(4,2) 只到 99.99 會與下方
    -- CHECK (gpa <= 100) 互相矛盾——驗證放行但寫入時 numeric field overflow。
    gpa              NUMERIC(5,2),
    class_rank       INTEGER,
    class_size       INTEGER,
    experience       JSONB NOT NULL DEFAULT '[]'::jsonb,
    review           TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_user_experiences_gpa
        CHECK (gpa IS NULL OR (gpa >= 0 AND gpa <= 100)),
    CONSTRAINT chk_user_experiences_rank
        CHECK (class_rank IS NULL OR class_rank > 0),
    CONSTRAINT chk_user_experiences_size
        CHECK (class_size IS NULL OR class_size > 0),
    CONSTRAINT chk_user_experiences_rank_size
        CHECK (class_rank IS NULL OR class_size IS NULL OR class_rank <= class_size),
    CONSTRAINT chk_user_experiences_items
        CHECK (jsonb_typeof(experience) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_user_experiences_apply_school_lower
    ON user_experiences (LOWER(apply_school));
CREATE INDEX IF NOT EXISTS idx_user_experiences_school_program_lower
    ON user_experiences (LOWER(apply_school), LOWER(apply_program));
CREATE INDEX IF NOT EXISTS idx_user_experiences_created_at
    ON user_experiences (created_at DESC);

-- 既有資料表升級：本檔用 CREATE TABLE IF NOT EXISTS，已建好的表不會套用上面的
-- 欄位定義，故在此補一次型別修正（(4,2) → (5,2)）。冪等，可重複執行。
ALTER TABLE user_experiences ALTER COLUMN gpa TYPE NUMERIC(5,2);
