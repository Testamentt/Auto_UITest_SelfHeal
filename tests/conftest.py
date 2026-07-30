"""pytest 公共 fixture。

TODO: 提供 settings / browser / page / orchestrator 等共享 fixture，
并在失败时自动触发现场采集与自愈。
"""

from __future__ import annotations

import pytest

from selfheal.config import Settings, load_settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()
