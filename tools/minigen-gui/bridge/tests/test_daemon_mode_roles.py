"""Тесты разделения ролей фонового режима (24.11, живой случай 08:00).

Роли разные:

- **надзиратель демонов** — ровно один, его держит блокировка
  `supervisor.lock` (кто первый взял — тот и следит);
- **API** (`:8097`) — порт эксклюзивный, его держит один процесс.

Живой случай: открытое окно владельца держало блокировку ⇒ фоновый
экземпляр выходил («надзиратель демонов уже есть — выхожу») ⇒ **API умирал
вместе с ним**, и сетевой сторож не мог перезапустить контур. Демоны при
этом жили (их поднимало окно) — то есть внешне всё выглядело живым.

Проверяем:

- **без блокировки API всё равно поднимается** (это и есть разделение ролей);
- **если API уже кто-то служит — выходим** (второй слушатель не нужен);
- **если порт занят и API не поднялся — выходим** и отпускаем блокировку.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge_mod  # noqa: E402


class _Stop(Exception):
    """Остановка бесконечного ожидания в тесте."""


class _FakeLock:
    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired
        self.released = False

    def acquire(self) -> bool:
        return self._acquired

    def release(self) -> None:
        self.released = True


class _FakeApi:
    def __init__(self, running: bool = False, bind_fails: bool = False) -> None:
        self.running = running
        self.bind_fails = bind_fails
        self.served = False

    def another_instance_running(self) -> bool:
        return self.running

    def serve(self) -> None:
        if self.bind_fails:
            raise OSError("порт занят")
        self.served = True

    def base_url(self) -> str:
        return "http://127.0.0.1:8097"


class DaemonModeRolesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._api = _FakeApi()
        self._lock = _FakeLock(True)
        self._orig = {}
        import minigen_api
        import minigen_lock
        self._orig["api"] = sys.modules["minigen_api"]
        self._orig["lock"] = minigen_lock.get_lock
        self._orig["bridge_cls"] = bridge_mod.Bridge
        self._orig["sleep"] = bridge_mod.time.sleep
        sys.modules["minigen_api"] = self._api
        minigen_lock.get_lock = lambda: self._lock
        bridge_mod.Bridge = lambda *a, **k: _FakeBridge()
        bridge_mod.time.sleep = _raise_stop

    def tearDown(self) -> None:
        import minigen_lock
        sys.modules["minigen_api"] = self._orig["api"]
        minigen_lock.get_lock = self._orig["lock"]
        bridge_mod.Bridge = self._orig["bridge_cls"]
        bridge_mod.time.sleep = self._orig["sleep"]

    def _run(self) -> int:
        try:
            return bridge_mod.daemon_mode()
        except _Stop:
            return 0

    def test_api_is_served_even_without_the_lock(self) -> None:
        # блокировку держит кто-то другой (например, открытое окно)
        self._lock = _FakeLock(False)
        bridge_mod.minigen_lock.get_lock = lambda: self._lock
        self._run()
        self.assertTrue(self._api.served,
                        "API должен подниматься и без блокировки надзирателя")

    def test_exits_when_api_is_already_served(self) -> None:
        self._api.running = True
        result = self._run()
        self.assertEqual(result, 0)
        self.assertFalse(self._api.served, "второй слушатель API не нужен")

    def test_exits_when_the_port_is_taken(self) -> None:
        self._api.bind_fails = True
        result = self._run()
        self.assertEqual(result, 0)
        self.assertTrue(self._lock.released, "блокировку отпускаем, если API не поднялся")


class _FakeBridge:
    def __init__(self, *args, **kwargs) -> None:
        self.core = _FakeCore()


class _FakeCore:
    def log(self, msg: str) -> None:
        return None


def _raise_stop(*args, **kwargs):
    raise _Stop()


if __name__ == "__main__":
    unittest.main()
