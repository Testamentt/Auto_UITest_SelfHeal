"""单元测试：本地确定性 Embedding（Phase 5 A1，不触网）。"""

import numpy as np
import pytest

from selfheal.config import Settings
from selfheal.llm.embedding import (
    UNCOMPARABLE,
    NgramEmbedding,
    get_embedding_for_settings,
)

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
    assert client.embedding_version == "v1-ngram-512"  # 版本号含 dim（审查 C3）


def test_embedding_version_includes_dim():
    """审查 C3：版本号含维度，改 dim 后新旧向量不同版本（防 L3 维度崩溃）。"""
    assert NgramEmbedding(dim=512).embedding_version == "v1-ngram-512"
    assert NgramEmbedding(dim=256).embedding_version == "v1-ngram-256"
    assert NgramEmbedding(dim=512).embedding_version != NgramEmbedding(dim=256).embedding_version


def test_factory_disabled_returns_none():
    s = Settings()
    s.embedding.enabled = False
    assert get_embedding_for_settings(s) is None


# --- L6：零向量护栏（不可比较标记）+ to_vector 维度校验 ---


def test_embed_empty_text_returns_uncomparable_marker():
    """L6：空 / 纯空白 / 无词元文本返回空 bytes，**不再**产出全零向量（余弦 nan 的源头）。"""
    emb = NgramEmbedding()
    for text in ("", "   ", "\t\n", "！！！", "，。、", "🙂🙂"):
        data = emb.embed(text)
        assert data == UNCOMPARABLE == b""
        assert emb.is_comparable(data) is False


def test_zero_vector_cosine_is_nan_but_marker_short_circuits():
    """L6 回归证据：零向量的**余弦**（含 0/0 归一化）是 nan —— 静默错误；空标记可在检索前判定。"""
    emb = NgramEmbedding()
    zero = np.zeros(emb.dim, dtype=np.float32)
    real = np.frombuffer(emb.embed("登录"), dtype=np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        nan_cosine = float(np.dot(zero, real) / (np.linalg.norm(zero) * np.linalg.norm(real)))
    assert np.isnan(nan_cosine)  # 修复前 embed("") 就是这个输入（余弦未定义 → nan）
    assert emb.is_comparable(emb.embed("")) is False  # 修复后调用方可据此短路


def test_is_comparable_accepts_real_vector_and_rejects_dirty_data():
    """L6：判定口径——非空且按 float32 对齐为可比较；空值 / 半截字节为不可比较。"""
    emb = NgramEmbedding()
    assert emb.is_comparable(emb.embed("登录按钮")) is True
    assert emb.is_comparable(b"") is False
    assert emb.is_comparable(None) is False
    assert emb.is_comparable(b"\x00\x00\x00") is False  # 3 字节：不是 float32 的整数倍


def test_embed_vector_raises_on_uncomparable_text():
    """L6：严格入口 embed_vector 对不可比较文本显式抛错（R4：不静默降级为 nan）。"""
    emb = NgramEmbedding()
    vec = emb.embed_vector("登录按钮")
    assert vec.shape == (emb.dim,)
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5
    for text in ("", "   ", "。"):
        with pytest.raises(ValueError, match="不可比较"):
            emb.embed_vector(text)


def test_to_vector_rejects_empty_payload():
    """L6：空载荷（不可比较标记 / 空值）不再返回空数组，而是明确抛错。"""
    with pytest.raises(ValueError, match="向量为空"):
        NgramEmbedding().to_vector(b"")


def test_to_vector_rejects_wrong_dimension():
    """L6：维度不符给出可定位的错误信息（期望字节数 + 实际字节数），而非下游矩阵乘法崩溃。"""
    emb = NgramEmbedding(dim=8)
    with pytest.raises(ValueError, match="期望 32"):
        emb.to_vector(np.zeros(9, dtype=np.float32).tobytes())
    with pytest.raises(ValueError, match="期望 32"):
        emb.to_vector(np.zeros(4, dtype=np.float32).tobytes())


def test_to_vector_rejects_unaligned_bytes():
    """L6：字节数不是 float32 整数倍（脏数据）明确抛错，不静默截断。"""
    emb = NgramEmbedding(dim=2)
    with pytest.raises(ValueError, match="实际 7"):
        emb.to_vector(b"\x00" * 7)


def test_embedding_version_roundtrip_survives_dim_check():
    """L6：正常往返不受新校验影响（既有 C3 契约：版本号含 dim）。"""
    emb = NgramEmbedding(dim=16)
    data = emb.embed("提交订单")
    vec = emb.to_vector(data)
    assert vec.shape == (16,)
    assert emb.dim == 16
    assert emb.embedding_version == "v1-ngram-16"
