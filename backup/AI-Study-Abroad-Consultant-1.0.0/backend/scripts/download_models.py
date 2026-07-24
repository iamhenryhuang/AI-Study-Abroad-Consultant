"""
容器啟動時執行，確保 embedding 和 reranker model 已下載至 /models。
若 volume 裡已存在就跳過下載。
"""
from __future__ import annotations

import os
from pathlib import Path

MODELS_DIR = Path(os.getenv("HF_HOME", "/models"))
EMBED_MODEL_ID = "BAAI/bge-m3"
RERANKER_MODEL_ID = "BAAI/bge-reranker-v2-m3"


def _model_ready(model_id: str) -> bool:
    slug = model_id.replace("/", "--")
    hub_path = MODELS_DIR / "hub" / f"models--{slug}"
    return hub_path.exists()


def download_if_needed(model_id: str, is_cross_encoder: bool = False) -> None:
    if _model_ready(model_id):
        print(f"[download_models] 已存在，跳過：{model_id}")
        return
    print(f"[download_models] 下載中：{model_id} ...")
    if is_cross_encoder:
        from sentence_transformers import CrossEncoder
        CrossEncoder(model_id, trust_remote_code=True)
    else:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer(model_id, trust_remote_code=True)
    print(f"[download_models] 完成：{model_id}")


if __name__ == "__main__":
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    download_if_needed(EMBED_MODEL_ID, is_cross_encoder=False)
    download_if_needed(RERANKER_MODEL_ID, is_cross_encoder=True)
    print("[download_models] 所有 model 就緒。")
