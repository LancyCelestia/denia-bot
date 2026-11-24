import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'rag'))
import serve_cloud
from fastapi import HTTPException


class EmbedTest(unittest.TestCase):
    def test_cloud_embed_bounds_input_and_uses_configured_embedder(self):
        class FakeEmbedder:
            def encode(self, texts):
                return np.array([[1.0, 0.0]])

        with patch.dict(serve_cloud.state, {'emb': FakeEmbedder()}):
            self.assertEqual(serve_cloud.embed(serve_cloud.EmbedReq(text='hello'))['vector'], [1.0, 0.0])
            with self.assertRaises(HTTPException):
                serve_cloud.embed(serve_cloud.EmbedReq(text=' '))
            with self.assertRaises(HTTPException):
                serve_cloud.embed(serve_cloud.EmbedReq(text='x' * 2001))


if __name__ == '__main__':
    unittest.main()
