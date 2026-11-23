"""Тесты управления демонами и атомарной записи конфига.

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Проверяем:

- **второй демон не поднимается**: если процесс уже жив — «уже работает»;
- **демон опознаётся по командной строке БЕЗ аргументов**: разовые вызовы
  (`--send`, `--list`) — не демон, иначе «демон жив» окажется правдой, пока
  его нет;
- **реальная** функция записи конфига (`write_projects`) не ломает чтение
  параллельному читателю — это боевой код, а не модель поведения.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_daemons as daemons   # noqa: E402
import minigen_state as state       # noqa: E402


class DaemonMatchingTest(unittest.TestCase):
    """Опознание демона и запрет дубля."""

    def test_by_name(self) -> None:
        self.assertEqual(daemons.by_name("лечащий")["script"], "minimax-autheal.py")
        self.assertEqual(daemons.by_name("minimax-netwatch.py")["role"], "сеть")
        self.assertEqual(daemons.by_name("нет такого"), {})

    def test_start_refuses_duplicate(self) -> None:
        spec = daemons.DAEMONS[0]
        original = daemons.daemon_pids
        daemons.daemon_pids = lambda s: [4242]
        try:
            result = daemons.start(spec)
        finally:
            daemons.daemon_pids = original
        self.assertTrue(result["ok"])
        self.assertTrue(result["already"])
        self.assertEqual(result["pids"], [4242])

    def test_status_shape(self) -> None:
        original = daemons.daemon_pids
        daemons.daemon_pids = lambda s: [1]
        try:
            result = daemons.status()
        finally:
            daemons.daemon_pids = original
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["alive_count"], 3)
        for item in result["daemons"]:
            self.assertIn("name", item)
            self.assertIn("role", item)
            self.assertTrue(item["alive"])

    def test_pids_pattern_ignores_arguments(self) -> None:
        """Разовая команда с аргументом — не демон."""
        import re
        pattern = re.compile(r"(minimax-(?:netwatch|sessionwatch|autheal)\.py)\s*$")
        daemon_line = r"python C:\Users\valstan\bin\minimax-sessionwatch.py"
        one_shot = ('"python.exe" C:\\Users\\valstan\\bin\\minimax-sessionwatch.py '
                    '--send brain mvs_123 "текст"')
        self.assertTrue(pattern.search(daemon_line))
        self.assertIsNone(pattern.search(one_shot))


class ApiTest(unittest.TestCase):
    """Локальный API программы (его зовёт сетевой сторож)."""

    def test_contour_excludes_netwatch(self) -> None:
        import minigen_api
        names = [spec["script"] for spec in minigen_api.contour_daemons()]
        self.assertNotIn("minimax-netwatch.py", names,
                         "сетевой сторож не должен перезапускать сам себя")
        self.assertIn("minimax-autheal.py", names)
        self.assertIn("minimax-sessionwatch.py", names)

    def test_api_requires_token(self) -> None:
        import urllib.error
        import urllib.request

        import minigen_api
        old_token_file = minigen_api.TOKEN_FILE
        old_local = minigen_api.LOCAL
        with tempfile.TemporaryDirectory() as tmp:
            minigen_api.LOCAL = Path(tmp)
            minigen_api.TOKEN_FILE = Path(tmp) / "bridge-token.txt"
            try:
                server = minigen_api.HTTPServer(("127.0.0.1", 0),
                                               minigen_api.Handler)
                import threading
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_address[1]
                try:
                    with urllib.request.urlopen(
                            "http://127.0.0.1:%d/status" % port, timeout=5) as resp:
                        self.fail("без токена доступ не должен открываться: %s"
                                  % resp.status)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 401)

                # с токеном — открывается
                token = minigen_api.token()
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/status?token=%s" % (port, token),
                        timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                server.shutdown()
                server.server_close()
            finally:
                minigen_api.TOKEN_FILE = old_token_file
                minigen_api.LOCAL = old_local


class ConfigWriteRaceTest(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.core = state.load_daemons()["core"]
        self._old_config = self.core.CONFIG
        self.core.CONFIG = Path(self.tmp.name) / "mcode-project-areas.json"

    def tearDown(self) -> None:
        self.core.CONFIG = self._old_config
        self.tmp.cleanup()

    def test_reader_never_sees_broken_json(self) -> None:
        errors: list[str] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    json.loads(self.core.CONFIG.read_text(encoding="utf-8"))
                except ValueError as exc:
                    errors.append(str(exc))
                except OSError:
                    pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            for i in range(25):
                cfg = {"projects": [{"slug": "a%d" % n, "autoheal": n % 2 == 0}
                                    for n in range(5 + i)]}
                ok, err = state.write_projects(cfg)
                self.assertTrue(ok, err)
        finally:
            stop.set()
            thread.join(timeout=5)
        self.assertEqual(errors, [], "читатель увидел битый JSON: %s" % errors[:3])
        final = json.loads(self.core.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(len(final["projects"]), 29)

    def test_write_reports_result(self) -> None:
        ok, err = state.write_projects({"projects": [{"slug": "x", "autoheal": True}]})
        self.assertTrue(ok, err)
        data = json.loads(self.core.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(data["projects"][0]["slug"], "x")


if __name__ == "__main__":
    unittest.main()
