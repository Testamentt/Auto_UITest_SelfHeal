"""本地确定性 Embedding（Phase 5 A：知识库语义化）。

v1 = 字符 n-gram 哈希 TF 向量：本地、零网络、零 API 费用、<10ms、确定性。
**绝不在热路径调用 API text-embedding**（延迟 500ms–1.5s + Token 成本失控）。
升级路径：v2 = fastembed 本地模型（如 bge-small-zh），见 docs 说明。

确定性关键：特征哈希用 hashlib.md5，**不用内置 hash()**（PYTHONHASHSEED 随机化会跨进程失效，
导致同一文本不同进程向量/repair_key 不同）。

**不可比较契约（L6）**——向量能否参与余弦比较是有前提的，本模块把前提显式化：

- 归一化向量（‖v‖=1）才有定义良好的余弦；文本无有效词元（空串 / 纯空白 / 纯标点符号 /
  纯 emoji）时取不到任何 n-gram → 向量恒为零向量，余弦分母为 0 → 相似度是 ``nan``，
  排序结果无意义（既非命中亦非未命中，属**静默错误**）。
- 因此约定：**不可比较的 embedding 一律返回空 bytes（长度为 0）**，作为"不可比较"标记；
  它天然不可能等于维度字节数（``dim * 4 > 0``），故知识库侧既有的"长度不符即跳过"
  防御（`knowledge/store.py` / `sqlite_store.py` 的 ``len(embedding) == len(query_vec)``）
  会直接过滤，不会被写进向量索引，也不会污染检索。
- **调用方义务**：在把 query 向量交给 `find_semantic` 等余弦检索前，先用
  `EmbeddingClient.is_comparable()` 判定；返回 False 时应**短路该检索**（记 debug 后
  走下一层策略），而不是把空向量喂给知识库。需要"不可比较即显式失败"的场景用
  `EmbeddingClient.embed_vector()`（抛 ``ValueError``，避免静默降级）。

调用点现状：`agent/strategies/semantic.py::_knowledge_semantic` 与
`agent/persistence.py::_persist` 仍直接调用 `embed()`（本模块不越层修改 agent 层）。
`embed()` 保持宽容返回（空标记）以保证既有调用方继续可用：查询侧因长度不符自然无命中
（不误采纳），写入侧落零长度 BLOB（SQLite 存为 ``x''``，被 ``len(embedding)`` 长度校验
拦下；语义 NULL 为 None 时走 ``IS NOT NULL``）——**两种存储形态下该案例都不参与向量检索**，
与修复前"存了全零向量但余弦为 nan"相比，差别只是失败方式从静默 ``nan`` 变为明确跳过。
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

from selfheal.config import Settings

# 词元：连续 ASCII 字母数字词 或 **单个**中文字符（CJK）。
# 注意：与 agent/strategies/heuristic.py 的 _TOKEN_RE（连续中文片段）粒度不同是有意的——
# 本模块做 n-gram 序列向量，单字才能形成"提交/订单"等中文二元组；意图词元重叠用片段更语义化。
# 调整任一处分词时需评估另一处（审查 m7：两处重复定义，勿漂移）。
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")

# 向量存储契约：float32 小端、一维（BLOB 直存，numpy 零拷贝还原）。
VECTOR_DTYPE = np.float32
ITEM_SIZE = np.dtype(VECTOR_DTYPE).itemsize  # 单个分量的字节数（float32 为 4 字节）

# "不可比较"标记：长度 0 的 bytes（见模块 docstring 的"不可比较契约"）。
# 用空 bytes 而非全零向量：① 零向量仍会被误算余弦（nan）；② 空值天然被知识库的长度校验过滤。
UNCOMPARABLE = b""

# numpy 向量类型别名（对外签名可读；等价于 np.ndarray）。
Vector = np.ndarray


class EmbeddingClient:
    """文本向量化抽象。embed() 返回 float32 bytes（供 BLOB 存储与 numpy 余弦）。

    子类契约：
    - `dim`：向量维度（分量个数），`to_vector()` 据此校验；
    - `embed()`：**仅当返回值的 `is_comparable()` 为 True 时才可参与余弦检索**；
    - `embed_vector()`：严格版入口，不可比较输入直接抛 ``ValueError``。
    """

    embedding_version = "base"

    @property
    def dim(self) -> int:
        """向量维度（分量个数）。子类必须给出真实维度，用于存储数据的维度校验。"""
        raise NotImplementedError

    def embed(self, text: str) -> bytes:
        """将文本编码为确定性 float32 向量（bytes）。

        **不可比较约定（L6）**：文本无有效词元（空串 / 纯空白 / 纯标点 / 纯 emoji）时
        返回 `UNCOMPARABLE`（空 bytes），**不返回全零向量**——全零向量参与余弦会得到
        ``nan``，是静默错误。调用方在送入余弦检索前必须先 `is_comparable()` 判定并短路。
        """
        raise NotImplementedError

    def embed_vector(self, text: str) -> Vector:
        """严格版：把文本编码为可比较的归一化向量；不可比较时抛 ``ValueError``。

        供"宁可不检索也不静默降级"的调用方使用（R4：失败要么恢复、要么明确抛出）。
        返回的 ndarray 为只读视图语义之外的新数组，形状 ``(dim,)``、dtype ``float32``。
        """
        data = self.embed(text)
        if not self.is_comparable(data):
            raise ValueError(
                f"文本无有效词元，向量不可比较（无 n-gram 特征，余弦无定义）: {text!r}"
            )
        return self.to_vector(data)

    def is_comparable(self, data: bytes | None) -> bool:
        """判定存储的向量能否参与余弦比较：非空且按 float32 对齐。

        False 表示"不可比较"（空文本标记 / 空值 / 脏数据），调用方应短路余弦检索
        （如 `find_semantic`）。空 bytes 无法表示任何浮点分量，故非空即可判定可比较。
        """
        return bool(data) and len(data) % ITEM_SIZE == 0

    def to_vector(self, data: bytes) -> Vector:
        """把存储的 bytes 还原为 numpy 向量（BLOB 快速载入，避免 JSON 反序列化）。

        校验（L6）：空值 / 长度不是 `dim` 个 float32 / 未按 float32 对齐，全部抛
        ``ValueError`` 并说明期望字节数——此前直接 `np.frombuffer` 会让维度不符的脏数据
        一路流到 `matrix @ qv`，在那里炸成难以定位的 ``ValueError``（或静默给出错向量）。
        """
        if not data:
            raise ValueError("向量为空（不可比较标记或空值），无法还原为 numpy 向量")
        expected = self.dim * ITEM_SIZE
        if len(data) != expected:
            raise ValueError(
                f"向量字节数不符：期望 {expected}（dim={self.dim} × float32 {ITEM_SIZE} 字节），"
                f"实际 {len(data)}"
            )
        return np.frombuffer(data, dtype=VECTOR_DTYPE)


class NgramEmbedding(EmbeddingClient):
    """v1：字符 n-gram 哈希 TF 向量（确定性、本地、零费用）。"""

    def __init__(self, dim: int = 512, ngram_max: int = 2):
        self._dim = dim
        self._ngram_max = ngram_max
        # 版本号含 dim（审查 C3）：库中向量按 (page_fingerprint, embedding_version) 分桶，
        # 若版本不含 dim，用户调整 dim 后新旧向量同版本不同维度，find_semantic 的
        # numpy 矩阵乘法会直接 ValueError 崩溃。
        self.embedding_version = f"v1-ngram-{dim}"

    @property
    def dim(self) -> int:
        """向量维度（分量个数）；与 embedding_version 中记录的 dim 同源。"""
        return self._dim

    def embed(self, text: str) -> bytes:
        """n-gram 哈希 TF 向量（L2 归一化）；无有效词元时返回 `UNCOMPARABLE` 空标记。

        不可比较标记而非零向量：`""` / `"   "` / `"！！"` 这类文本零 n-gram，
        返回零向量会让下游余弦得到 ``nan``（见模块 docstring 的"不可比较契约"）。
        """
        tokens = _TOKEN_RE.findall(text or "")
        if not tokens:
            return UNCOMPARABLE
        vec = np.zeros(self._dim, dtype=VECTOR_DTYPE)
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
        return vec.astype(VECTOR_DTYPE).tobytes()


def get_embedding_for_settings(settings: Settings) -> EmbeddingClient | None:
    """按配置构建可用 EmbeddingClient；不可用返回 None（语义策略降级跳过，零回归）。

    method 已在加载期收敛为 `Literal["ngram", "fastembed"]` 且 fastembed 未实现即报错
    （见 config.EmbeddingConfig），故此处只剩 ngram 一条**可达**分支；末尾的
    `return None` 是防御性兜底（R4：绝不静默返回错实现）。
    """
    cfg = settings.embedding
    if not cfg.enabled:
        return None
    try:
        import numpy  # noqa: F401 - 需 numpy 才支持向量运算
    except ImportError:
        return None
    if cfg.method == "ngram":
        return NgramEmbedding(dim=cfg.dim)
    return None  # pragma: no cover - 受 Literal + 加载期校验保护，防御性兜底
