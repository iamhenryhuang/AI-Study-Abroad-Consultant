import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection


def _print_school_stats(cur) -> None:
    cur.execute("""
        SELECT
            u.school_id,
            u.name,
            COUNT(DISTINCT wp.id)  AS page_count,
            COUNT(dc.id)           AS chunk_count,
            SUM(dc.embedding IS NOT NULL::int) AS has_vector
        FROM universities u
        LEFT JOIN web_pages wp ON wp.university_id = u.id
        LEFT JOIN document_chunks dc ON dc.university_id = u.id
        GROUP BY u.id, u.school_id, u.name
        ORDER BY u.school_id
    """)
    print(f"\n{'學校 ID':<12} {'學校名稱':<40} {'頁面數':<8} {'chunk數':<10} {'向量數'}")
    print("-" * 90)
    for sid, name, pages, chunks, vecs in cur.fetchall():
        print(f"{sid:<12} {name:<40} {pages:<8} {chunks:<10} {vecs}")


def _print_chunk_type_stats(cur) -> None:
    cur.execute("""
        SELECT dc.school_id, pt->>'type' AS ptype, COUNT(*) AS cnt
        FROM document_chunks dc,
             jsonb_array_elements(dc.passed_types) AS pt
        GROUP BY dc.school_id, pt->>'type'
        ORDER BY dc.school_id, pt->>'type'
    """)
    print(f"\n{'學校':<12} {'頁面類型':<20} {'chunk數'}")
    print("-" * 45)
    for sid, ptype, cnt in cur.fetchall():
        print(f"{sid:<12} {ptype:<20} {cnt}")


def _print_chunk_preview(cur) -> None:
    cur.execute("""
        SELECT
            dc.school_id,
            dc.passed_types,
            dc.chunk_index,
            dc.source_url,
            LEFT(dc.chunk_text, 80) AS preview,
            dc.embedding IS NOT NULL AS has_vector
        FROM document_chunks dc
        ORDER BY dc.school_id, dc.chunk_index
        LIMIT 10
    """)
    print("\n--- 抽查前 10 筆 chunk ---")
    for sid, passed_types, idx, url, preview, has_vec in cur.fetchall():
        types_str = ", ".join(pt["type"] for pt in (passed_types or []))
        print(f"  [{sid}][{types_str}] chunk#{idx} vec={has_vec}")
        print(f"    URL: {url[-60:]}")
        print(f"    預覽: {preview!r}")


def _print_vector_dim(cur) -> None:
    cur.execute("SELECT embedding FROM document_chunks WHERE embedding IS NOT NULL LIMIT 1")
    row = cur.fetchone()
    if row and row[0]:
        vec = row[0]
        dims = len(vec) if isinstance(vec, (list, tuple)) else len(str(vec).strip("[]").split(","))
        print(f"\n向量維度: {dims}")


def verify_embeddings() -> bool:
    """檢查向量資料庫（v2 schema）內容。"""
    conn = get_connection()
    if not conn:
        return False

    try:
        with conn.cursor() as cur:
            _print_school_stats(cur)
            _print_chunk_type_stats(cur)
            _print_chunk_preview(cur)
            _print_vector_dim(cur)
        print("\n向量資料庫驗證通過。")
        return True
    except Exception as e:
        print(f"驗證失敗: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    verify_embeddings()
