-- 依申請學校查詢最新的使用者經驗。
-- psycopg 參數：apply_school、limit、offset。
SELECT
    id,
    graduate_school,
    country,
    apply_school,
    apply_program,
    gpa,
    class_rank,
    class_size,
    experience,
    review,
    created_at
FROM user_experiences
WHERE LOWER(apply_school) = LOWER(%(apply_school)s)
ORDER BY created_at DESC, id DESC
LIMIT %(limit)s OFFSET %(offset)s;
