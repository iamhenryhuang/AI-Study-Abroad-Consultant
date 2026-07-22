"""查詢端 embedding：BGE-M3（1024 維），供 hybrid_search 把使用者問題轉向量。

與 data_crawler/backfill_embeddings.py 共用 env BGE_EMBED_MODEL_PATH，
模型只需下載/存放一份。sentence_transformers 延遲 import：
套件或模型不可用時在呼叫端（hybrid_search facade）統一降級純 FTS。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 明確載入 backend/.env：無參數的 load_dotenv() 只找 CWD 的 .env（專案根沒有
# 這個檔），先前能運作是仰賴其他模組先被 import 的副作用。
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        model_path = os.getenv("BGE_EMBED_MODEL_PATH") or "BAAI/bge-m3"
        print(f"[vectorize] 載入 embedding 模型：{model_path}")
        _model = SentenceTransformer(model_path, trust_remote_code=True)
    return _model


def embed_query(text: str) -> list[float]:
    """將單一查詢字串轉成 1024 維向量；空字串回傳 []。"""
    if not text or not text.strip():
        return []
    embeddings = _get_model().encode(
        [text],
        batch_size=8,
        normalize_embeddings=True,   # 把向量正規化成單位向量（長度為 1）。這樣後面在 SQL 用 <=>（cosine distance）算相似度時，才跟寫入端 backfill 時的向量在同一個尺度上
        show_progress_bar=False,
    )
    return embeddings[0].tolist()
