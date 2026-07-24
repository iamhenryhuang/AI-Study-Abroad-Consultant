"""獨立 backfill 腳本：補齊 document_chunks 中 embedding IS NULL 的向量。

平常 pipeline 以 ENABLE_EMBEDDING=off 執行（靠 fts_vector 全文檢索），
之後想開向量檢索時跑這支即可：

    python -m data_crawler.backfill_embeddings [--school-id ucla] [--batch-size 16]

模型沿用 backend embedder：BAAI/bge-m3（1024 維），
env BGE_EMBED_MODEL_PATH 可指定本機模型路徑。
"""
import argparse
import os

from dotenv import load_dotenv

load_dotenv()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school-id", default=None, help="只補指定學校（預設全部）")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="最多處理幾筆（0 = 不限）")
    args = ap.parse_args()

    from sentence_transformers import SentenceTransformer
    from .db import get_connection

    # 空字串 env（.env 裡 BGE_EMBED_MODEL_PATH=）視為未設定 → 用預設 hub id；
    # trust_remote_code=True：BGE-M3 在新版 sentence-transformers 下載入所需。
    model_path = os.getenv("BGE_EMBED_MODEL_PATH") or "BAAI/bge-m3"
    print(f"載入模型：{model_path}")
    model = SentenceTransformer(model_path, trust_remote_code=True)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id, chunk_text FROM document_chunks WHERE embedding IS NULL"
            params = []
            if args.school_id:
                sql += " AND school_id = %s"
                params.append(args.school_id)
            sql += " ORDER BY id"
            if args.limit > 0:
                sql += " LIMIT %s"
                params.append(args.limit)
            cur.execute(sql, params)
            rows = cur.fetchall()

        print(f"待補 embedding：{len(rows)} 筆")
        for i in range(0, len(rows), args.batch_size):
            batch = rows[i:i + args.batch_size]
            vectors = model.encode([r[1] for r in batch],
                                   batch_size=args.batch_size,
                                   normalize_embeddings=True)
            with conn.cursor() as cur:
                for (chunk_id, _), vec in zip(batch, vectors):
                    cur.execute("UPDATE document_chunks SET embedding = %s WHERE id = %s",
                                (vec.tolist(), chunk_id))
            conn.commit()
            print(f"  進度 {min(i + args.batch_size, len(rows))}/{len(rows)}")
    finally:
        conn.close()
    print("✅ backfill 完成")


if __name__ == "__main__":
    main()
