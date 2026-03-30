"""
流程：
    1. 讀取 crawler/school_data/ 目錄下所有 .json
    2. 每個 record 含 school_id, url, passed_types, data
    3. 正規化 school_id，寫入 universities 和 web_pages 表
    4. 依主要 page_type（passed_types 中分數最高者）切片
    5. 向量化（embed_texts）—— 僅當 content_hash 有變動時才執行
    6. 寫入 document_chunks 表

新增功能：
    - web_pages.content_hash：對 raw_text 做 SHA-256，
      僅在 hash 改變時才重新切片 / 向量化，節省 API 費用
    - 跳過記錄附帶原因 + url，結束後統一輸出摘要
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
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
    "Washu":    "Washington University in St. Louis",
    "utoronto": "University of Toronto",
    "bu":       "Boston University",
    "columbia": "Columbia University",
    "cornell":" Cornell University",
    "northeastern":"Northeastern University",
}

_SCHOOL_ID_NORMALIZE: dict[str, str] = {
    "standford": "stanford",
    "perdure":   "purdue",
}


# ── 跳過記錄 ───────────────────────────────────────────────────────────────────

@dataclass
class SkipRecord:
    reason: str
    school_id: str
    url: str


@dataclass
class PipelineStats:
    total_pages:  int = 0
    total_chunks: int = 0
    unchanged:    int = 0          # hash 未變動，略過 embed
    skipped: list[SkipRecord] = field(default_factory=list)

    def add_skip(self, reason: str, school_id: str = "", url: str = "") -> None:
        self.skipped.append(SkipRecord(reason=reason, school_id=school_id, url=url))

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print(f"  處理頁數   : {self.total_pages}")
        print(f"  總 chunks  : {self.total_chunks}")
        print(f"  略過 embed : {self.unchanged}（內容未變動）")
        print(f"  跳過筆數   : {len(self.skipped)}")

        if self.skipped:
            # 依原因分組顯示
            from collections import defaultdict
            by_reason: dict[str, list[SkipRecord]] = defaultdict(list)
            for r in self.skipped:
                by_reason[r.reason].append(r)

            print("\n  ── 跳過明細 ──")
            for reason, records in by_reason.items():
                print(f"\n  [{reason}]（{len(records)} 筆）")
                for rec in records:
                    school_tag = f"[{rec.school_id}] " if rec.school_id else ""
                    url_display = rec.url[:100] if rec.url else "（無 URL）"
                    print(f"    {school_tag}{url_display}")

        print("=" * 60)


# ── 工具函式 ───────────────────────────────────────────────────────────────────

def _normalize_school_id(school_id: str) -> str:
    sid = school_id.lower().strip()
    return _SCHOOL_ID_NORMALIZE.get(sid, sid)


def _get_school_name(school_id: str) -> str:
    return SCHOOL_NAMES.get(school_id, school_id.upper())


def _get_primary_type(passed_types: list[dict]) -> str:
    if not passed_types:
        return "general"
    return max(passed_types, key=lambda x: x.get("score", 0))["type"]


def _compute_hash(text: str) -> str:
    """對原始文字做 SHA-256，作為內容指紋。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── DB 操作 ────────────────────────────────────────────────────────────────────

def upsert_university(conn, school_id: str, name: str, domain: str) -> int:
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
) -> tuple[int, bool]:
    """
    寫入或更新 web_pages 表。

    Returns:
        (page_id, content_changed)
        content_changed=False 表示 raw_text hash 與 DB 中相同，不需重新 embed。
    """
    new_hash = _compute_hash(raw_text)

    with conn.cursor() as cur:
        # 先查舊 hash（避免不必要的 UPDATE）
        cur.execute(
            "SELECT id, content_hash FROM web_pages WHERE url = %s",
            (url,),
        )
        existing = cur.fetchone()

        if existing:
            existing_id, existing_hash = existing
            content_changed = existing_hash != new_hash
        else:
            existing_id     = None
            content_changed = True  # 全新資料，必須 embed

        # 無論 hash 是否變動，都更新 metadata（university_id / passed_types 可能改變）
        cur.execute(
            """
            INSERT INTO web_pages
                (university_id, url, passed_types, raw_text, char_count, content_hash)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                university_id = EXCLUDED.university_id,
                passed_types  = EXCLUDED.passed_types,
                raw_text      = EXCLUDED.raw_text,
                char_count    = EXCLUDED.char_count,
                content_hash  = EXCLUDED.content_hash
            RETURNING id;
            """,
            (
                university_id, url,
                json.dumps(passed_types, ensure_ascii=False),
                raw_text, len(raw_text), new_hash,
            ),
        )
        page_id = cur.fetchone()[0]

    return page_id, content_changed


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
    stats: PipelineStats,
) -> None:
    """處理單一 record，將結果寫入 stats（不再回傳 bool）。"""
    school_id    = _normalize_school_id(record.get("school_id", ""))
    url          = record.get("url", "").strip()
    passed_types = record.get("passed_types", [])
    raw_text     = record.get("data", "").strip()

    # ── 基本欄位驗證 ──
    if not school_id:
        stats.add_skip("缺少 school_id", url=url)
        return
    if not url:
        stats.add_skip("缺少 url", school_id=school_id)
        return
    if not raw_text:
        stats.add_skip("data 欄位為空", school_id=school_id, url=url)
        return
    if len(raw_text) < 50:
        stats.add_skip(f"文字過短（{len(raw_text)} 字元，最低 50）",
                       school_id=school_id, url=url)
        return

    # ── 寫入 university / web_page ──
    university_id = _get_or_create_university(conn, school_id, school_university_ids)
    page_id, content_changed = upsert_web_page(
        conn, university_id, url, passed_types, raw_text
    )
    conn.commit()

    # ── hash 未變動 → 略過 embed ──
    if not content_changed:
        stats.unchanged += 1
        print(f"  [略過 embed][{school_id}] {url[-70:]}")
        return

    # ── 切片 ──
    primary_type = _get_primary_type(passed_types)
    school_name  = _get_school_name(school_id)
    chunks       = chunk_text(raw_text, primary_type=primary_type, school_name=school_name)

    if not chunks:
        stats.add_skip("切片結果為空", school_id=school_id, url=url)
        return

    # ── 向量化 & 寫入 ──
    print(f"  [embed][{school_id}][{primary_type}] {url[-60:]} → {len(chunks)} chunks")
    embeddings = embed_texts(chunks)
    n = upsert_chunks(
        conn, university_id, page_id, school_id, url,
        passed_types, chunks, embeddings,
    )
    conn.commit()

    stats.total_pages  += 1
    stats.total_chunks += n


def run_pipeline(
    data_dirname: str = "crawler/school_data",
    target_school: str | None = None,
) -> bool:
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

    stats = PipelineStats()

    try:
        school_university_ids: dict[str, int] = {}

        for path in json_files:
            print(f"\n讀取檔案：{path.name}")
            try:
                records: list[dict] = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                stats.add_skip(f"JSON 解析失敗（{e}）", url=str(path.name))
                continue

            if not isinstance(records, list):
                stats.add_skip("檔案格式不符（預期為 list）", url=str(path.name))
                continue

            for record in records:
                if not isinstance(record, dict):
                    stats.add_skip("record 非 dict 型別",
                                   url=str(record)[:80] if record else "")
                    continue

                if target_school:
                    sid = _normalize_school_id(record.get("school_id", ""))
                    if sid != target_school.lower().strip():
                        # target filter 略過，不計入 skipped（非錯誤）
                        continue

                _process_record(conn, record, school_university_ids, stats)

        stats.print_summary()
        return True

    except Exception as e:
        conn.rollback()
        print(f"\nPipeline 意外失敗: {e}")
        import traceback
        traceback.print_exc()
        stats.print_summary()   # 即使失敗也輸出已收集的統計
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    run_pipeline("crawler/school_data", "bu")   