"""
pipeline.py — Chunking + Embedding 流水線（v2）

處理新格式的資料：
    school_info.json = { "https://url1": "純文字1", "https://url2": "純文字2", ... }

流程：
    1. 讀取 school_info.json（或 data/ 目錄下所有 .json）
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

# 讓 scripts 目錄在 path 中
CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CURRENT_DIR.parent
ROOT_DIR = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db.connection import get_connection
from embedder.chunker import chunk_text, infer_page_type
from embedder.vectorize import embed_texts


# ── 學校識別對照表 ─────────────────────────────────────────────
# 只保留 backend/data 目前使用到的學校（依 URL domain 對應）
SCHOOL_MAP: dict[str, dict] = {
    "cmu.edu": {
        "school_id": "cmu",
        "name": "Carnegie Mellon University",
    },
    "stanford.edu": {
        "school_id": "stanford",
        "name": "Stanford University",
    },
    "mit.edu": {
        "school_id": "mit",
        "name": "MIT",
    },
    "illinois.edu": {
        "school_id": "uiuc",
        "name": "UIUC",
    },
    "uiuc.edu": {
        "school_id": "uiuc",
        "name": "UIUC",
    },
    "cornell.edu": {
        "school_id": "cornell",
        "name": "Cornell University",
    },
    "harvard.edu": {
        "school_id": "harvard",
        "name": "Harvard University",
    },
    "princeton.edu": {
        "school_id": "princeton",
        "name": "Princeton University",
    },
    "utexas.edu": {
        "school_id": "utexas",
        "name": "UT Austin",
    },
    "yale.edu": {
        "school_id": "yale",
        "name": "Yale University",
    },
    "ucla.edu": {
        "school_id": "ucla",
        "name": "UCLA",
    },
    "ucsd.edu": {
        "school_id": "ucsd",
        "name": "UC San Diego",
    },
    "nccu.edu.tw": {
        "school_id": "nccu",
        "name": "National Chengchi University",
    },
}

# 檔名提示別名（例如 ut_austin.json）對應到 SCHOOL_MAP 的 school_id
FILENAME_HINT_ALIASES: dict[str, str] = {
    "ut_austin": "utexas",
}


def _normalize_filename_hint(filename_hint: str) -> str:
    hint = filename_hint.lower().replace("_professors", "").replace("_professor", "")
    return FILENAME_HINT_ALIASES.get(hint, hint)


def identify_school(url: str, filename_hint: str | None = None) -> dict | None:
    """
    從 URL 的 domain 找出對應學校資訊。
    若識別失敗，則嘗試使用 filename_hint。
    回傳 {'school_id': ..., 'name': ..., 'domain': ...}，若無法識別則回傳 None。

    特殊處理：
      - scholar.google.com → 教授資料 JSON，學校資訊來自 filename_hint
        （因為 Google Scholar 是共用 domain，無法從 URL 判斷學校）
    """
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""

    # Google Scholar URL → 直接走 filename_hint（學校資訊在檔名裡）
    # 例如 stanford_professors.json → filename_hint = "stanford_professors" → school_id = "stanford"
    if "scholar.google.com" in hostname:
        if filename_hint:
            hint_clean = _normalize_filename_hint(filename_hint)
            for domain, info in SCHOOL_MAP.items():
                if (
                    info["school_id"] == hint_clean
                    or hint_clean in info["name"].lower()
                    or info["school_id"] in hint_clean
                ):
                    return {**info, "domain": hostname}
        return None  # 無法從 Google Scholar URL 判斷學校且沒有 filename_hint

    # 1. 優先從 URL 識別（一般學校官網）
    for domain, info in SCHOOL_MAP.items():
        if domain in hostname:
            return {**info, "domain": hostname}

    # 2. 回退到 filename_hint 識別
    if filename_hint:
        hint_clean = _normalize_filename_hint(filename_hint)
        for domain, info in SCHOOL_MAP.items():
            if (
                info["school_id"] == hint_clean
                or hint_clean in info["name"].lower()
                or info["school_id"] in hint_clean
            ):
                return {**info, "domain": hostname or f"{filename_hint}.edu"}

    return None


# ── DB 操作 ──────────────────────────────────────────────────

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
    page_type: str,
    raw_text: str,
) -> int:
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

    with conn.cursor() as cur:
        # 先清除同一 page_id 的舊 chunk
        cur.execute("DELETE FROM document_chunks WHERE page_id = %s", (page_id,))

        meta_base = {
            "school_id":  school_id,
            "page_type":  page_type,
            "source_url": source_url,
        }
        meta_json = json.dumps(meta_base, ensure_ascii=False)

        for idx, (text, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                """
                INSERT INTO document_chunks
                    (university_id, page_id, school_id, source_url, page_type,
                     chunk_index, chunk_text, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                ON CONFLICT (page_id, chunk_index) DO UPDATE SET
                    chunk_text    = EXCLUDED.chunk_text,
                    embedding     = EXCLUDED.embedding,
                    metadata      = EXCLUDED.metadata;
                """,
                (
                    university_id, page_id, school_id, source_url, page_type,
                    idx, text, str(emb), meta_json,
                ),
            )
    return len(chunks)


# ── 主線 ─────────────────────────────────────────────────

def run_pipeline(data_dirname: str = "data") -> bool:
    """讀取 school_info.json → 切片 → 向量化 → 寫入 DB。"""
    data_dir = ROOT_DIR / data_dirname
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

    total_pages = 0
    total_chunks = 0
    skipped = 0

    try:
        for path in json_files:
            print(f"\n讀取檔案：{path.name}")
            data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))

            if not isinstance(data, dict):
                print(f"{path.name} 格式不符（預期為 dict），跳過。")
                continue

            # 追蹤同一檔案裡各學校的 university_id
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

                # 識別學校（帶入檔名作為提示）
                filename_hint = path.stem  # e.g. 'caltech'
                school_info = identify_school(url, filename_hint=filename_hint)
                if not school_info:
                    print(f"無法識別學校（未知 domain），跳過：{url[:80]}")
                    skipped += 1
                    continue

                school_id = school_info["school_id"]
                name      = school_info["name"]
                domain    = school_info["domain"]

                # 寫入 universities（若尚未寫入）
                if school_id not in school_university_ids:
                    university_id = upsert_university(conn, school_id, name, domain)
                    school_university_ids[school_id] = university_id
                    conn.commit()
                else:
                    university_id = school_university_ids[school_id]

                # 推斷頁面類型
                page_type = infer_page_type(url)

                # 寫入 web_pages
                page_id = upsert_web_page(conn, university_id, url, page_type, raw_text)
                conn.commit()
                total_pages += 1

                # 切片
                chunks = chunk_text(raw_text, page_type=page_type, url=url, school_name=name)
                if not chunks:
                    print(f"切片結果為空：{url[:80]}")
                    skipped += 1
                    continue

                print(f"  [{school_id}][{page_type}] {url[-60:]} → {len(chunks)} chunks")

                # 向量化
                embeddings = embed_texts(chunks)

                # 寫入 document_chunks
                n = upsert_chunks(
                    conn,
                    university_id=university_id,
                    page_id=page_id,
                    school_id=school_id,
                    source_url=url,
                    page_type=page_type,
                    chunks=chunks,
                    embeddings=embeddings,
                )
                conn.commit()
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
