"""本地确定性 Embedding（Phase 5 A：知识库语义化）。

v1 = 字符 n-gram 哈希 TF 向量：本地、零网络、零 API 费用、<10ms、确定性。
**绝不在热路径调用 API text-embedding**（延迟 500ms–1.5s + Token 成本失控）。
升级路径：v2 = fastembed 本地模型（如 bge-small-zh），见 docs 说明。

确定性关键：特征哈希用 hashlib.md5，**不用内置 hash()**（PYTHONHASHSEED 随机化会跨进程失效，
导致同一文本不同进程向量/repair_key 不同）。
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

from selfheal.config import Settings

# 词元：连续 ASCII 字母数字词 或 单个中文字符（CJK）
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


class EmbeddingClient:
    """文本向量化抽象。embed() 返回 float32 bytes（供 BLOB 存储与 numpy 余弦）。"""

    embedding_version = "base"

    def embed(self, text: str) -> bytes:
        """将文本编码为确定性 float32 向量（bytes）。"""
        raise NotImplementedError

    def to_vector(self, data: bytes) -> np.ndarray:
        """把存储的 bytes 还原为 numpy 向量（BLOB 快速载入，避免 JSON 反序列化）。"""
        return np.frombuffer(data, dtype=np.float32)


class NgramEmbedding(EmbeddingClient):
    """v1：字符 n-gram 哈希 TF 向量（确定性、本地、零费用）。"""

    embedding_version = "v1-ngram"

    def __init__(self, dim: int = 512, ngram_max: int = 2):
        self._dim = dim
        self._ngram_max = ngram_max

    def embed(self, text: str) -> bytes:
        tokens = _TOKEN_RE.findall(text or "")
        vec = np.zeros(self._dim, dtype=np.float32)
        for n in range(1, self._ngram_max + 1):
            for i in range(len(tokens) - n + 1):
                gram = "".join(tokens[i : i + n])
                idx = (
                    int.from_bytes(hashlib.md5(gram.encode("utf-8")).digest()[:4], "little")
                    % self._dim
                )
                vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32).tobytes()


def get_embedding_for_settings(settings: Settings) -> EmbeddingClient | None:
    """按配置构建可用 EmbeddingClient；不可用返回 None（语义策略降级跳过，零回归）。"""
    cfg = settings.embedding
    if not cfg.enabled:
        return None
    try:
        import numpy  # noqa: F401 - 需 numpy 才支持向量运算
    except ImportError:
        return None
    if cfg.method == "ngram":
        return NgramEmbedding(dim=cfg.dim)
    return None  # 未实现的方法（如 fastembed）暂不可用
