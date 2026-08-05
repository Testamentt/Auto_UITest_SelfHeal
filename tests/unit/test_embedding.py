"""单元测试：本地确定性 Embedding（Phase 5 A1，不触网）。"""

import numpy as np
import pytest

from selfheal.config import Settings
from selfheal.llm.embedding import NgramEmbedding, get_embedding_for_settings

pytestmark = pytest.mark.unit


def _cosine(a: bytes, b: bytes) -> float:
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    return float(np.dot(va, vb))  # 向量已归一化


def test_embedding_deterministic():
    """同一文本向量跨调用一致（md5 特征哈希，非内置 hash()）。"""
    emb = NgramEmbedding()
    a = emb.embed("登录按钮")
    b = emb.embed("登录按钮")
    assert a == b


def test_embedding_normalized_unit_length():
    emb = NgramEmbedding()
    v = np.frombuffer(emb.embed("登录"), dtype=np.float32)
    assert abs(np.linalg.norm(v) - 1.0) < 1e-5


def test_similar_texts_high_cosine():
    emb = NgramEmbedding()
    v1 = emb.embed("登录 提交 确认")
    v2 = emb.embed("登录 提交 取消")
    v3 = emb.embed("注册 邮箱 验证码")
    assert _cosine(v1, v2) > _cosine(v1, v3)


def test_to_vector_roundtrip():
    emb = NgramEmbedding()
    data = emb.embed("确定")
    vec = emb.to_vector(data)
    assert vec.dtype == np.float32
    assert vec.shape == (emb._dim,)


def test_factory_returns_ngram_when_enabled():
    s = Settings()
    client = get_embedding_for_settings(s)
    assert isinstance(client, NgramEmbedding)
    assert client.embedding_version == "v1-ngram"


def test_factory_disabled_returns_none():
    s = Settings()
    s.embedding.enabled = False
    assert get_embedding_for_settings(s) is None
