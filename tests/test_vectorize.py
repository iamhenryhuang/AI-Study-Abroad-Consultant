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
