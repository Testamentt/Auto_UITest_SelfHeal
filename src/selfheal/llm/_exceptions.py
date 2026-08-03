"""LLM 层异常定义。

UnavailableError：模型能力不可用（SDK 缺失 / 网络失败 / 返回空内容等）。
独立成文件以避免 openai_client / factory / 调用方之间循环 import。
"""


class UnavailableError(Exception):
    """模型能力不可用。上层（orchestrator / 诊断 / 策略）捕获后优雅降级。"""
