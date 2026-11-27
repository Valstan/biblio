r"""Тест переключателя «водитель проекта» (промт КАРМАНА #174).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Проверяем РЕАЛЬНУЮ команду `areas.set_driver` и то, что окно видит водителя:

- переключатель пишется в конфиг области и читается обратно;
- «по правилу» (пустое значение) — значение по умолчанию;
- неизвестное значение и несуществующая область — отказ, а не тихая запись;
- в списке областей видно, **кто водит сейчас** (водитель + причина);
- ядро без правила водителя (старая версия на машине) — честное «—», а не
  выдуманный водитель.

⚠️ Почему изолированно: команда пишет в конфиг областей — боевой конфиг
(`mcode-project-areas.json`) трогать нельзя, поэтому тест работает на
временном конфиге.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_state as state   # noqa: E402


class DriverSwitchTest(unittest.TestCase):
    """areas.set_driver: переключатель водителя сессий проекта."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # ⚠️ Мост берёт демонов из БОЕВОЙ папки, если она есть (`C:\Users\
        # valstan\bin`) — а там может лежать старая копия ядра, без правила
        # водителя. Тест проверяет КОД РЕПОЗИТОРИЯ, поэтому на время теста
        # указываем мосту на эталон из репозитория.
        self._old_env = os.environ.get("MINIGEN_DAEMON_DIR")
        self._old_cache = getattr(state.load_daemons, "_cache", None)
        os.environ["MINIGEN_DAEMON_DIR"] = str(state.REPO_TOOLS)
        state.load_daemons._cache = None
        self.core = state.load_daemons()["core"]
        self._old_config = self.core.CONFIG
        self._old_log = self.core.log
        self.config = root / "mcode-project-areas.json"
        self.config.write_text(json.dumps({
            "projects": [{"slug": "brain", "title": "МОЗГ", "folder": "",
                          "autoheal": True}],
        }, ensure_ascii=False), encoding="utf-8")
        self.core.CONFIG = self.config
        self.core.log = lambda msg: None

    def tearDown(self) -> None:
        self.core.CONFIG = self._old_config
        self.core.log = self._old_log
        if self._old_env is None:
            os.environ.pop("MINIGEN_DAEMON_DIR", None)
        else:
            os.environ["MINIGEN_DAEMON_DIR"] = self._old_env
        state.load_daemons._cache = self._old_cache
        self.tmp.cleanup()

    # ── вспомогательное ───────────────────────────────────────────────────
    def _bridge(self):
        import minigen_bridge

        class FakeBridge:
            def __init__(self, bridge_self) -> None:
                self.core = bridge_self.core
                self.mods = state.load_daemons()

            def log(self, msg: str) -> None:
                pass

        return minigen_bridge.Bridge, FakeBridge(self)

    def _projects(self) -> list:
        data = json.loads(self.config.read_text(encoding="utf-8"))
        return data.get("projects") or []

    # ── проверки ───────────────────────────────────────────────────────────
    def test_set_driver_writes_config(self) -> None:
        bridge_cls, fake = self._bridge()
        result = bridge_cls.cmd_set_driver(fake, {"slug": "brain", "driver": "manager"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("driver"), "manager")
        projects = self._projects()
        self.assertEqual(projects[0].get("driver"), "manager")
        # прежние поля области не тронуты
        self.assertTrue(projects[0].get("autoheal"))

    def test_driver_auto_means_by_rule(self) -> None:
        """«По правилу» — пустое значение (значение по умолчанию)."""
        bridge_cls, fake = self._bridge()
        bridge_cls.cmd_set_driver(fake, {"slug": "brain", "driver": "manager"})
        result = bridge_cls.cmd_set_driver(fake, {"slug": "brain", "driver": ""})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("driver", self._projects()[0])

    def test_unknown_driver_refused(self) -> None:
        bridge_cls, fake = self._bridge()
        result = bridge_cls.cmd_set_driver(fake, {"slug": "brain", "driver": "кто-то"})
        self.assertIn("error", result)
        self.assertNotIn("driver", self._projects()[0])

    def test_unknown_area_refused(self) -> None:
        bridge_cls, fake = self._bridge()
        result = bridge_cls.cmd_set_driver(fake, {"slug": "нет-такой", "driver": "manager"})
        self.assertIn("error", result)

    def test_areas_state_shows_driver(self) -> None:
        """В списке областей видно, кто водит сейчас (и почему)."""
        bridge_cls, fake = self._bridge()
        bridge_cls.cmd_set_driver(fake, {"slug": "brain", "driver": "manager"})
        mods = state.load_daemons()
        mods["core"].CONFIG = self.config
        areas = state.areas_state(mods, include_sessions=False)
        brain = next((a for a in areas if a["slug"] == "brain"), {})
        self.assertEqual(brain.get("driver"), "manager")
        now = brain.get("driverNow") or {}
        self.assertIn("driver", now)
        self.assertIn("title", now)
        # Ядро на машине может быть без правила водителя — тогда честно «—».
        self.assertTrue(now.get("title") or now.get("why"))

    def test_driver_of_area_without_rule(self) -> None:
        """Старое ядро (без `driver_of`) — честное «—», а не выдумка."""

        class OldCore:
            pass

        out = state.driver_of_area({"core": OldCore()}, {"slug": "brain"})
        self.assertEqual(out.get("driver"), "")
        self.assertEqual(out.get("title"), "—")
        self.assertTrue(out.get("why"))

    def test_switch_has_exactly_two_positions(self) -> None:
        """Переключатель — два положения (заказ владельца 27.11).

        ⚠️ «Внешнее управление» (`manager`) и «Оператор» (`none») — одно
        положение (управление внешнее; оператор или другой агент — без разницы),
        поэтому в переключателе его нет: там **«Миниген»** и **«Оператор»**.
        В ядре значение `manager` осталось — это ответ **правила** (в области
        работает Управляющий ⇒ водит он), и правило обязано его уметь назвать.
        """
        import minigen_tools as tools

        tool = tools.by_name()["areas.set_driver"]
        props = tool["parameters"]["properties"]
        enum = list(props["driver"]["enum"])
        self.assertEqual(sorted(enum), ["auto", "automaton", "none"],
                         "в переключателе должны быть только два положения "
                         "(и «по правилу» для ядра)")
        self.assertNotIn("manager", enum)

    def test_legacy_manager_value_is_still_accepted(self) -> None:
        """Старое значение из прежнего переключателя — не ломаем.

        В конфиге области может остаться `manager` (переключатель раньше его
        предлагал) ⇒ команда обязана его принять, а не ругаться.
        """
        bridge_cls, fake = self._bridge()
        result = bridge_cls.cmd_set_driver(fake, {"slug": "brain", "driver": "manager"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(self._projects()[0].get("driver"), "manager")


if __name__ == "__main__":
    unittest.main()
