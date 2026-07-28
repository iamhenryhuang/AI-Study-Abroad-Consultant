import os
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


class TestHybridSearchWithFallback(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ, {"ENABLE_HYBRID_SEARCH": "true"})
        self._env.start()

    def tearDown(self):
        self._env.stop()

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


if __name__ == "__main__":
    unittest.main()
