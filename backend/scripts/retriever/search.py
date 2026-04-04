# search.py — backward-compatibility shim
# 實際邏輯已拆分至：
#   retriever/hybrid_search.py           （向量 + FTS 混合檢索）
#   retriever/alternative_recommendation.py （備案學校推薦）
from retriever.hybrid_search import (
    _build_filter_clauses,
    _execute_hybrid_search,
    search_core,
    run_search,
)
from retriever.alternative_recommendation import (
    _EXP_KEY_TO_SCHOOL_ID,
    search_alternative,
)

__all__ = [
    "_build_filter_clauses",
    "_execute_hybrid_search",
    "search_core",
    "run_search",
    "_EXP_KEY_TO_SCHOOL_ID",
    "search_alternative",
]
