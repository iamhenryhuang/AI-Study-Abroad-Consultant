# Hybrid Search Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 agent 的文本檢索 fallback 從純 FTS 升級為「向量(BGE-M3)+FTS 的 RRF 混合 → BGE CrossEncoder 重排序」，任一環節不可用時自動降級回純 FTS。

**Architecture:** 三個新模組放在 `backend/scripts/retriever/`（`vectorize.py` 查詢端 embedding、`reranker.py` CrossEncoder 重排序、`hybrid_search.py` RRF 混合檢索 + 降級 facade）。`agent.py` 的 `_fulltext_one_query` 改呼叫 facade，LangGraph 圖拓撲不動。Spec：`docs/superpowers/specs/2026-07-11-hybrid-search-fallback-design.md`。

**Tech Stack:** PostgreSQL + pgvector（HNSW）、psycopg3、sentence-transformers（BGE-M3 / BGE-reranker-v2-m3）、unittest。

## Global Constraints

- 不改 `requirements.txt`（`sentence-transformers>=5.0` 已在）、不改 `db/init_db.sql`、不改 `fulltext_search.py` / `applicant_search.py` / `sql_search.py`、不改 LangGraph 圖結構。
- `sentence_transformers` 一律在函式內延遲 import（模組頂層 import 會拖慢 agent 啟動，且套件缺失時要能降級）。
- DB 連線一律 `conn.read_only = True`（與現行檢索模組統一）。
- 回傳 doc 形狀必須含 `chunk_text` / `source_url` / `school_id` / `university_name`（對齊 `fulltext_search`，下游零改動）。
- SQL 的 `vector_matches` CTE 必須有 `WHERE embedding IS NOT NULL`。
- 向量以 pgvector 文字字面值傳遞（`'[0.1,0.2,...]'::vector`），避免 psycopg 型別轉接問題。
- 測試 import 路徑：`sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))` 後以 `from retriever.xxx import ...` 匯入。
- 測試框架：unittest（跟隨 `tests/test_gradcafe.py` 的 FakeConn/FakeCursor 慣例）。

---

### Task 1: vectorize.py — 查詢端 embedding

**Files:**
- Create: `backend/scripts/retriever/vectorize.py`
- Test: `tests/test_vectorize.py`

**Interfaces:**
- Consumes: env `BGE_EMBED_MODEL_PATH`（未設定時 `"BAAI/bge-m3"`）
- Produces: `embed_query(text: str) -> list[float]`（1024 維、L2 normalized；Task 3 的 `hybrid_search` 呼叫）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vectorize.py
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import vectorize


class FakeArray:
    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return list(self._values)


class FakeModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False):
        self.calls.append({
            "texts": texts,
            "normalize_embeddings": normalize_embeddings,
        })
        return [FakeArray([0.1, 0.2, 0.3]) for _ in texts]


class TestEmbedQuery(unittest.TestCase):
    def test_embed_query_returns_vector_list(self):
        fake = FakeModel()
        with patch.object(vectorize, "_get_model", return_value=fake):
            vec = vectorize.embed_query("CMU CS admission")
        self.assertEqual(vec, [0.1, 0.2, 0.3])
        # 查詢端必須與寫入端一致做 normalize，餘弦距離才有意義
        self.assertTrue(fake.calls[0]["normalize_embeddings"])
        self.assertEqual(fake.calls[0]["texts"], ["CMU CS admission"])

    def test_embed_query_empty_text_returns_empty(self):
        with patch.object(vectorize, "_get_model") as mock_get:
            vec = vectorize.embed_query("   ")
        self.assertEqual(vec, [])
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_vectorize -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'retriever.vectorize'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/retriever/vectorize.py
"""查詢端 embedding：BGE-M3（1024 維），供 hybrid_search 把使用者問題轉向量。

與 data_crawler/backfill_embeddings.py 共用 env BGE_EMBED_MODEL_PATH，
模型只需下載/存放一份。sentence_transformers 延遲 import：
套件或模型不可用時在呼叫端（hybrid_search facade）統一降級純 FTS。
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        model_path = os.getenv("BGE_EMBED_MODEL_PATH", "BAAI/bge-m3")
        print(f"[vectorize] 載入 embedding 模型：{model_path}")
        _model = SentenceTransformer(model_path)
    return _model


def embed_query(text: str) -> list[float]:
    """將單一查詢字串轉成 1024 維向量；空字串回傳 []。"""
    if not text or not text.strip():
        return []
    embeddings = _get_model().encode(
        [text],
        batch_size=8,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return embeddings[0].tolist()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_vectorize -v`
Expected: PASS（2 tests OK）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/retriever/vectorize.py tests/test_vectorize.py
git commit -m "feat: add query-side BGE-M3 embedding for hybrid search"
```

---

### Task 2: reranker.py — BGE CrossEncoder 重排序

**Files:**
- Create: `backend/scripts/retriever/reranker.py`
- Test: `tests/test_reranker.py`

**Interfaces:**
- Consumes: env `BGE_RERANKER_MODEL_PATH`（未設定時 `"BAAI/bge-reranker-v2-m3"`）
- Produces: `rerank(query: str, documents: list[dict], top_n: int = 5) -> list[dict]`——輸入文件需含 `chunk_text`，輸出依 `rerank_score` 降冪、附 `rerank_score` 欄位（Task 3 呼叫）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reranker.py
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import reranker


class FakeCrossEncoder:
    """依 chunk_text 開頭數字給分，方便驗證排序。"""

    def predict(self, pairs):
        return [float(doc.split(":")[0]) for _, doc in pairs]


class TestRerank(unittest.TestCase):
    def test_rerank_sorts_by_score_desc_and_truncates(self):
        docs = [
            {"chunk_text": "1:low"},
            {"chunk_text": "9:high"},
            {"chunk_text": "5:mid"},
        ]
        with patch.object(reranker, "_get_model", return_value=FakeCrossEncoder()):
            ranked = reranker.rerank("q", docs, top_n=2)
        self.assertEqual([d["chunk_text"] for d in ranked], ["9:high", "5:mid"])
        self.assertEqual(ranked[0]["rerank_score"], 9.0)

    def test_rerank_empty_documents_returns_empty(self):
        with patch.object(reranker, "_get_model") as mock_get:
            self.assertEqual(reranker.rerank("q", [], top_n=5), [])
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_reranker -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'retriever.reranker'`

- [ ] **Step 3: Write minimal implementation**

從 backup 移植，保留 HF hub snapshot 解析與 SSL env 清理，改為延遲 import：

```python
# backend/scripts/retriever/reranker.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_reranker -v`
Expected: PASS（2 tests OK）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/retriever/reranker.py tests/test_reranker.py
git commit -m "feat: port BGE cross-encoder reranker from backup"
```

---

### Task 3: hybrid_search.py — RRF 混合檢索核心

**Files:**
- Create: `backend/scripts/retriever/hybrid_search.py`
- Test: `tests/test_hybrid_search.py`

**Interfaces:**
- Consumes: `retriever.vectorize.embed_query(text) -> list[float]`（Task 1）、`retriever.reranker.rerank(query, documents, top_n) -> list[dict]`（Task 2）、`db.connection.get_connection()`
- Produces: `hybrid_search(query: str, school_id: str | None = None, limit: int = 5, use_rerank: bool = True) -> list[dict]`——doc 含 `chunk_text` / `source_url` / `school_id` / `university_name` / `vector_score` / `fts_score` / `rrf_score`（+ rerank 時的 `rerank_score`）。**查詢/連線失敗一律 raise**（讓 Task 4 的 facade 降級），只有「查無資料」才回傳 `[]`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hybrid_search.py
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import hybrid_search as hs


class FakeCursor:
    def __init__(self, rows, capture):
        self._rows = rows
        self._capture = capture
        self.description = [
            ("chunk_text",), ("source_url",), ("school_id",), ("university_name",),
            ("vector_score",), ("fts_score",), ("rrf_score",),
        ]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self._capture.append((sql, params))

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.captured = []
        self.read_only = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self.rows, self.captured)

    def close(self):
        self.closed = True


_ROW = ("some text", "http://u", "cmu", "Carnegie Mellon University", 0.9, 0.5, 0.03)


class TestHybridSearch(unittest.TestCase):
    def _run(self, conn, **kwargs):
        with patch.object(hs, "get_connection", return_value=conn), \
             patch.object(hs, "embed_query", return_value=[0.1] * 4), \
             patch.object(hs, "rerank", side_effect=lambda q, docs, top_n: docs[:top_n]):
            return hs.hybrid_search("cmu deadline", **kwargs)

    def test_sql_filters_null_embeddings_and_returns_doc_shape(self):
        conn = FakeConn([_ROW])
        docs = self._run(conn)
        sql, params = conn.captured[0]
        self.assertIn("embedding IS NOT NULL", sql)
        self.assertIn("websearch_to_tsquery('simple'", sql)
        # 向量以 pgvector 文字字面值傳遞
        self.assertTrue(str(params["vec"]).startswith("["))
        # doc 形狀對齊 fulltext_search，下游零改動
        for key in ("chunk_text", "source_url", "school_id", "university_name"):
            self.assertIn(key, docs[0])
        self.assertTrue(conn.read_only)
        self.assertTrue(conn.closed)

    def test_school_id_filter_added_to_both_ctes(self):
        conn = FakeConn([_ROW])
        self._run(conn, school_id="cmu")
        sql, params = conn.captured[0]
        self.assertEqual(sql.count("school_id = %(school_id)s"), 2)
        self.assertEqual(params["school_id"], "cmu")

    def test_rerank_failure_falls_back_to_rrf_order(self):
        # 3 筆候選、limit=2：len(candidates) > limit 才會觸發 rerank
        rows = [
            ("text a", "http://a", "cmu", "CMU", 0.9, 0.5, 0.05),
            ("text b", "http://b", "cmu", "CMU", 0.8, 0.4, 0.04),
            ("text c", "http://c", "cmu", "CMU", 0.7, 0.3, 0.03),
        ]
        conn = FakeConn(rows)
        with patch.object(hs, "get_connection", return_value=conn), \
             patch.object(hs, "embed_query", return_value=[0.1] * 4), \
             patch.object(hs, "rerank", side_effect=RuntimeError("model missing")) as mock_rr:
            docs = hs.hybrid_search("cmu deadline", limit=2)
        mock_rr.assert_called_once()
        # 降級後保留 RRF 排序、截前 limit 筆
        self.assertEqual([d["chunk_text"] for d in docs], ["text a", "text b"])

    def test_query_error_raises_for_facade_to_degrade(self):
        class BrokenConn(FakeConn):
            def cursor(self):
                raise RuntimeError("db down")

        conn = BrokenConn([])
        with patch.object(hs, "get_connection", return_value=conn), \
             patch.object(hs, "embed_query", return_value=[0.1] * 4):
            with self.assertRaises(RuntimeError):
                hs.hybrid_search("cmu deadline")
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_hybrid_search -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'retriever.hybrid_search'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/retriever/hybrid_search.py
"""向量 + FTS 的 RRF 混合檢索（agent 文本檢索 fallback 的升級版）。

自 backup/AI-Study-Abroad-Consultant-1.0.0 移植，三處適配：
  1. vector_matches CTE 加 WHERE embedding IS NOT NULL
     （pipeline 預設 ENABLE_EMBEDDING=off，存在 NULL 向量，不濾會污染排名）
  2. conn.read_only = True（與現行檢索模組統一）
  3. doc 形狀對齊 fulltext_search（chunk_text/source_url/school_id/university_name）

錯誤語意：查詢/連線/embedding 失敗一律 raise（由 hybrid_search_with_fallback
降級純 FTS）；只有真的查無資料才回傳 []。reranker 失敗例外：退回 RRF 排序。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.connection import get_connection
from retriever.vectorize import embed_query
from retriever.reranker import rerank

# RRF 常數：backup 沿用學界慣例 k=60
_RRF_K = 60


def _to_pgvector_literal(vec: list[float]) -> str:
    """轉成 pgvector 文字字面值 '[0.1,0.2,...]'，避免 psycopg 型別轉接問題。"""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def _build_sql(school_id: str | None, initial_k: int) -> str:
    school_filter = "AND school_id = %(school_id)s" if school_id else ""
    return f"""
        WITH vector_matches AS (
            SELECT id,
                   1 - (embedding <=> %(vec)s::vector) AS vector_score,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
            FROM document_chunks
            WHERE embedding IS NOT NULL
              {school_filter}
            ORDER BY embedding <=> %(vec)s::vector
            LIMIT {initial_k}
        ),
        keyword_matches AS (
            SELECT id,
                   ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %(q)s)) AS fts_score,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery('simple', %(q)s)) DESC
                   ) AS rank
            FROM document_chunks
            WHERE fts_vector @@ websearch_to_tsquery('simple', %(q)s)
              {school_filter}
            LIMIT {initial_k}
        )
        SELECT dc.chunk_text,
               dc.source_url,
               dc.school_id,
               u.name AS university_name,
               COALESCE(vm.vector_score, 0) AS vector_score,
               COALESCE(km.fts_score, 0)    AS fts_score,
               (COALESCE(1.0 / ({_RRF_K} + vm.rank), 0)
                + COALESCE(1.0 / ({_RRF_K} + km.rank), 0)) AS rrf_score
        FROM document_chunks dc
        LEFT JOIN universities u ON u.school_id = dc.school_id
        LEFT JOIN vector_matches vm ON dc.id = vm.id
        LEFT JOIN keyword_matches km ON dc.id = km.id
        WHERE vm.id IS NOT NULL OR km.id IS NOT NULL
        ORDER BY rrf_score DESC
        LIMIT {initial_k}
    """


def hybrid_search(
    query: str,
    school_id: str | None = None,
    limit: int = 5,
    use_rerank: bool = True,
) -> list[dict]:
    """RRF 混合檢索 → CrossEncoder 重排序，回傳前 limit 筆 doc。

    embedding 全部未 backfill 時 vector_matches 為空，RRF 自然退化為
    FTS-only 排名，仍可運作；reranker 失敗則退回 RRF 排序結果。
    """
    vec = embed_query(query)
    if not vec:
        return []

    conn = get_connection()
    if not conn:
        raise ConnectionError("[HybridSearch] 無法取得資料庫連線")

    conn.read_only = True
    initial_k = limit * 2

    try:
        with conn.cursor() as cur:
            params = {"vec": _to_pgvector_literal(vec), "q": query}
            if school_id:
                params["school_id"] = school_id
            cur.execute(_build_sql(school_id, initial_k), params)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    candidates = [dict(zip(columns, row)) for row in rows]
    candidates = [c for c in candidates if c.get("chunk_text")]
    if not candidates:
        return []

    if use_rerank and len(candidates) > limit:
        try:
            return rerank(query, candidates, top_n=limit)
        except Exception as e:
            print(f"[HybridSearch] 重排序失敗，改用 RRF 排序：{e}")

    return candidates[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_hybrid_search -v`
Expected: PASS（4 tests OK；`test_rerank_failure` 需確認 `mock_rr.assert_called_once()` 有被執行到）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/retriever/hybrid_search.py tests/test_hybrid_search.py
git commit -m "feat: add RRF hybrid search with rerank for fallback retrieval"
```

---

### Task 4: 降級 facade + agent 接線 + 文件

**Files:**
- Modify: `backend/scripts/retriever/hybrid_search.py`（檔尾加 facade）
- Modify: `backend/scripts/retriever/agent.py:499-521`（`_fulltext_one_query`）
- Modify: `.env.example`
- Modify: `backend/README.md`（fallback 說明段落）
- Test: `tests/test_hybrid_search.py`（追加 facade 測試）

**Interfaces:**
- Consumes: `hybrid_search(query, school_id, limit, use_rerank)`（Task 3）、`retriever.fulltext_search.fulltext_search(query, school_id, limit) -> list[dict]`（既有）
- Produces: `hybrid_search_with_fallback(query: str, school_id: str | None = None, limit: int = 5) -> list[dict]`——**永不 raise**；hybrid 任何例外都降級純 FTS 並印出原因（agent 唯一入口）

- [ ] **Step 1: Write the failing test（追加到 tests/test_hybrid_search.py）**

```python
# 追加到 tests/test_hybrid_search.py
class TestHybridSearchWithFallback(unittest.TestCase):
    def test_returns_hybrid_results_when_available(self):
        docs = [{"chunk_text": "hybrid doc"}]
        with patch.object(hs, "hybrid_search", return_value=docs), \
             patch.object(hs, "fulltext_search") as mock_fts:
            result = hs.hybrid_search_with_fallback("q", school_id="cmu")
        self.assertEqual(result, docs)
        mock_fts.assert_not_called()

    def test_degrades_to_fulltext_on_hybrid_failure(self):
        fts_docs = [{"chunk_text": "fts doc"}]
        with patch.object(hs, "hybrid_search", side_effect=ImportError("no sentence_transformers")), \
             patch.object(hs, "fulltext_search", return_value=fts_docs) as mock_fts:
            result = hs.hybrid_search_with_fallback("q", school_id="cmu", limit=5)
        self.assertEqual(result, fts_docs)
        mock_fts.assert_called_once_with("q", school_id="cmu", limit=5)

    def test_hybrid_empty_result_is_not_degraded(self):
        # 查無資料是合法結果（≠故障），不應觸發降級
        with patch.object(hs, "hybrid_search", return_value=[]), \
             patch.object(hs, "fulltext_search") as mock_fts:
            result = hs.hybrid_search_with_fallback("q")
        self.assertEqual(result, [])
        mock_fts.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_hybrid_search -v`
Expected: 新增 3 個測試 ERROR with `AttributeError: ... has no attribute 'hybrid_search_with_fallback'`

- [ ] **Step 3: Write minimal implementation（追加到 hybrid_search.py 檔尾）**

```python
# 追加到 backend/scripts/retriever/hybrid_search.py 檔尾
from retriever.fulltext_search import fulltext_search


def hybrid_search_with_fallback(
    query: str,
    school_id: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """agent fallback 唯一入口：先試 hybrid，任何故障降級純 FTS，永不 raise。

    「查無資料」回傳 []（合法結果，不降級）；「故障」（模型未下載、
    sentence_transformers 缺失、DB 錯誤）才降級，行為與升級前純 FTS 相同。
    """
    try:
        return hybrid_search(query, school_id=school_id, limit=limit)
    except Exception as e:
        print(f"[HybridSearch] 混合檢索不可用（{type(e).__name__}: {e}），降級純 FTS")
        return fulltext_search(query, school_id=school_id, limit=limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_hybrid_search -v`
Expected: PASS（7 tests OK）

- [ ] **Step 5: agent.py 接線**

`backend/scripts/retriever/agent.py` 兩處修改：

import 區（第 41 行附近），把

```python
from retriever.fulltext_search import fulltext_search
```

改成

```python
from retriever.hybrid_search import hybrid_search_with_fallback
```

`_fulltext_one_query`（第 499-521 行），把

```python
    results = fulltext_search(q, school_id=school_id)
```

改成

```python
    results = hybrid_search_with_fallback(q, school_id=school_id)
```

其餘（`_emit` 事件、`school_id` 偵測、`item["query"] = q` 迴圈）全部不動。

- [ ] **Step 6: 驗證 agent 模組可正常 import**

Run: `python -c "import sys; sys.path.insert(0, 'backend/scripts'); from retriever.agent import run_agent; print('OK')"`
Expected: 印出 `OK`（無 ImportError；sentence_transformers 是延遲 import，此時不會載入模型）

- [ ] **Step 7: 更新 .env.example**

在 SerpAPI 段落後追加：

```bash
# ── 向量檢索（選配）──
# hybrid search fallback 用的模型路徑；未設定時直接用 HuggingFace model id 線上下載。
# 模型未下載 / sentence-transformers 缺失時自動降級純 FTS，不影響既有功能。
# 向量生效前需先跑：python -m data_crawler.backfill_embeddings
BGE_EMBED_MODEL_PATH=
BGE_RERANKER_MODEL_PATH=
```

- [ ] **Step 8: 更新 backend/README.md**

找到描述 fulltext fallback 的段落（`fulltext_search`／「全文檢索」相關文字），將 fallback 說明更新為：

```markdown
SQL 檢索不足時的文本 fallback 已升級為混合檢索（`retriever/hybrid_search.py`）：
BGE-M3 向量 + FTS 以 RRF（k=60）融合，再經 BGE-reranker-v2-m3 重排序。
模型未下載或 embedding 未 backfill 時自動降級純 FTS（`retriever/fulltext_search.py`），
行為與升級前相同。向量檢索生效前置條件：`python -m data_crawler.backfill_embeddings`。
```

- [ ] **Step 9: 跑全部測試**

Run: `python -m unittest discover tests -v`
Expected: 全部 PASS（含既有 test_gradcafe）

- [ ] **Step 10: Commit**

```bash
git add backend/scripts/retriever/hybrid_search.py backend/scripts/retriever/agent.py tests/test_hybrid_search.py .env.example backend/README.md
git commit -m "feat: wire hybrid search fallback into agent with FTS degradation"
```
