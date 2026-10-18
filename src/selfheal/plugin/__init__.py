"""插件装配层（M10-②）：把「自愈插件」装配成对 POM 可直接使用的对象。

**为什么需要独立的 plugin 层**：`HealingPage` 是自愈插件的**组合根**——它要构建知识库、
编排器（orchestrator）、弹窗处理与报告器，天然依赖 agent / knowledge / reporting 全部上层。
此前它落在 `engine/healing_locator.py`，导致「能力建设层」在 import 期反向拉起整个 Agent 层
（评审 M10 分层倒置）。上移本层后：

- `engine/` 只提供原语（`HealingLocator` / `HealingFailedError` / 智能等待 / 弹窗处理），
  仅依赖 `contracts` + engine 内部 + `config`；
- `collect/` 只做现场采集，依赖 `dom` + `contracts`；
- 装配依赖集中在本层，方向单一：plugin → engine + agent + knowledge + reporting。

约束：本层只允许被更外层（tests / 用户代码）依赖，**不得**被 engine / collect / agent 反向 import。
"""

from selfheal.plugin.healing_page import HealingPage

__all__ = ["HealingPage"]
