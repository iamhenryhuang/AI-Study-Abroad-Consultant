"""向量化模組：使用本地 BAAI/bge-m3 模型。

模型路徑由 .env 中的 BGE_EMBED_MODEL_PATH 決定，
預設為 D:\\DforDownload\\BAAI\\bge-m3。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

# 從 .env 讀取路徑；若未設定則使用預設 D 槽路徑
_MODEL_PATH = Path(
    os.getenv("BGE_EMBED_MODEL_PATH", r"D:\DforDownload\BAAI\bge-m3")
)

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        model_id = str(_MODEL_PATH) if _MODEL_PATH.exists() else "BAAI/bge-m3"
        if not _MODEL_PATH.exists():
            print(
                f"[vectorize] 找不到本地模型路徑 {_MODEL_PATH}，"
                "改從 HuggingFace 下載（約 2.3 GB）..."
            )
        else:
            print(f"[vectorize] 載入本地模型：{model_id}")
        _model = SentenceTransformer(model_id, trust_remote_code=True)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """將文字列表轉成 1024 維向量列表。"""
    if not texts:
        return []
    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=8,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    return [emb.tolist() for emb in embeddings]
