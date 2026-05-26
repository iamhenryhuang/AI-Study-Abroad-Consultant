from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

_MODEL_PATH = Path(
    os.getenv("BGE_EMBED_MODEL_PATH", r"D:\DforDownload\BAAI\bge-m3")
)

_model: SentenceTransformer | None = None


def _resolve_model_id() -> str:
    if not _MODEL_PATH.exists():
        print(f"[vectorize] 找不到本地模型 {_MODEL_PATH}，改從 HuggingFace 下載（約 2.3 GB）...")
        return "BAAI/bge-m3"
    snapshots_dir = _MODEL_PATH / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted(snapshots_dir.iterdir())
        if snapshots:
            resolved = snapshots[-1]
            print(f"[vectorize] 偵測到 HF hub 快取，使用 snapshot：{resolved}")
            return str(resolved)
    print(f"[vectorize] 載入本地模型：{_MODEL_PATH}")
    return str(_MODEL_PATH)


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(_resolve_model_id(), trust_remote_code=True)
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
