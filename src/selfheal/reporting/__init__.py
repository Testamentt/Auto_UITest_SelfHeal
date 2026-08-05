"""展示层 —— 报告与审计。

把自愈过程（原定位器 / 新定位器 / 策略 / 置信度 / 根因 / 现场截图）
写入 Allure 附件与自研 HTML 看板（dashboard.py，B3 起由 pytest 会话结束自动生成），
供面试演示与审计回放。Playwright trace 由 conftest `--trace-healing` 录制并附 Allure（C5）。
"""
