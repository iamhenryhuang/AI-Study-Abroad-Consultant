"""
流程：
    1. 讀取 data/ 目錄下所有 .json
    2. 對每個 URL 推斷學校（school_id）與頁面類型（page_type）
    3. 寫入 universities 和 web_pages 表
    4. 依頁面類型切片（chunk_text）
    5. 向量化（embed_texts）
    6. 寫入 document_chunks 表
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_ROOT_DIR = _SCRIPTS_DIR.parent

from db.connection import get_connection
from embedder.chunker import chunk_text, infer_page_type
from embedder.vectorize import embed_texts


# ── 學校識別對照表 ─────────────────────────────────────────────────────────────

SCHOOL_MAP: dict[str, dict] = {
    "cmu.edu":       {"school_id": "cmu",      "name": "Carnegie Mellon University"},
    "stanford.edu":  {"school_id": "stanford",  "name": "Stanford University"},
    "mit.edu":       {"school_id": "mit",       "name": "MIT"},
    "illinois.edu":  {"school_id": "uiuc",      "name": "UIUC"},
    "uiuc.edu":      {"school_id": "uiuc",      "name": "UIUC"},
    "cornell.edu":   {"school_id": "cornell",   "name": "Cornell University"},
    "harvard.edu":   {"school_id": "harvard",   "name": "Harvard University"},
    "princeton.edu": {"school_id": "princeton", "name": "Princeton University"},
    "utexas.edu":    {"school_id": "utexas",    "name": "UT Austin"},
    "yale.edu":      {"school_id": "yale",      "name": "Yale University"},
    "ucla.edu":      {"school_id": "ucla",      "name": "UCLA"},
    "ucsd.edu":      {"school_id": "ucsd",      "name": "UC San Diego"},
    "nccu.edu.tw":   {"school_id": "nccu",      "name": "National Chengchi University"},
}

# 檔名提示別名（e.g. ut_austin.json → utexas）
FILENAME_HINT_ALIASES: dict[str, str] = {
    "ut_austin": "utexas",
}


def _normalize_filename_hint(filename_hint: str) -> str:
    hint = filename_hint.lower().replace("_professors", "").replace("_professor", "")
    return FILENAME_HINT_ALIASES.get(hint, hint)


def identify_school(url: str, filename_hint: str | None = None) -> dict | None:
    """
    從 URL 的 domain 找出對應學校資訊，失敗時嘗試 filename_hint。

    特殊處理：scholar.google.com 是共用 domain，必須靠 filename_hint 判斷學校。

    Returns:
        {'school_id': ..., 'name': ..., 'domain': ...}，或 None。
    """
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""

    if "scholar.google.com" in hostname:
        return _identify_by_hint(hostname, filename_hint)

    # 一般學校官網：從 domain 比對
    for domain, info in SCHOOL_MAP.items():
        if domain in hostname:
            return {**info, "domain": hostname}

    # 退回 filename_hint
    return _identify_by_hint(hostname, filename_hint)


def _identify_by_hint(hostname: str, filename_hint: str | None) -> dict | None:
    """透過 filename_hint 辨識學校。"""
    if not filename_hint:
        return None
    hint_clean = _normalize_filename_hint(filename_hint)
    for domain, info in SCHOOL_MAP.items():
        if (
            info["school_id"] == hint_clean
            or hint_clean in info["name"].lower()
            or info["school_id"] in hint_clean
        ):
            return {**info, "domain": hostname or f"{filename_hint}.edu"}
    return None


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


def upsert_web_page(conn, university_id: int, url: str, page_type: str, raw_text: str) -> int:
    """寫入或更新 web_pages 表，回傳 page.id。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO web_pages (university_id, url, page_type, raw_text, char_count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                university_id = EXCLUDED.university_id,
                page_type     = EXCLUDED.page_type,
                raw_text      = EXCLUDED.raw_text,
                char_count    = EXCLUDED.char_count
            RETURNING id;
            """,
            (university_id, url, page_type, raw_text, len(raw_text)),
        )
        return cur.fetchone()[0]


def upsert_chunks(
    conn,
    university_id: int,
    page_id: int,
    school_id: str,
    source_url: str,
    page_type: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> int:
    """刪除舊 chunks 並重新寫入，回傳寫入筆數。"""
    if not chunks:
        return 0

    meta_json = json.dumps(
        {"school_id": school_id, "page_type": page_type, "source_url": source_url},
        ensure_ascii=False,
    )

    with conn.cursor() as cur:
        cur.execute("DELETE FROM document_chunks WHERE page_id = %s", (page_id,))
        for idx, (text, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                """
                INSERT INTO document_chunks
                    (university_id, page_id, school_id, source_url, page_type,
                     chunk_index, chunk_text, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                ON CONFLICT (page_id, chunk_index) DO UPDATE SET
                    chunk_text = EXCLUDED.chunk_text,
                    embedding  = EXCLUDED.embedding,
                    metadata   = EXCLUDED.metadata;
                """,
                (university_id, page_id, school_id, source_url, page_type,
                 idx, text, str(emb), meta_json),
            )
    return len(chunks)


# ── Pipeline ───────────────────────────────────────────────────────────────────

def _get_or_create_university(conn, school_info: dict, school_university_ids: dict[str, int]) -> int:
    """回傳 university_id，若尚未寫入則先 upsert。"""
    school_id = school_info["school_id"]
    if school_id not in school_university_ids:
        uid = upsert_university(conn, school_id, school_info["name"], school_info["domain"])
        school_university_ids[school_id] = uid
        conn.commit()
    return school_university_ids[school_id]


def _process_url(
    conn,
    url: str,
    raw_text: str,
    filename_hint: str,
    school_university_ids: dict[str, int],
) -> tuple[int, bool]:
    """
    處理單一 URL：識別學校、寫入 web_pages、切片、向量化、寫入 chunks。

    Returns:
        (chunk_count, skipped)
    """
    school_info = identify_school(url, filename_hint=filename_hint)
    if not school_info:
        print(f"無法識別學校（未知 domain），跳過：{url[:80]}")
        return 0, True

    university_id = _get_or_create_university(conn, school_info, school_university_ids)

    page_type = infer_page_type(url)
    page_id   = upsert_web_page(conn, university_id, url, page_type, raw_text)
    conn.commit()

    chunks = chunk_text(raw_text, page_type=page_type, url=url, school_name=school_info["name"])
    if not chunks:
        print(f"切片結果為空：{url[:80]}")
        return 0, True

    print(f"  [{school_info['school_id']}][{page_type}] {url[-60:]} → {len(chunks)} chunks")
    embeddings = embed_texts(chunks)
    n = upsert_chunks(conn, university_id, page_id, school_info["school_id"], url, page_type, chunks, embeddings)
    conn.commit()
    return n, False


def run_pipeline(data_dirname: str = "data") -> bool:
    """讀取 data/*.json → 切片 → 向量化 → 寫入 DB。"""
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

    total_pages   = 0
    total_chunks  = 0
    skipped       = 0

    try:
        for path in json_files:
            print(f"\n讀取檔案：{path.name}")
            data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))

            if not isinstance(data, dict):
                print(f"{path.name} 格式不符（預期為 dict），跳過。")
                continue

            school_university_ids: dict[str, int] = {}

            for url, raw_text in data.items():
                if not isinstance(url, str) or not isinstance(raw_text, str):
                    skipped += 1
                    continue

                raw_text = raw_text.strip()
                if len(raw_text) < 50:
                    print(f"跳過（文字過短）：{url[:80]}")
                    skipped += 1
                    continue

                n, was_skipped = _process_url(conn, url, raw_text, path.stem, school_university_ids)
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
