"""Тесты моста «МиниГена».

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем (и почему именно это):

- разбор строк журнала — формат в бою нестрогий, и от него зависит статистика;
- классификацию событий — по ней владелец видит «кто и почему перезапускался»;
- чтение и запись настроек — с атомарной заменой файла;
- разбор ответа вендора — из него строится и текст, и вызовы инструментов;
- агент-цикл — безопасный инструмент выполняется сразу, опасный ждёт
  подтверждения владельца;
- соответствие каталога инструментов реестру команд — инструмент, у которого
  нет команды, — это «кнопка без обработчика».
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

# Журнал пишется по Москве (заказ владельца 24.11) — и подставной тоже.
MSK = timezone(timedelta(hours=3), "MSK")

import minigen_agent as agent          # noqa: E402
import minigen_state as state          # noqa: E402
import minigen_tools as tools          # noqa: E402


class FakeCore:
    """Мини-ядро: свои области, свой журнал, свой каталог.

    ⚠️ Подпись `fmt_ms` — **как в боевом ядре**: ОДИН аргумент (формат зашит
    в модуль времени). Раньше подставное ядро принимало два аргумента и
    прятало расхождение: мост звал `fmt_ms(ms, формат)`, боевое ядро — нет,
    и `state.snapshot` падал в бою, а в тестах проходил (живой случай
    24.11: окно показывало «0 областей», «демоны 0/0»).
    """

    def __init__(self, journal: Path, areas: list | None = None) -> None:
        self.JOURNAL = journal
        self._areas = areas or []
        self.CONFIG = journal.parent / "config.json"
        self.NET_STATE = journal.parent / "net-state.json"

    def areas(self) -> list:
        return self._areas

    def running_areas(self) -> set:
        return {"p-brain"}

    def fmt_ms(self, ms: int) -> str:
        """Время — по Москве, как в настоящем ядре (заказ владельца 24.11)."""
        return datetime.fromtimestamp(int(ms or 0) / 1000, MSK).strftime("%d.%m %H:%M:%S")

    def read_state(self, path) -> dict:
        return {}

    def net_config(self) -> dict:
        return {}


class JournalTest(unittest.TestCase):
    def test_split_bracketed(self) -> None:
        ts, text = state.split_journal_line("[2026-11-23 15:03:02] СЕТЬ: путь живой")
        self.assertEqual(ts, "2026-11-23 15:03:02")
        self.assertEqual(text, "СЕТЬ: путь живой")

    def test_split_plain(self) -> None:
        ts, text = state.split_journal_line("2026-11-23 15:22:58 — p-minigen: клетка 7")
        self.assertEqual(ts, "2026-11-23 15:22:58")
        self.assertIn("p-minigen", text)

    def test_split_without_time(self) -> None:
        ts, text = state.split_journal_line("просто строка")
        self.assertEqual(ts, "")
        self.assertEqual(text, "просто строка")


class ReasonTest(unittest.TestCase):
    def test_balance_wins(self) -> None:
        reason = state.classify_reason(
            "отказ провайдера 50110 insufficient balance — перезапускаю область")
        self.assertEqual(reason, "отказ по очкам")

    def test_network(self) -> None:
        self.assertEqual(state.classify_reason("ход упал: fetch failed"),
                         "обрыв сети")

    def test_vendor(self) -> None:
        self.assertEqual(state.classify_reason("The model service is unstable"),
                         "нестабильность вендора")

    def test_unknown(self) -> None:
        self.assertEqual(state.classify_reason("привет"), "прочее")

    def test_event_kind(self) -> None:
        self.assertEqual(state._event_kind("сессия сжата рантаймом, ротирую"), "ротация")
        self.assertEqual(state._event_kind("«Продолжай» без перезагрузки"), "оживление")
        self.assertEqual(state._event_kind("лечу область, беру аккаунт"), "лечение")
        self.assertEqual(state._event_kind("просто строка"), "прочее")


class StatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "journal.log"
        # ⚠️ Живой случай 24.11: метки времени в журнале считались от «вчера
        # 10:00», а окно статистики — «последние сутки» (сейчас минус день).
        # Ровно в 10:00 вчерашние записи выпадали из окна, и тест падал сам
        # собой — в зависимости от часов, а не от правки. Поэтому метки
        # времени считаются от «сейчас».
        now = time.time()

        def stamp(offset: float) -> str:
            # Журнал живёт в московском времени (заказ владельца 24.11), и
            # окно статистики фильтрует по той же зоне — иначе на машине с
            # другой зоной записи выпадали бы из окна.
            return datetime.fromtimestamp(now - offset, MSK).strftime("%Y-%m-%d %H:%M:%S")

        self.journal.write_text(
            "\n".join([
                "[%s] СЕССИИ: brain2 — сессия abc сжата рантаймом, ротирую" % stamp(300),
                "[%s] СЕССИИ: p-matricarmz — перезапускаю область: отказ 50110" % stamp(200),
                "[%s] СЕТЬ: страна выхода US вместо NL — путь живой" % stamp(100),
                "[%s] помидоры" % stamp(50),
            ]),
            encoding="utf-8",
        )
        core = FakeCore(self.journal, areas=[
            {"slug": "brain2", "title": "Управляющий", "dir": Path("p-brain2")},
            {"slug": "matricarmz", "title": "Матрица", "dir": Path("p-matricarmz")},
        ])
        self.mods = {"core": core}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stats_counts_events(self) -> None:
        stats = state.stats_summary(self.mods, days=1)
        self.assertEqual(stats["restarts"], 2)
        self.assertEqual(stats["by_reason"].get("компактизация контекста"), 1)
        self.assertEqual(stats["by_reason"].get("отказ по очкам"), 1)
        self.assertEqual(stats["by_area"][0]["slug"], "brain2")
        self.assertTrue(stats["events"])

    def test_network_lines_not_counted_as_restart(self) -> None:
        stats = state.stats_summary(self.mods, days=1)
        self.assertNotIn("ручной перезапуск", stats["by_reason"])

    def test_journal_filter(self) -> None:
        lines = state.read_journal(self.mods, limit=50, contains="ротирую")
        self.assertEqual(len(lines), 1)
        self.assertIn("ротирую", lines[0]["text"])


class SnapshotTest(unittest.TestCase):
    """Снимок — то, что показывает окно. Он обязан быть полным.

    ⚠️ Живой случай 24.11: команда `state.snapshot` падала (TypeError в
    `fmt_ms`), мост отдавал «успешный» пустой ответ — и окно показывало
    «0 областей», «демоны 0/0», «надзирателя нет», не сказав ни слова о
    поломке. Поэтому снимок проверяем целиком, на боевой подписи ядра.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.journal = root / "journal.log"
        self.journal.write_text("[2026-11-24 10:00:00] СЕТЬ: путь живой\n",
                                encoding="utf-8")
        core = FakeCore(self.journal, areas=[
            {"slug": "brain2", "title": "Управляющий", "dir": Path("p-brain2")},
            {"slug": "matricarmz", "title": "Матрица", "dir": Path("p-matricarmz")},
        ])
        core.CONFIG.write_text(json.dumps({"projects": []}), encoding="utf-8")
        self.mods = {"core": core}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_has_areas_and_daemons(self) -> None:
        snap = state.snapshot(self.mods, include_sessions=False)
        self.assertEqual(len(snap["areas"]), 2)
        self.assertIsInstance(snap["daemons"], dict)
        self.assertIn("daemons", snap["daemons"])
        self.assertIn("stats", snap)

    def test_snapshot_survives_core_signature(self) -> None:
        """Подпись ядра — как в бою: `fmt_ms(ms)`, без второго аргумента."""
        with self.assertRaises(TypeError):
            self.mods["core"].fmt_ms(int(time.time() * 1000), "%Y-%m-%d %H:%M:%S")
        # …и снимок при этом собирается.
        state.snapshot(self.mods, include_sessions=False)


class MskStampTest(unittest.TestCase):
    """Граница окна статистики — по Москве, в формате журнала."""

    def test_stamp_matches_moscow(self) -> None:
        ms = int(time.time() * 1000)
        stamp = state.msk_stamp(ms)
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        # Москва — фиксированное смещение +3, независимо от зоны машины.
        self.assertEqual(stamp, datetime.fromtimestamp(ms / 1000, MSK).strftime(
            "%Y-%m-%d %H:%M:%S"))


class SnapshotPointsTest(unittest.TestCase):
    """В снимке должны быть очки — иначе столбец «Очки» пуст.

    ⚠️ Живой случай 24.11: очки лежали в кэше моста, но в снимок не
    попадали ⇒ в окне столбец «Очки» всегда был «—», и сортировать по
    нему было нечего.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        journal = root / "journal.log"
        journal.write_text("", encoding="utf-8")
        core = FakeCore(journal, areas=[
            {"slug": "p-brain", "title": "Мозг", "dir": Path("p-brain")},
        ])
        core.CONFIG.write_text(json.dumps({"projects": []}), encoding="utf-8")
        import minigen_bridge as bridge_mod
        self.bridge = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
        self.bridge.mods = {"core": core}
        self.bridge._points = {"p-brain": {"points_today": 7}}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_carries_the_points(self) -> None:
        snap = self.bridge.cmd_snapshot({})
        self.assertEqual(len(snap["areas"]), 1)
        self.assertEqual(snap["areas"][0]["points"]["points_today"], 7)

    def test_area_without_points_has_none(self) -> None:
        self.bridge._points = {}
        snap = self.bridge.cmd_snapshot({})
        self.assertIsNone(snap["areas"][0]["points"])


class DaemonDirTest(unittest.TestCase):
    """Где мост ищет ядро — и что делает, если живой копии нет.

    ⚠️ 23.11: тут была ошибка (`REPO_TOOLS` считали неверно), и её нашла
    встроенная нейросеть. Первая правка тоже была неверной, а проверки
    проходили — потому что запасной путь не был покрыт тестом. Теперь
    покрыт.
    """

    def test_repo_tools_holds_the_core(self) -> None:
        self.assertTrue((state.REPO_TOOLS / "minigen_core.py").is_file(),
                        "REPO_TOOLS указывает не туда: %s" % state.REPO_TOOLS)
        self.assertTrue((state.REPO_TOOLS / "minimax-sessionwatch.py").is_file(),
                        "в эталонах нет сторожа сессий: %s" % state.REPO_TOOLS)

    def test_fallback_uses_repo_copy(self) -> None:
        import tempfile
        old = state.LIVE_BIN
        # подменяем именно константу модуля: она читается при импорте
        state.LIVE_BIN = Path(tempfile.gettempdir()) / "no-such-bin"
        try:
            base = state.daemon_dir()
        finally:
            state.LIVE_BIN = old
        self.assertEqual(base, state.REPO_TOOLS)
        self.assertTrue((base / "minigen_core.py").is_file())


class ProgramVersionTest(unittest.TestCase):
    """Программа должна знать свою версию (урок 23.11: «программа без версии»)."""

    def test_version_from_installed_copy(self) -> None:
        import tempfile
        from pathlib import Path
        import json

        old = state.HERE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "version.json").write_text(
                json.dumps({"commit": "abc1234", "installed_at": 1700000000,
                            "source": "D:/repo"}), encoding="utf-8")
            bridge_dir = root / "app" / "bridge"
            bridge_dir.mkdir(parents=True)
            state.HERE = bridge_dir
            try:
                version = state.program_version()
            finally:
                state.HERE = old
        self.assertTrue(version["installed"])
        self.assertEqual(version["commit"], "abc1234")
        self.assertEqual(version["source"], "D:/repo")

    def test_version_without_install_file(self) -> None:
        version = state.program_version()
        self.assertIn("commit", version)
        self.assertIn("running_from", version)

    def test_version_in_development_shows_the_commit(self) -> None:
        """Запуск из дерева (разработка): версия — коммит рабочего дерева.

        ⚠️ Живой случай 24.11: в мосте не было константы `REPO`, и
        `program_version()` падал с NameError (его ловили) ⇒ в окне
        разработки было «Версия: неизвестна».
        """
        version = state.program_version()
        self.assertTrue(version["commit"],
                        "коммит рабочего дерева не прочитан: %s" % version)
        self.assertIn("рабочее дерево", version["source"])


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._old = state.SETTINGS_FILE
        state.SETTINGS_FILE = Path(self.tmp.name) / "settings.json"

    def tearDown(self) -> None:
        state.SETTINGS_FILE = self._old
        self.tmp.cleanup()

    def test_defaults_and_write(self) -> None:
        cur = state.read_settings()
        self.assertIn("refreshMs", cur)
        state.write_settings({"refreshMs": 9000, "чужак": 1})
        again = state.read_settings()
        self.assertEqual(again["refreshMs"], 9000)
        self.assertNotIn("чужак", again)


class ParseMessageTest(unittest.TestCase):
    def test_text_and_tool_use(self) -> None:
        data = {
            "content": [
                {"type": "thinking", "thinking": "…"},
                {"type": "text", "text": "Смотрю."},
                {"type": "tool_use", "id": "t1", "name": "areas.list", "input": {}},
            ]
        }
        blocks, text, calls = agent.parse_message(data)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(text, "Смотрю.")
        self.assertEqual(calls[0]["name"], "areas.list")
        self.assertEqual(calls[0]["id"], "t1")


class AgentTest(unittest.TestCase):
    """Агент-цикл: безопасное — сразу, опасное — через подтверждение."""

    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[dict] = []

        def fake_chat(messages, system, tool_payload, cfg, timeout=120):
            return self.responses.pop(0)

        self._orig_chat = agent.chat_completion
        self._orig_prompt = agent.system_prompt
        agent.chat_completion = fake_chat
        agent.system_prompt = lambda: "системный промпт"

        def fake_config(area_slug: str) -> dict:
            return {"base_url": "http://local", "model": "test", "token": "t",
                    "api_key": "", "headers": {}}

        self._orig_config = agent.provider_config
        agent.provider_config = fake_config

    def tearDown(self) -> None:
        agent.chat_completion = self._orig_chat
        agent.system_prompt = self._orig_prompt
        agent.provider_config = self._orig_config

    def _agent(self) -> agent.Agent:
        tools_list = [
            {"name": "safe.one", "description": "", "parameters": {},
             "command": "areas.list", "dangerous": False},
            {"name": "risky.one", "description": "", "parameters": {},
             "command": "areas.restart", "dangerous": True},
        ]

        def dispatch(cmd, args):
            self.calls.append((cmd, args))
            return {"ok": True}

        return agent.Agent(dispatch, tools_list)

    def test_safe_tool_runs_immediately(self) -> None:
        self.responses = [
            {"content": [{"type": "tool_use", "id": "t1", "name": "safe.one", "input": {"a": 1}}]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        result = self._agent().ask([], "сделай")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["text"], "Готово.")
        self.assertEqual(self.calls, [("areas.list", {"a": 1})])

    def test_dangerous_tool_waits_confirmation(self) -> None:
        self.responses = [
            {"content": [{"type": "tool_use", "id": "t2", "name": "risky.one", "input": {"slug": "brain"}}]},
        ]
        cli = self._agent()
        result = cli.ask([], "перезапусти")
        self.assertEqual(result["status"], "confirm")
        self.assertEqual(result["pending"]["tool"], "risky.one")
        self.assertEqual(self.calls, [])

        self.responses = [{"content": [{"type": "text", "text": "Перезапустил."}]}]
        result = cli.confirm(True, result["history"] or [])
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.calls, [("areas.restart", {"slug": "brain"})])

    def test_dangerous_tool_can_be_declined(self) -> None:
        self.responses = [
            {"content": [{"type": "tool_use", "id": "t3", "name": "risky.one", "input": {"slug": "brain"}}]},
        ]
        cli = self._agent()
        result = cli.ask([], "перезапусти")
        result = cli.confirm(False, result["history"] or [])
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.calls, [])


class ToolCatalogTest(unittest.TestCase):
    def test_every_tool_has_command(self) -> None:
        source = (BRIDGE_DIR / "minigen_bridge.py").read_text(encoding="utf-8")
        known = set(re.findall(r'"([a-z_]+\.[a-z_]+)":\s*self\.cmd_', source))
        for tool in tools.TOOLS:
            self.assertIn(tool["command"], known,
                          "инструмент %s ссылается на неизвестную команду %s"
                          % (tool["name"], tool["command"]))

    def test_dangerous_tools_have_hint(self) -> None:
        for tool in tools.TOOLS:
            if tool["dangerous"]:
                self.assertTrue(tool["confirm_hint"],
                                "у опасного инструмента %s нет пояснения" % tool["name"])

    def test_tool_schema_is_valid(self) -> None:
        for tool in tools.TOOLS:
            params = tool["parameters"]
            self.assertEqual(params.get("type"), "object")
            json.dumps(params)          # схема должна быть сериализуемой


if __name__ == "__main__":
    unittest.main()
