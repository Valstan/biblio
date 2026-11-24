"""Протокол моста: окно должно видеть поломку, а не пустоту.

Живой случай 24.11: команда `state.snapshot` упала (`TypeError` в `fmt_ms`),
а мост отдал «успешный» ответ без областей и демонов — и окно показывало
«0 областей», «демоны 0/0», «надзирателя нет», молча о поломке. Демоны
при этом были живы.

Правило моста: команда, которая **упала**, — это ошибка протокола
(`ok: false` с текстом), а не «успешный» ответ с пустым содержимым.

Запуск:  python -m unittest discover -s bridge/tests -t . -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge_mod      # noqa: E402


def _bridge(handlers: dict) -> object:
    """Мост без тяжёлой инициализации — только реестр команд."""
    obj = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
    obj.handlers = handlers
    obj._report_call = lambda cmd, result: None   # отчёты тут не нужны
    return obj


class ReplyTest(unittest.TestCase):
    def test_ok_answer_carries_data(self) -> None:
        out = bridge_mod.reply("7", {"areas": [1]})
        self.assertEqual(out, {"id": "7", "ok": True, "data": {"areas": [1]}})

    def test_failed_command_is_an_error(self) -> None:
        out = bridge_mod.reply("7", {"error": "TypeError: fmt_ms()", "__failed__": True})
        self.assertFalse(out["ok"])
        self.assertIn("fmt_ms", out["error"])
        self.assertNotIn("data", out)

    def test_failure_marker_never_leaks_to_the_window(self) -> None:
        out = bridge_mod.reply("7", {"ok": True, "__failed__": True})
        self.assertFalse(out["ok"])
        self.assertNotIn("__failed__", out)

    def test_normal_answer_has_no_marker(self) -> None:
        out = bridge_mod.reply("7", {"ok": True, "areas": []})
        self.assertTrue(out["ok"])
        self.assertNotIn("__failed__", out["data"])


class DispatchFailureTest(unittest.TestCase):
    """Команда, которая упала, помечается — протокол её так и отдаст."""

    def test_raised_command_is_marked_failed(self) -> None:
        def boom(args):
            raise TypeError("fmt_ms() takes 1 positional argument but 2 were given")

        obj = _bridge({"boom": boom})
        result = obj.dispatch("boom", {})
        self.assertIn("fmt_ms", result["error"])
        self.assertTrue(result["__failed__"])
        self.assertFalse(bridge_mod.reply("1", result)["ok"])

    def test_unknown_command_is_failed(self) -> None:
        obj = _bridge({})
        result = obj.dispatch("нет.такой.команды", {})
        self.assertTrue(result["__failed__"])
        self.assertFalse(bridge_mod.reply("1", result)["ok"])

    def test_normal_answer_is_not_marked(self) -> None:
        obj = _bridge({"ok.cmd": lambda args: {"ok": True}})
        result = obj.dispatch("ok.cmd", {})
        self.assertNotIn("__failed__", result)
        self.assertTrue(bridge_mod.reply("1", result)["ok"])

    def test_answer_with_soft_error_stays_a_normal_answer(self) -> None:
        """Команда, которая ответила «не получилось» — это её ответ, а не падение."""
        obj = _bridge({"soft.cmd": lambda args: {"ok": False, "error": "нет такой области"}})
        result = obj.dispatch("soft.cmd", {})
        self.assertNotIn("__failed__", result)
        out = bridge_mod.reply("1", result)
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["error"], "нет такой области")


if __name__ == "__main__":
    unittest.main()
