"""AutoAiSelfHeal —— 带 AI 自愈能力的 UI 自动化测试框架。

四层架构：
- engine/     能力建设层（Playwright 执行引擎、自愈定位器、智能等待、弹窗处理）
- collect/    数据采集层（截图 / DOM / 网络 / trace）
- agent/      AI 自愈 Agent 层（编排闭环 / 诊断 / 多策略修复）
- reporting/  展示层（Allure / HTML 看板 / 审计）
支撑模块：llm/（模型抽象）、knowledge/（知识库）、config.py（配置）。
"""

__version__ = "0.1.0"
