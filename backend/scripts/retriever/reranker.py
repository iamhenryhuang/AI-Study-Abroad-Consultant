"""BGE-reranker-v2-m3 CrossEncoder 重排序（hybrid search 的第二階段）。

自 backup/AI-Study-Abroad-Consultant-1.0.0 移植；CrossEncoder 延遲 import，
套件或模型不可用時由呼叫端（hybrid_search）捕捉例外、退回 RRF 排序結果。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_model = None


def _sanitize_ssl_env() -> None:
    """SSL_CERT_FILE 指向不存在的檔案時會讓模型下載直接 crash，先行清掉。"""
    cert_file = os.getenv("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).exists():
        print(f"[reranker] SSL_CERT_FILE 無效，已忽略：{cert_file}")
        os.environ.pop("SSL_CERT_FILE", None)


def _resolve_model_id() -> str:
    """本地路徑存在就用本地（含 HF hub 快取的 snapshots/ 解析），否則用 hub id 下載。"""
    raw = os.getenv("BGE_RERANKER_MODEL_PATH", "")
    if not raw:
        return "BAAI/bge-reranker-v2-m3"
    model_path = Path(raw)
    if not model_path.exists():
        print(f"[reranker] 找不到本地模型 {model_path}，改從 HuggingFace 下載 ...")
        return "BAAI/bge-reranker-v2-m3"
    snapshots_dir = model_path / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted(snapshots_dir.iterdir())
        if snapshots:
            resolved = snapshots[-1]
            print(f"[reranker] 偵測到 HF hub 快取，使用 snapshot：{resolved}")
            return str(resolved)
    print(f"[reranker] 載入本地重排序模型：{model_path}")
    return str(model_path)


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        _sanitize_ssl_env()
        _model = CrossEncoder(_resolve_model_id(), trust_remote_code=True)
    return _model


def rerank(query: str, documents: list[dict], top_n: int = 5) -> list[dict]:
    """對含 chunk_text 的文件重排序，回傳前 top_n 筆（附 rerank_score，降冪）。"""
    if not documents:
        return []
    model = _get_model()
    pairs = [(query, doc["chunk_text"]) for doc in documents]
    scores = model.predict(pairs)
    for doc, score in zip(documents, scores):
        doc["rerank_score"] = float(score)
    ranked = sorted(documents, key=lambda d: d["rerank_score"], reverse=True)
    return ranked[:top_n]
