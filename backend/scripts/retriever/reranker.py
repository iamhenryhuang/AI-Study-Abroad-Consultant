from __future__ import annotations
import os
from pathlib import Path

from dotenv import load_dotenv
from sentence_transformers import CrossEncoder

load_dotenv()

# 從 .env 讀取路徑；若未設定則使用預設 D 槽路徑
_MODEL_PATH = Path(
    os.getenv("BGE_RERANKER_MODEL_PATH", r"C:\Users\Henry\.cache\huggingface\hub\models--BAAI--bge-reranker-v2-m3")
)
_MODEL_NAME = _MODEL_PATH.name

_model: CrossEncoder | None = None


def _sanitize_ssl_env() -> None:
    """Avoid crashes when SSL cert env points to a non-existent file."""
    cert_file = os.getenv("SSL_CERT_FILE")
    if cert_file and not Path(cert_file).exists():
        print(f"[reranker] SSL_CERT_FILE 無效，已忽略：{cert_file}")
        os.environ.pop("SSL_CERT_FILE", None)

def _resolve_model_id() -> str:
    """
    解析模型路徑。若指向 HuggingFace hub 快取目錄（含 snapshots/），
    自動找到 snapshot 資料夾裡的實際模型；否則直接使用該路徑。
    本地路徑不存在時改用 HuggingFace model ID 線上下載。
    """
    if not _MODEL_PATH.exists():
        print(f"[reranker] 找不到本地模型 {_MODEL_PATH}，改從 HuggingFace 下載 ...")
        return f"BAAI/{_MODEL_NAME}"

    snapshots_dir = _MODEL_PATH / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted(snapshots_dir.iterdir())
        if snapshots:
            resolved = snapshots[-1]
            print(f"[reranker] 偵測到 HF hub 快取，使用 snapshot：{resolved}")
            return str(resolved)

    print(f"[reranker] 載入本地重排序模型：{_MODEL_PATH}")
    return str(_MODEL_PATH)


def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        _sanitize_ssl_env()
        _model = CrossEncoder(_resolve_model_id(), trust_remote_code=True)
    return _model

def rerank(query: str, documents: list[dict], top_n: int = 5) -> list[dict]:
    """
    對文件列表進行重排序。
    
    Args:
        query: 使用者提問
        documents: 待排序的文件列表，每個元素須包含 'chunk_text' 鍵
        top_n: 最終返回的結果數量
        
    Returns:
        排序後的文件列表，包含 'rerank_score'
    """
    if not documents:
        return []
    
    model = _get_model()
    
    # 準備 Cross-Encoder 的輸入：[(query, doc1), (query, doc2), ...]
    # 注意：這裡假設 documents 裡面的文字鍵值是 'chunk_text' (符合 search.py 的 select)
    pairs = [(query, doc["chunk_text"]) for doc in documents]
    
    # 計算分數 (Cross-Encoder 分數通常越高越相關)
    scores = model.predict(pairs)
    
    # 將分數分配回文件
    for i, score in enumerate(scores):
        documents[i]["rerank_score"] = float(score)
    
    # 由高到低排序
    ranked_docs = sorted(documents, key=lambda x: x["rerank_score"], reverse=True)
    
    return ranked_docs[:top_n]
