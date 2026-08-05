"""确定性指纹子模块（A5 拆包）——DOM / 页面 / repair_key 三个哈希。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。
全部用 hashlib（弃用内置 hash()，避免 PYTHONHASHSEED 跨进程随机化破坏确定性）。
"""

from __future__ import annotations

import hashlib

from selfheal.agent.dom.parser import parse_interactive_elements
from selfheal.agent.dom.selector_builder import build_stable_selector


def _url_path(url: str) -> str:
    """取 URL 的路径部分（去 query/fragment），作为页面隔离的一部分。"""
    from urllib.parse import urlparse

    return urlparse(url).path or url or ""


def dom_fingerprint(dom: str | None) -> str | None:
    """计算 DOM 指纹：可交互元素稳定定位器排序后的哈希。

    用于知识库相似度匹配——同一页面结构（可交互元素集合一致）指纹相同，
    使修复案例可在"同结构页面"上可靠复用。无可交互元素时返回 None。
    """
    if not dom:
        return None
    selectors = sorted(
        s for el in parse_interactive_elements(dom) if (s := build_stable_selector(el))
    )
    if not selectors:
        return None
    return hashlib.sha1("\n".join(selectors).encode("utf-8")).hexdigest()


def compute_page_fingerprint(url: str, dom: str | None) -> str:
    """页面指纹 = md5(URL 路径 + DOM 结构指纹)——跨页面隔离（防"张冠李戴"误修复）。

    确定性：md5（非内置 hash()，避免跨进程随机化）。
    """
    dom_fp = dom_fingerprint(dom) or ""
    raw = f"{_url_path(url)}\n{dom_fp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def compute_repair_key(page_fingerprint: str, tag_path: str) -> str:
    """L1 精确命中键 = md5(page_fingerprint|tag_path)，带 v2 版本前缀。

    v2 变更（2026-08-05 决策）：
    - tag_path 含 nth-of-type 索引（如 html>body>div:nth-of-type(2)>button:nth-of-type(1)），
      区分同路径兄弟，防 L1 键碰撞；
    - 不再包含元素文本——文案变化（"提交订单"→"提交"）不破键；结构变动 → L1 miss 交 L2 补位。
    版本前缀（v2:）防止旧库（v1 含 text 的键）残留误命中。
    """
    raw = f"{page_fingerprint}|{tag_path}"
    return "v2:" + hashlib.md5(raw.encode("utf-8")).hexdigest()
