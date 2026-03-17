"""
重排序模組：使用 Cross-Encoder 對向量檢索結果進行精確評分。

模型路徑由 .env 中的 BGE_RERANKER_MODEL_PATH 決定，
預設為 D:\\DforDownload\\BAAI\\bge-reranker-v2-m3。
"""
from __future__ import annotations
import os
from pathlib import Path

from dotenv import load_dotenv
from sentence_transformers import CrossEncoder

load_dotenv()

# 從 .env 讀取路徑；若未設定則使用預設 D 槽路徑
_MODEL_PATH = Path(
    os.getenv("BGE_RERANKER_MODEL_PATH", r"D:\DforDownload\BAAI\bge-reranker-v2-m3")
)
_MODEL_NAME = _MODEL_PATH.name

_model: CrossEncoder | None = None

def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        model_id = str(_MODEL_PATH) if _MODEL_PATH.exists() else f"BAAI/{_MODEL_NAME}"
        if not _MODEL_PATH.exists():
            print(
                f"[reranker] 找不到本地模型 {_MODEL_PATH}，"
                f"改從 HuggingFace 下載 {_MODEL_NAME} ..."
            )
        else:
            print(f"[reranker] 載入本地重排序模型：{model_id}")
        
        # trust_remote_code=True 以前是必要的，現在 BGE v2 最好也帶著
        _model = CrossEncoder(model_id, trust_remote_code=True)
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
