# 基础自动化框架建设（Base Framework）

> 日期：2026-08-05 · 依据：用户提出"自愈插件已成熟，但 pytest+Playwright 基座薄"
> 原则：基座面向 Page 接口编写，与自愈插件（HealingPage 兼容原生 Page）正交，**不破坏现有插件与测试**

## Context

自愈插件层已成熟（Phase 1–4 + 评审修复），但作为基石的 pytest+Playwright 框架只停留在"够用"：
无 POM 基类、BrowserManager 单薄、无数据驱动、无"怎么写用例"文档。本计划把基座补成能支撑
新用例/POM 的成体系框架，同时放大自愈"可插拔"的价值。

## 现状（Base 已有 vs 缺失）

| 已有 | 缺失 |
| --- | --- |
| BrowserManager（chromium/chrome，读 config） | POM 基类（DemoPage/PopupPage 手写、无规范） |
| conftest session/function fixture + `--selfheal` | 多浏览器 / trace / 失败截图配置 |
| 演示 POM + e2e | 数据驱动 / 断言层 |
| 命令式 README | "写你的第一个 POM + 用例"指引 |

## 组件（按优先级）

### B1 · POM 基类 BasePage（最高价值，直接支撑 D3"POM 无缝切换"）
- 新增 `src/selfheal/engine/base_page.py`：

```python
class BasePage:
    """POM 基类：统一"POM 如何拿 page、如何打开、如何定位"。
    POM 只依赖 page 接口（原生 Page 或 HealingPage 均可），自愈开/关都能跑（决策 D3）。"""

    def __init__(self, page):
        self.page = page

    @property
    def url(self) -> str:
        raise NotImplementedError

    def open(self, url: str | None = None) -> None:
        self.page.goto(url or self.url)

    def locator(self, selector, *, description=None, fallback=None, **kwargs):
        return self.page.locator(selector, description=description, fallback=fallback, **kwargs)
```

- 约定：子类声明 `url`、用 property 暴露关键元素、动作写成方法（`def login(...)`）。
- 重构 `DemoPage`/`PopupPage` 继承 BasePage（`class DemoPage(BasePage)`）。
- **验收**：重构后既有 e2e 三场景零回归（证明兼容）。

### B2 · BrowserManager 增强（为 Phase 4 视频回放铺路）
- `BrowserConfig` 增 `browser_type`（chromium/firefox/webkit，默认 chromium）；修正 channel（仅 chromium 适用）。
- `BrowserConfig` 增 `trace: bool`、`record_video: bool`、`screenshot_on_failure: bool`。
- `BrowserManager`：按 browser_type launch；new_context 支持 record_video/trace；失败截图钩子。
- **注意**：firefox/webkit 需 `playwright install` 对应内核；可后置、独立小步。

### B3 · conftest fixture 标准化（对齐 pytest-playwright 形状）
- 提供 `browser`(session) → `context`(function) → `page`(function) 标准链（用自研 BrowserManager）。
- `healing_page` 保持兼容（既有测试不动）。
- 新增 `pom` 工厂（`get_page(DemoPage)` 之类）便于用例直接拿到页面对象。

### B4 · 数据驱动（轻量，可选）
- `tests/support/data.py`：`load_test_data(path)`（JSON/YAML）+ `parametrize_from_json`。
- 示例数据文件；一个 `@pytest.mark.parametrize` 用例。

### B5 · 用例开发文档
- README 增「写你的第一个 POM + 用例」（fixture 注入、继承 BasePage、healing 增强参数）。
- 或 `docs/writing-tests.md`。

## 实施顺序

1. **B1 BasePage + 重构 Demo/PopupPage**（核心，先做）
2. **B3 fixtures 标准化**（配合 B1 提供 `page`/`pom`）
3. **B5 文档**（基座成型即写"怎么写用例"）
4. **B4 数据驱动**（低优先，可选）
5. **B2 BrowserManager 增强**（含 trace/视频；多内核需装内核，独立小步、可后置）

## 验证

1. `pytest -m unit`：BasePage 基本行为（fake page）+ 全量回归。
2. `pytest -m e2e`：BasePage 重构后的 DemoPage 三场景零回归 + 其余场景。
3. 数据驱动：一个 parametrize 用例通过。
4. `ruff check . && ruff format .` 干净。

## 风险与注意

- **不破坏自愈插件**：BasePage 只是持有 page 接口，不自知 HealingPage；重构 Demo/PopupPage 必须保持既有 e2e 通过。
- B2 多浏览器需额外内核下载（网络环境），失败不阻塞 B1/B3。
- 断言层（软断言等）本次不纳入，避免范围膨胀；后续需要再评估。
