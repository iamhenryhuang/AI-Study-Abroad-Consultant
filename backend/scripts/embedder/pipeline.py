"""
流程：
    1. 讀取 crawler/school_data/ 目錄下所有 .json
    2. 每個 record 含 school_id, url, passed_types, data
    3. 正規化 school_id，寫入 universities 和 web_pages 表
    4. 依主要 page_type（passed_types 中分數最高者）切片
    5. 向量化（embed_texts）
    6. 寫入 document_chunks 表
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_ROOT_DIR = _SCRIPTS_DIR.parent.parent  # 專案根目錄（AI-Study-Abroad-Consultant/）

from db.connection import get_connection
from embedder.chunker import chunk_text
from embedder.vectorize import embed_texts


# ── 學校名稱對照表 ──────────────────────────────────────────────────────────────

SCHOOL_NAMES: dict[str, str] = {
    "cmu":      "Carnegie Mellon University",
    "stanford": "Stanford University",
    "mit":      "MIT",
    "caltech":  "Caltech",
    "gatech":   "Georgia Tech",
    "ucla":     "UCLA",
    "ucsd":     "UC San Diego",
    "uci":      "UC Irvine",
    "umass":    "UMass Amherst",
    "purdue":   "Purdue University",
    "washu":    "Washington University in St. Louis",
    "utoronto": "University of Toronto",
}

# 已知拼寫錯誤的 school_id 正規化（資料來源可能有 typo）
_SCHOOL_ID_NORMALIZE: dict[str, str] = {
    "standford": "stanford",
    "perdure":   "purdue",
}


def _normalize_school_id(school_id: str) -> str:
    sid = school_id.lower().strip()
    return _SCHOOL_ID_NORMALIZE.get(sid, sid)


def _get_school_name(school_id: str) -> str:
    return SCHOOL_NAMES.get(school_id, school_id.upper())


def _get_primary_type(passed_types: list[dict]) -> str:
    """回傳 passed_types 中分數最高的 type，無資料時回傳 'general'。"""
    if not passed_types:
        return "general"
    return max(passed_types, key=lambda x: x.get("score", 0))["type"]


# ── DB 操作 ────────────────────────────────────────────────────────────────────

def upsert_university(conn, school_id: str, name: str, domain: str) -> int:
    """寫入或更新 universities 表，回傳 university.id。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO universities (school_id, name, domain)
            VALUES (%s, %s, %s)
            ON CONFLICT (school_id) DO UPDATE SET
                name   = EXCLUDED.name,
                domain = EXCLUDED.domain
            RETURNING id;
            """,
            (school_id, name, domain),
        )
        return cur.fetchone()[0]


def upsert_web_page(
    conn,
    university_id: int,
    url: str,
    passed_types: list[dict],
    raw_text: str,
) -> int:
    """寫入或更新 web_pages 表，回傳 page.id。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO web_pages (university_id, url, passed_types, raw_text, char_count)
            VALUES (%s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                university_id = EXCLUDED.university_id,
                passed_types  = EXCLUDED.passed_types,
                raw_text      = EXCLUDED.raw_text,
                char_count    = EXCLUDED.char_count
            RETURNING id;
            """,
            (
                university_id, url,
                json.dumps(passed_types, ensure_ascii=False),
                raw_text, len(raw_text),
            ),
        )
        return cur.fetchone()[0]


def upsert_chunks(
    conn,
    university_id: int,
    page_id: int,
    school_id: str,
    source_url: str,
    passed_types: list[dict],
    chunks: list[str],
    embeddings: list[list[float]],
) -> int:
    """刪除舊 chunks 並重新寫入，回傳寫入筆數。"""
    if not chunks:
        return 0

    passed_types_json = json.dumps(passed_types, ensure_ascii=False)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM document_chunks WHERE page_id = %s", (page_id,))
        for idx, (text, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                """
                INSERT INTO document_chunks
                    (university_id, page_id, school_id, source_url,
                     passed_types, chunk_index, chunk_text, embedding)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::vector)
                ON CONFLICT (page_id, chunk_index) DO UPDATE SET
                    chunk_text   = EXCLUDED.chunk_text,
                    embedding    = EXCLUDED.embedding,
                    passed_types = EXCLUDED.passed_types;
                """,
                (
                    university_id, page_id, school_id, source_url,
                    passed_types_json, idx, text, str(emb),
                ),
            )
    return len(chunks)


# ── Pipeline ───────────────────────────────────────────────────────────────────

def _get_or_create_university(
    conn,
    school_id: str,
    school_university_ids: dict[str, int],
) -> int:
    """回傳 university_id，若尚未寫入則先 upsert。"""
    if school_id not in school_university_ids:
        name   = _get_school_name(school_id)
        domain = f"{school_id}.edu"
        uid    = upsert_university(conn, school_id, name, domain)
        school_university_ids[school_id] = uid
        conn.commit()
    return school_university_ids[school_id]


def _process_record(
    conn,
    record: dict,
    school_university_ids: dict[str, int],
) -> tuple[int, bool]:
    """
    處理單一 record：寫入 web_pages、切片、向量化、寫入 chunks。

    Returns:
        (chunk_count, skipped)
    """
    school_id    = _normalize_school_id(record.get("school_id", ""))
    url          = record.get("url", "").strip()
    passed_types = record.get("passed_types", [])
    raw_text     = record.get("data", "").strip()

    if not school_id or not url or not raw_text:
        return 0, True

    if len(raw_text) < 50:
        print(f"跳過（文字過短）：{url[:80]}")
        return 0, True

    university_id = _get_or_create_university(conn, school_id, school_university_ids)
    primary_type  = _get_primary_type(passed_types)
    page_id       = upsert_web_page(conn, university_id, url, passed_types, raw_text)
    conn.commit()

    school_name = _get_school_name(school_id)
    chunks      = chunk_text(raw_text, primary_type=primary_type, school_name=school_name)
    if not chunks:
        print(f"切片結果為空：{url[:80]}")
        return 0, True

    print(f"  [{school_id}][{primary_type}] {url[-60:]} → {len(chunks)} chunks")
    embeddings = embed_texts(chunks)
    n = upsert_chunks(
        conn, university_id, page_id, school_id, url,
        passed_types, chunks, embeddings,
    )
    conn.commit()
    return n, False


def run_pipeline(data_dirname: str = "crawler/school_data") -> bool:
    """讀取 crawler/school_data/*.json → 切片 → 向量化 → 寫入 DB。"""
    data_dir = _ROOT_DIR / data_dirname
    if not data_dir.is_dir():
        print(f"找不到目錄 {data_dir}")
        return False

    json_files = sorted(data_dir.glob("*.json"))
    if not json_files:
        print(f"目錄 {data_dir} 下沒有 .json 檔")
        return False

    conn = get_connection()
    if not conn:
        print("無法連線至資料庫，請確認 .env 中的 DATABASE_URL。")
        return False

    total_pages  = 0
    total_chunks = 0
    skipped      = 0

    try:
        school_university_ids: dict[str, int] = {}

        for path in json_files:
            print(f"\n讀取檔案：{path.name}")
            records: list[dict] = json.loads(path.read_text(encoding="utf-8"))

            if not isinstance(records, list):
                print(f"{path.name} 格式不符（預期為 list），跳過。")
                continue

            for record in records:
                if not isinstance(record, dict):
                    skipped += 1
                    continue

                n, was_skipped = _process_record(conn, record, school_university_ids)
                if was_skipped:
                    skipped += 1
                else:
                    total_pages  += 1
                    total_chunks += n

        print(f"\n完成！共處理 {total_pages} 頁、{total_chunks} 個 chunk，跳過 {skipped} 筆。")
        return True

    except Exception as e:
        conn.rollback()
        print(f"\nPipeline 失敗: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    run_pipeline()
