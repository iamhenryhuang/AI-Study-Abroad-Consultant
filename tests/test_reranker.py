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
