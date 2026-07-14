import os
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


class TestGetModel(unittest.TestCase):
    def setUp(self):
        vectorize._model = None

    def tearDown(self):
        vectorize._model = None

    def test_get_model_passes_trust_remote_code(self):
        # BAAI/bge-m3 在較新版本的 sentence-transformers 下，缺少
        # trust_remote_code=True 會導致模型模組載入失敗（self[0] 變成 None）。
        with patch("sentence_transformers.SentenceTransformer") as mock_cls:
            vectorize._get_model()
        _, kwargs = mock_cls.call_args
        self.assertTrue(kwargs.get("trust_remote_code"))

    def test_get_model_treats_empty_env_var_as_unset(self):
        # .env 裡 `BGE_EMBED_MODEL_PATH=`（等號後空白）會讓 python-dotenv
        # 把該變數設成空字串，而非「不存在」——os.getenv(key, default) 的
        # default 只在 key 完全不存在時生效，空字串會直接被當成值回傳，
        # 導致 SentenceTransformer("") 被呼叫、模型模組載入失敗。
        old = os.environ.get("BGE_EMBED_MODEL_PATH")
        os.environ["BGE_EMBED_MODEL_PATH"] = ""
        try:
            with patch("sentence_transformers.SentenceTransformer") as mock_cls:
                vectorize._get_model()
            args, kwargs = mock_cls.call_args
            model_path = args[0] if args else kwargs.get("model_name_or_path")
            self.assertEqual(model_path, "BAAI/bge-m3")
        finally:
            if old is None:
                os.environ.pop("BGE_EMBED_MODEL_PATH", None)
            else:
                os.environ["BGE_EMBED_MODEL_PATH"] = old


if __name__ == "__main__":
    unittest.main()
