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
def _reset_status_cache() -> None:
    """Сбросить кэш статуса демонов (иначе тесты видели бы чужой ответ)."""
    daemons._STATUS_CACHE.update({"at": 0.0, "value": None})

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
        original = daemons.daemon_pids_all
        daemons.daemon_pids_all = lambda: {"minimax-autheal.py": [1],
                                           "minimax-netwatch.py": [1],
                                           "minimax-sessionwatch.py": [1]}
        original_view = daemons.supervisor_view
        daemons.supervisor_view = lambda: None      # надзирателя нет — смотрим сами
        _reset_status_cache()
        try:
            result = daemons.status()
        finally:
            daemons.daemon_pids_all = original
            daemons.supervisor_view = original_view
            _reset_status_cache()
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


class SupervisorViewTest(unittest.TestCase):
    """Демонов спрашивает надзиратель — он смотрит их своими правами.

    ⚠️ Живой случай 24.11: демоны подняты с повышенными правами, окно — нет.
    WMI не отдаёт их командные строки ⇒ окно показывало «демоны 0/3» при живых
    демонах, а «запустить» грозило второй копией (урок 22.11).
    """

    def setUp(self) -> None:
        self._view = daemons.supervisor_view
        self._ask = daemons._ask_supervisor
        self._pids = daemons.daemon_pids_all
        daemons.daemon_pids_all = lambda: {}         # окно демонов не видит
        _reset_status_cache()

    def tearDown(self) -> None:
        daemons.supervisor_view = self._view
        daemons._ask_supervisor = self._ask
        daemons.daemon_pids_all = self._pids
        _reset_status_cache()

    def test_status_takes_the_supervisor_view(self) -> None:
        daemons.supervisor_view = lambda: {
            "daemons": [{"name": "лечащий", "alive": True, "pids": [111]}],
            "alive_count": 3, "total": 3,
            "supervisor": {"pid": 999, "alive": True, "is_self": True},
        }
        result = daemons.status()
        self.assertEqual(result["alive_count"], 3)
        self.assertEqual(result["daemons"][0]["pids"], [111])

    def test_supervisor_is_not_us_unless_it_is_us(self) -> None:
        """Ответ надзирателя говорит «это я» — но «я» здесь — окно."""
        daemons.supervisor_view = lambda: {
            "daemons": [], "alive_count": 0, "total": 3,
            "supervisor": {"pid": 4242, "alive": True, "is_self": True},
        }
        result = daemons.status()
        self.assertFalse(result["supervisor"]["is_self"],
                         "надзиратель — не этот процесс ⇒ это не окно")

    def test_start_goes_through_the_supervisor(self) -> None:
        """Демона, которого окно не видит, поднимает надзиратель."""
        asked = []

        def ask(path, timeout=90):
            asked.append(path)
            return {"ok": True, "already": True, "pids": [222]}

        daemons._ask_supervisor = ask
        result = daemons.start(daemons.DAEMONS[0])
        self.assertTrue(result["ok"])
        self.assertEqual(result["pids"], [222])
        self.assertTrue(asked, "надзирателя не спросили")

    def test_stop_and_restart_go_through_the_supervisor(self) -> None:
        asked = []

        def ask(path, timeout=90):
            asked.append(path)
            return {"ok": True}

        daemons._ask_supervisor = ask
        self.assertTrue(daemons.stop(daemons.DAEMONS[0])["ok"])
        self.assertTrue(daemons.restart(daemons.DAEMONS[0])["ok"])
        self.assertTrue(daemons.restart_all()["ok"])
        self.assertEqual(len(asked), 3)


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
        import minigen_daemons as daemons
        old_token_file = minigen_api.TOKEN_FILE
        old_local = minigen_api.LOCAL
        # ⚠️ Статус демонов — **живой** (WMI по всем процессам машины): он
        # занимает 5–6 с, а клиентский таймаут в тесте — 5 с ⇒ тест был
        # монеткой: на тихой машине проходил, под нагрузкой (живой случай
        # 26.11: красный набор Python-тестов) падал «по времени», а не по
        # существу. Проверяем **контракт API** (токен обязателен, с токоном
        # 200), а не скорость WMI.
        old_status = daemons.status
        daemons.status = lambda: {"daemons": [], "alive_count": 0, "total": 3,
                                  "supervisor": {"pid": 0, "alive": False,
                                                 "is_self": False}}
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

                # живой API = другой фоновый экземпляр уже работает
                self.assertTrue(minigen_api.another_instance_running(port=port))

                server.shutdown()
                server.server_close()
            finally:
                minigen_api.TOKEN_FILE = old_token_file
                minigen_api.LOCAL = old_local
                daemons.status = old_status

    def test_no_instance_when_api_is_silent(self) -> None:
        import minigen_api
        # свободный порт, на котором никто не слушает
        self.assertFalse(minigen_api.another_instance_running(port=8099,
                                                              timeout=2))

    def test_daemon_routes_exist(self) -> None:
        """Окно ходит в API за демонами — значит, маршруты должны быть.

        ⚠️ Проверяем только «не такого демона» (404): настоящий запуск
        демона в тесте поднял бы второй экземпляр.
        """
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
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_address[1]
                token = minigen_api.token()
                try:
                    for path in ("/start?name=nope", "/stop?name=nope",
                                 "/restart?name=nope"):
                        with self.assertRaises(urllib.error.HTTPError) as ctx:
                            urllib.request.urlopen(
                                "http://127.0.0.1:%d%s&token=%s"
                                % (port, path, token), timeout=5)
                        self.assertEqual(ctx.exception.code, 404)
                finally:
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

    def test_write_keeps_a_local_backup(self) -> None:
        """Перед записью — копия рядом (в git его нет, копия — единственная).

        ⚠️ Конфиг областей — локальная вещь компьютера (канон владельца
        27.11): в репозитории его нет ⇒ страховка живёт только тут.
        """
        state.write_projects({"projects": [{"slug": "первая", "autoheal": True}]})
        backup = Path(str(self.core.CONFIG) + ".bak")
        self.assertTrue(backup.is_file(), "локальной копии нет: %s" % backup)
        first = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(first["projects"][0]["slug"], "первая")

        state.write_projects({"projects": [{"slug": "вторая", "autoheal": False}]})
        again = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(again["projects"][0]["slug"], "первая",
                         "копия должна хранить ПРЕДЫДУЩЕЮ версию")

    def test_broken_file_does_not_overwrite_the_backup(self) -> None:
        """Битый файл не должен затереть хорошую копию."""
        state.write_projects({"projects": [{"slug": "хорошая", "autoheal": True}]})
        backup = Path(str(self.core.CONFIG) + ".bak")
        self.core.CONFIG.write_text("{не json", encoding="utf-8")
        state.write_projects({"projects": [{"slug": "новая", "autoheal": True}]})
        saved = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(saved["projects"][0]["slug"], "хорошая",
                         "битый файл затерел хорошую копию")

    def test_write_reports_result(self) -> None:
        ok, err = state.write_projects({"projects": [{"slug": "x", "autoheal": True}]})
        self.assertTrue(ok, err)
        data = json.loads(self.core.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(data["projects"][0]["slug"], "x")


class OneScanTest(unittest.TestCase):
    """Демонов видно ОДНИМ вызовом PowerShell (26.11).

    ⚠️ Живой замер на установленной программе: `state.snapshot` стоил
    **12,6–28,2 с**, и виноват был не код окна, а три вызова WMI по одному на
    демона (2,8 с на тихой машине). Теперь список процессов читается один раз,
    а разбор делает Python.
    """

    FAKE = (
        "100|python C:\\Users\\valstan\\bin\\minimax-autheal.py\n"
        "200|python C:\\Users\\valstan\\bin\\minimax-netwatch.py\n"
        "300|python C:\\Users\\valstan\\bin\\minimax-sessionwatch.py\n"
        "400|python C:\\Users\\valstan\\bin\\minimax-sessionwatch.py "
        "--send brain mvs_1 \"текст\"\n"
        "500|python C:\\Users\\valstan\\bin\\minimax-orphans.py\n"
    )

    def setUp(self) -> None:
        self._ps = daemons._ps
        self._all = daemons.daemon_pids_all
        self._view = daemons.supervisor_view
        self.calls: list[str] = []
        daemons.supervisor_view = lambda: None      # надзирателя нет — смотрим сами
        _reset_status_cache()

    def tearDown(self) -> None:
        daemons._ps = self._ps
        daemons.daemon_pids_all = self._all
        daemons.supervisor_view = self._view
        _reset_status_cache()

    def fake_ps(self, cmd: str, timeout: int = 30) -> str:
        self.calls.append(cmd)
        return self.FAKE

    def test_status_scans_once_for_all_daemons(self) -> None:
        """Один вызов WMI на всех демонов, а не по одному на каждого."""
        daemons._ps = self.fake_ps
        result = daemons.status()
        self.assertEqual(len(self.calls), 1,
                         "статус демонов читается одним вызовом, а не тремя")
        self.assertEqual(result["alive_count"], 3)
        by_name = {item["name"]: item for item in result["daemons"]}
        self.assertEqual(by_name["лечащий"]["pids"], [100])
        self.assertEqual(by_name["сетевой сторож"]["pids"], [200])
        self.assertEqual(by_name["сторож сессий"]["pids"], [300])

    def test_one_shot_call_is_not_a_daemon(self) -> None:
        """Разовый вызов с аргументом — не демон (правило из докстринга)."""
        daemons._ps = self.fake_ps
        found = daemons.daemon_pids_all()
        self.assertEqual(found.get("minimax-sessionwatch.py"), [300],
                         "разовый вызов --send не должен считаться демоном")
        self.assertNotIn("minimax-orphans.py", found,
                         "чужой скрипт не должен попадать в список демонов")

    def test_quoted_path_is_still_a_daemon(self) -> None:
        """Путь в кавычках (пробел в пути) — демон, а не «мёртвый»."""
        lines = (
            "700|python \"C:\\Program Files\\МиниГен\\bin\\minimax-autheal.py\"\n")
        daemons._ps = lambda cmd, timeout=30: lines
        found = daemons.daemon_pids_all()
        self.assertEqual(found.get("minimax-autheal.py"), [700],
                         "путь в кавычках — это демон")

    def test_quoted_one_shot_is_not_a_daemon(self) -> None:
        """Разовый вызов с кавычками в конце — всё равно не демон."""
        lines = (
            "700|python \"C:\\Program Files\\МиниГен\\bin\\minimax-autheal.py\" "
            "--send brain mvs_1 \"текст\"\n")
        daemons._ps = lambda cmd, timeout=30: lines
        found = daemons.daemon_pids_all()
        self.assertFalse(found.get("minimax-autheal.py"),
                         "разовый вызов с аргументами демоном не считается")

    def test_status_is_cached(self) -> None:
        """Повторный вопрос про демонов не идёт в WMI (кэш `STATUS_TTL`)."""
        daemons._ps = self.fake_ps
        daemons.status()
        daemons.status()
        self.assertEqual(len(self.calls), 1,
                         "второй вопрос должен взять кэш, а не WMI")
        # кэш протух — идём в WMI снова
        daemons._STATUS_CACHE["at"] = 0.0
        daemons.status()
        self.assertEqual(len(self.calls), 2)

    def test_daemon_pids_is_never_cached(self) -> None:
        """Проверка «жив ли демон» — всегда свежая (второй демон не поднимется)."""
        daemons._ps = self.fake_ps
        daemons.daemon_pids(daemons.DAEMONS[0])
        daemons.daemon_pids(daemons.DAEMONS[0])
        self.assertEqual(len(self.calls), 2,
                         "проверка процессов обязана быть свежей: иначе "
                         "может подняться второй демон (урок 22.11)")


if __name__ == "__main__":
    unittest.main()
