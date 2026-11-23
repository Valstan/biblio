"""Тесты «один фоновый режим — ровно один».

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что здесь проверяется (живые находки 23.11):

- **надзиратель демонов — один**: системная блокировка достаётся только
  одному процессу; второй её не берёт (и не следит за демонами);
- **блокировка не протухает**: упавший владелец её отпускает (снимает ОС,
  а не файл-флаг) — иначе демоны остались бы без присмотра;
- **гонка двух стартов**: оба процесса проверяют API, оба видят «никого» —
  но блокировку получает только один;
- **API нельзя перехватить**: второй сервер на том же порту обязан
  отказать (на Windows `SO_REUSEADDR` разрешал двум процессам слушать
  `:8097` одновременно — и обращение сетевого сторожа могло уйти не к
  тому экземпляру);
- **не смог поднять API — не надзиратель**: процесс отказывается от роли
  менеджера, вместо того чтобы молча следить за демонами вторым.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_daemons as daemons   # noqa: E402
import minigen_lock as minigen_lock  # noqa: E402
import minigen_state as state       # noqa: E402


class LockTest(unittest.TestCase):
    """Системная блокировка: эксклюзивность и отсутствие «протухания»."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.path = base / "supervisor.lock"
        self.info = base / "supervisor.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _lock(self) -> minigen_lock.SupervisorLock:
        return minigen_lock.SupervisorLock(path=self.path, info=self.info)

    def test_second_holder_does_not_get_the_lock(self) -> None:
        first = self._lock()
        second = self._lock()
        try:
            self.assertTrue(first.acquire(), "первый должен взять блокировку")
            self.assertFalse(second.acquire(),
                             "второй не должен получить блокировку: надзиратель должен быть один")
        finally:
            first.release()

    def test_lock_frees_after_release(self) -> None:
        first = self._lock()
        second = self._lock()
        self.assertTrue(first.acquire())
        first.release()
        try:
            self.assertTrue(second.acquire(),
                            "после освобождения блокировку должен взять кто-то ещё")
        finally:
            second.release()

    def test_lock_is_not_left_by_a_dead_process(self) -> None:
        """Упавший владелец не должен оставить блокировку (файл-флаг протухает)."""
        code = (
            "import sys; sys.path.insert(0, %r); import minigen_lock as m; "
            "l = m.SupervisorLock(path=%r, info=%r); "
            "print('ok' if l.acquire() else 'no')" % (str(BRIDGE_DIR), str(self.path),
                                                       str(self.info))
        )
        res = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual((res.stdout or "").strip(), "ok",
                         "дочерний процесс не взял блокировку: %s" % res.stderr[-200:])
        # дочерний процесс умер — блокировку сняла ОС
        mine = self._lock()
        try:
            self.assertTrue(mine.acquire(),
                            "после смерти владельца блокировка должна освободиться")
        finally:
            mine.release()

    def test_owner_is_written_and_readable(self) -> None:
        lock = self._lock()
        try:
            self.assertTrue(lock.acquire())
            owner = minigen_lock.read_owner(self.info)
            self.assertIsNotNone(owner, "владелец должен быть записан")
            self.assertEqual(owner["pid"], os.getpid())
            self.assertTrue(owner["alive"])
        finally:
            lock.release()
        self.assertIsNone(minigen_lock.read_owner(self.info),
                          "после освобождения подсказка должна исчезнуть")


class SuperviseLoopTest(unittest.TestCase):
    """Надзирать за демонами должен только владелец блокировки."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.path = base / "supervisor.lock"
        self.info = base / "supervisor.json"
        # ⚠️ Тест не должен писать в ЖИВОЙ журнал (%LOCALAPPDATA%\mcode-web):
        # надзор пишет туда «демон не жил — поднял», и такая строка от теста
        # выглядела бы потом как настоящая поломка.
        self._orig_load = state.load_daemons
        state.load_daemons = lambda: (_ for _ in ()).throw(RuntimeError("тест без журнала"))

    def tearDown(self) -> None:
        state.load_daemons = self._orig_load
        self.tmp.cleanup()

    def test_no_supervision_while_another_holder_keeps_the_lock(self) -> None:
        holder = minigen_lock.SupervisorLock(path=self.path, info=self.info)
        self.assertTrue(holder.acquire())

        started: list[str] = []
        original_pids = daemons.daemon_pids
        original_start = daemons.start
        original_respawn = daemons.RESPAWN_EVERY
        daemons.daemon_pids = lambda spec: []
        daemons.start = lambda spec, grace=0.0: started.append(spec["name"]) or {"ok": True}
        daemons.RESPAWN_EVERY = 1
        stop = threading.Event()
        lock = minigen_lock.SupervisorLock(path=self.path, info=self.info)
        thread = threading.Thread(
            target=daemons.supervise_loop, args=(stop, lock), daemon=True)
        thread.start()
        try:
            time.sleep(2.0)
            self.assertEqual(started, [],
                             "второй надзиратель не должен поднимать демонов: %s" % started)
        finally:
            stop.set()
            thread.join(timeout=5)
            lock.release()
            daemons.daemon_pids = original_pids
            daemons.start = original_start
            daemons.RESPAWN_EVERY = original_respawn
            holder.release()

    def test_supervision_starts_after_the_lock_is_released(self) -> None:
        started: list[str] = []
        original_pids = daemons.daemon_pids
        original_start = daemons.start
        original_respawn = daemons.RESPAWN_EVERY
        daemons.daemon_pids = lambda spec: []
        daemons.start = lambda spec, grace=0.0: started.append(spec["name"]) or {"ok": True}
        daemons.RESPAWN_EVERY = 1
        stop = threading.Event()
        lock = minigen_lock.SupervisorLock(path=self.path, info=self.info)
        thread = threading.Thread(
            target=daemons.supervise_loop, args=(stop, lock), daemon=True)
        thread.start()
        try:
            deadline = time.time() + 15
            while time.time() < deadline and not started:
                time.sleep(0.2)
            self.assertTrue(started, "владелец блокировки должен поднимать демонов")
        finally:
            stop.set()
            thread.join(timeout=5)
            lock.release()
            daemons.daemon_pids = original_pids
            daemons.start = original_start
            daemons.RESPAWN_EVERY = original_respawn


class ApiExclusivityTest(unittest.TestCase):
    """Порт API нельзя занять второму процессу."""

    def test_second_server_cannot_take_the_same_port(self) -> None:
        import minigen_api

        first = minigen_api.ApiServer(("127.0.0.1", 0), minigen_api.Handler)
        port = first.server_address[1]
        try:
            with self.assertRaises(OSError):
                minigen_api.ApiServer(("127.0.0.1", port), minigen_api.Handler)
        finally:
            first.server_close()


class DaemonModeTest(unittest.TestCase):
    """Фоновый режим: чужая жизнь — выходим, а не становимся вторым."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.path = base / "supervisor.lock"
        self.info = base / "supervisor.json"
        self._lock = minigen_lock.SupervisorLock(path=self.path, info=self.info)

        import minigen_bridge
        import minigen_api

        self.bridge = minigen_bridge
        self.api = minigen_api
        self._orig_get_lock = minigen_lock.get_lock
        self._orig_running = minigen_api.another_instance_running
        self._orig_serve = minigen_api.serve
        minigen_lock.get_lock = lambda: self._lock

    def tearDown(self) -> None:
        minigen_lock.get_lock = self._orig_get_lock
        self.api.another_instance_running = self._orig_running
        self.api.serve = self._orig_serve
        self._lock.release()
        self.tmp.cleanup()

    def test_exits_when_api_answers(self) -> None:
        """Живой API — значит, фоновый режим уже есть (PR #83)."""
        self.api.another_instance_running = lambda *a, **k: True
        self.api.serve = lambda *a, **k: self.fail("API поднимать нельзя")
        self.assertEqual(self.bridge.daemon_mode(), 0)
        self.assertFalse(self._lock.owned, "блокировку брать нельзя: надзиратель уже есть")

    def test_exits_when_api_port_is_busy(self) -> None:
        """Порт занят — значит, мы не менеджер: уходим, а не следим за демонами."""
        self.api.another_instance_running = lambda *a, **k: False

        def boom(*_args, **_kwargs):
            raise OSError("адрес уже занят")

        self.api.serve = boom
        self.assertEqual(self.bridge.daemon_mode(), 0)
        self.assertFalse(self._lock.owned,
                         "блокировка должна быть отпущена: надзиратель — не мы")


if __name__ == "__main__":
    unittest.main()
