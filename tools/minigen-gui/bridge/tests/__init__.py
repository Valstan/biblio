"""Тесты моста «МиниГена».

⚠️ **Живой реестр агентов неприкосновенен.** Реестр — рабочая копия
программы (`%LOCALAPPDATA%\\minigen-gui\\agents\\minigen-agents`) и он
**источник правды**: по нему агента восстанавливают на любой другой
машине. Тест, который пишет в него, ломает программу владельца.

Живой случай 24.11: в `RequestTest.setUp` манифест писался **до** подмены
`registry_dir` — то есть уходил в настоящий реестр и обрезал манифест
агента «Идеи» до двух строк (`slug` и `repo`). После этого окно
показывало агента без названия и области, а «Восстановить» на другой
машине поехало бы не по тому манифесту. На GitHub манифест был цел —
сломала его локальная копия.

Поэтому здесь реестр — **всегда временный**: путь подменяется на весь
сеанс тестов (см. `TEST_REGISTRY`), и отдельные тесты, которым нужен
свой реестр, подменяют его поверх. Проверка `test_agents.py::
LiveRegistryGuardTest` следит, чтобы эта страховка не исчезла.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_agents as agents  # noqa: E402

#: Настоящий реестр программы — тесты его не трогают.
LIVE_REGISTRY = agents.REGISTRY_HOME / agents.DEFAULT_REGISTRY.split("/")[-1]

#: Реестр на время тестов (общий для всего сеанса).
TEST_REGISTRY = Path(tempfile.mkdtemp(prefix="minigen-agents-tests-")) / "minigen-agents"
TEST_REGISTRY.mkdir(parents=True, exist_ok=True)

#: Рабочее место агентов на время тестов (иначе тест мог бы писать в
#: `D:\\valstan\\REPO` — настоящую папку владельца).
TEST_WORKSPACE = Path(tempfile.mkdtemp(prefix="minigen-workspace-tests-"))

agents.registry_dir = lambda: TEST_REGISTRY      # type: ignore[assignment]
agents.workspace_home = lambda: TEST_WORKSPACE   # type: ignore[assignment]
