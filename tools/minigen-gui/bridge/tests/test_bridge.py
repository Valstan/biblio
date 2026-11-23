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
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_agent as agent          # noqa: E402
import minigen_state as state          # noqa: E402
import minigen_tools as tools          # noqa: E402


class FakeCore:
    """Мини-ядро: свои области, свой журнал, свой каталог."""

    def __init__(self, journal: Path, areas: list | None = None) -> None:
        self.JOURNAL = journal
        self._areas = areas or []

    def areas(self) -> list:
        return self._areas

    def running_areas(self) -> set:
        return {"p-brain"}


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
        self.journal.write_text(
            "\n".join([
                "[2026-11-23 10:00:00] СЕССИИ: brain2 — сессия abc сжата рантаймом, ротирую",
                "[2026-11-23 10:05:00] СЕССИИ: p-matricarmz — перезапускаю область: отказ 50110",
                "[2026-11-23 10:06:00] СЕТЬ: страна выхода US вместо NL — путь живой",
                "[2026-11-23 10:07:00] помидоры",
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
