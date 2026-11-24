"""Тесты «ящика отчётов» (24.11, заказ владельца).

Заказ: «инструменты МиниГена должны сами отправлять внутренней нейросети
отчёты о своей работе и ошибках, скармливать отчёты и логи своей нейросети».

Проверяем:

- **отчёт пишется и читается**: `core.report()` → строка в «ящике»,
  `core.reports()` → последние отчёты, с фильтрами (уровень, «с какого
  времени»);
- **журнал тоже попадает в ящик**: сторож не знает про нейросеть — он просто
  пишет в журнал, и это уже видно нейросети;
- **инструменты отчитываются сами**: каждый вызов команды моста оставляет
  отчёт, а ошибка — отчёт уровня `error`;
- **нейросеть получает свежие отчёты**: непрочитанные отчёты подкладываются
  в ход чата, и повторно не кормятся;
- **инструмент `reports.recent` есть** в каталоге и он безопасный.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
REPO_TOOLS = BRIDGE_DIR.parent.parent / "minimax-project-areas"
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge_mod  # noqa: E402
import minigen_tools as tools          # noqa: E402


def _load_core():
    """Боевое ядро из репозитория (как на машине, только из исходников)."""
    spec = importlib.util.spec_from_file_location(
        "mg_core_under_test", str(REPO_TOOLS / "minigen_core.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoreReportsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.core = _load_core()
        self.core.REPORTS = Path(self.tmp.name) / "reports.jsonl"
        self.core.JOURNAL = Path(self.tmp.name) / "journal.log"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_report_is_written_and_read_back(self) -> None:
        self.core.report("info", "network", "путь живой")
        self.core.report("error", "tool", "areas.restart: отказ")
        items = self.core.reports(limit=10)
        self.assertEqual(len(items), 2)
        # новые — первыми (как tail)
        self.assertEqual(items[0]["kind"], "tool")
        self.assertEqual(items[0]["level"], "error")
        self.assertEqual(items[1]["kind"], "network")
        self.assertIn("путь живой", items[1]["text"])
        self.assertTrue(items[0]["ts"] > 0)

    def test_reports_are_filtered_by_level(self) -> None:
        self.core.report("info", "log", "живо")
        self.core.report("error", "network", "умерло")
        errors = self.core.reports(limit=10, level="error")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["text"], "умерло")

    def test_reports_since_timestamp(self) -> None:
        # Пишем напрямую: время отчёта задано явно — гонки миллисекунд нет.
        with self.core.REPORTS.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": 1000, "level": "info", "kind": "log",
                                 "area": "", "text": "старое"}) + "\n")
            fh.write(json.dumps({"ts": 2000, "level": "info", "kind": "log",
                                 "area": "", "text": "новое"}) + "\n")
        fresh = self.core.reports(limit=10, since_ms=1000)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["text"], "новое")
        self.assertEqual(len(self.core.reports(limit=10)), 2)

    def test_journal_lines_land_in_the_box(self) -> None:
        self.core.log("СЕТЬ: путь живой")
        items = self.core.reports(limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "log")
        self.assertIn("путь живой", items[0]["text"])

    def test_report_never_raises(self) -> None:
        self.core.REPORTS = Path(self.tmp.name) / "no-such-dir" / "x" / "reports.jsonl"
        self.core.report("info", "log", "некуда писать")     # не должно бросить


class FakeCore:
    """Мини-ядро для моста: пишет отчёты и отдаёт заготовленные."""

    def __init__(self, items: list[dict] | None = None) -> None:
        self.written: list[tuple[str, str, str]] = []
        self.items = items or []

    def report(self, level: str, kind: str, text: str, area: str = "") -> None:
        self.written.append((level, kind, text))

    def reports(self, limit: int = 50, level: str = "", since_ms: int = 0) -> list[dict]:
        """Ведёт себя как боевое ядро: новые — первыми."""
        out = []
        for item in sorted(self.items, key=lambda i: int(i.get("ts") or 0), reverse=True):
            if level and item.get("level") != level:
                continue
            if since_ms and int(item.get("ts") or 0) <= since_ms:
                continue
            out.append(item)
        return out[:max(int(limit or 1), 1)]

    def fmt_ms(self, ms: int) -> str:
        """Время — по Москве, как в настоящем ядре (заказ владельца 24.11).

        ⚠️ Подпись — **один аргумент**, как в бою: формат зашит в модуль
        времени. Подставное ядро с двумя аргументами прятало расхождение
        с боевым (живой случай 24.11: `state.snapshot` падал в бою и
        проходил в тестах).
        """
        from datetime import datetime, timedelta, timezone
        msk = timezone(timedelta(hours=3), "MSK")
        return datetime.fromtimestamp(int(ms or 0) / 1000, msk).strftime("%d.%m %H:%M:%S")


def _bridge(core: FakeCore, handlers: dict | None = None) -> object:
    """Мост без тяжёлой инициализации: только то, что нужно для отчётов."""
    obj = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
    obj.core = core
    obj.handlers = handlers or {}
    return obj


class ToolReportsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.core = FakeCore()
        self.bridge = _bridge(self.core, {"areas.list": lambda args: {"ok": True}})

    def test_successful_call_is_reported(self) -> None:
        result = self.bridge.dispatch("areas.list", {})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(self.core.written), 1)
        level, kind, text = self.core.written[0]
        self.assertEqual(level, "info")
        self.assertEqual(kind, "tool")
        self.assertIn("areas.list", text)

    def test_failed_call_is_reported_as_error(self) -> None:
        self.bridge.handlers["boom"] = lambda args: {"error": "сломалось"}
        result = self.bridge.dispatch("boom", {})
        self.assertIn("error", result)
        level, kind, text = self.core.written[0]
        self.assertEqual(level, "error")
        self.assertEqual(kind, "tool")
        self.assertIn("сломалось", text)

    def test_exception_is_reported_as_error(self) -> None:
        def boom(args):
            raise RuntimeError("падает")
        self.bridge.handlers["boom"] = boom
        result = self.bridge.dispatch("boom", {})
        self.assertIn("падает", result["error"])
        self.assertEqual(self.core.written[0][0], "error")

    def test_chat_and_reports_are_not_reported(self) -> None:
        self.bridge.handlers["chat.send"] = lambda args: {"ok": True}
        self.bridge.handlers["reports.list"] = lambda args: {"reports": []}
        self.bridge.dispatch("chat.send", {})
        self.bridge.dispatch("reports.list", {})
        self.assertEqual(self.core.written, [])


class FeedTest(unittest.TestCase):
    """Свежие отчёты подкладываются в ход чата — и не кормятся дважды."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._old_seen = bridge_mod.REPORTS_SEEN
        bridge_mod.REPORTS_SEEN = Path(self.tmp.name) / "reports-seen.json"
        self.core = FakeCore([
            {"ts": 1000, "level": "error", "kind": "network", "text": "путь умер"},
            {"ts": 2000, "level": "info", "kind": "heal", "text": "аккаунт получен"},
        ])
        self.bridge = _bridge(self.core)

    def tearDown(self) -> None:
        bridge_mod.REPORTS_SEEN = self._old_seen
        self.tmp.cleanup()

    def test_fresh_reports_are_fed_once(self) -> None:
        feed = self.bridge._unread_reports()
        self.assertIn("путь умер", feed)
        self.assertIn("аккаунт получен", feed)
        self.assertTrue(bridge_mod.REPORTS_SEEN.is_file())
        again = self.bridge._unread_reports()
        self.assertEqual(again, "")

    def test_feed_is_ordered_old_first(self) -> None:
        feed = self.bridge._unread_reports()
        self.assertLess(feed.index("путь умер"), feed.index("аккаунт получен"))


class CatalogTest(unittest.TestCase):
    def test_reports_tool_exists_and_is_safe(self) -> None:
        tool = tools.by_name().get("reports.recent")
        self.assertIsNotNone(tool, "в каталоге нет инструмента reports.recent")
        self.assertFalse(tool["dangerous"], "чтение отчётов не должно быть опасным")
        self.assertEqual(tool["command"], "reports.list")


if __name__ == "__main__":
    unittest.main()
