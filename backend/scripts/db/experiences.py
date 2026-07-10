"""使用者申請經驗的建表、寫入與查詢。"""
from __future__ import annotations

import json

from psycopg.rows import dict_row

from .connection import PROJECT_ROOT, get_connection

SCHEMA_PATH = PROJECT_ROOT / "db" / "init_experience.sql"


def ensure_experience_schema() -> None:
    conn = get_connection()
    if not conn:
        raise RuntimeError("DATABASE_URL 未設定或無法連線資料庫")
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def create_experience(data: dict) -> dict:
    conn = get_connection()
    if not conn:
        raise RuntimeError("DATABASE_URL 未設定或無法連線資料庫")
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """INSERT INTO user_experiences
                       (graduate_school, country, apply_school, apply_program, gpa,
                        class_rank, class_size, experience, review)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                   RETURNING id, graduate_school, country, apply_school, apply_program,
                             gpa, class_rank, class_size, experience, review, created_at""",
                (
                    data["graduate_school"], data["country"], data["apply_school"],
                    data["apply_program"], data.get("gpa"), data.get("class_rank"),
                    data.get("class_size"), json.dumps(data.get("experience", []), ensure_ascii=False),
                    data["review"],
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_experiences(apply_school: str, apply_program: str | None,
                     limit: int, offset: int) -> tuple[list[dict], int]:
    conn = get_connection()
    if not conn:
        raise RuntimeError("DATABASE_URL 未設定或無法連線資料庫")
    where = "LOWER(apply_school) = LOWER(%s)"
    params: list = [apply_school]
    if apply_program:
        where += " AND LOWER(apply_program) = LOWER(%s)"
        params.append(apply_program)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM user_experiences WHERE {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"""SELECT id, graduate_school, country, apply_school, apply_program, gpa,
                           class_rank, class_size, experience, review, created_at
                    FROM user_experiences
                    WHERE {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s""",
                [*params, limit, offset],
            )
            rows = [dict(row) for row in cur.fetchall()]
        return rows, total
    finally:
        conn.close()
