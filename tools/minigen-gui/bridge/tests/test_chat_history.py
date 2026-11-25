"""История чата «Управляющего» на диске (25.11, промт #129).

Заказ владельца: «Чат «Управляющий» должен помнить историю, не терять её при
смене вкладки и перезапуске окна, отвечать человеческим резюме»; и: «не путать
историю чата с «ящиком отчётов» — отчёты сторожей подкладываются в ход как
сводка, но не должны становиться репликами в истории».

Проверяем:

- **история живёт на диске машины** (вне репозитория, D-038) и переживает
  перезапуск: реплики читаются обратно;
- **предел**: 200 реплик или 1 МБ — что наступит первым;
- **запись атомарная**: временный файл не остаётся;
- **отчёты сторожей не становятся репликами**: сводка уходит в подсказку
  хода, а в истории её нет;
- **`chat.reset` чистит и файл, и память**;
- **память агента восстанавливается** из видимой истории (текстом).

⚠️ Тесты не трогают живой стор программы: пути подменяются на временные.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge_mod   # noqa: E402
import minigen_chat_store as store    # noqa: E402
import minigen_state as state          # noqa: E402

REPORT_FEED = ("Отчёты программы с прошлого раза (сторож и инструменты):\n"
               "[25.11 14:00:00] tool/info: ДЕМОНЫ: демоны подняты")


class _FakeAgent:
    """Агент-заглушка: запоминает подсказку хода и отдаёт заготовку."""

    def __init__(self, status: str = "done") -> None:
        self.status = status
        self.note = ""
        self.pending = None
        self.history: list[dict] = []

    def ask(self, history, user_text, area_slug="minigen", model="", note=""):
        self.note = note
        self.history = list(history) + [{"role": "user", "content": user_text}]
        return {"status": self.status, "text": "ответ модели",
                "calls": [{"tool": "code.read", "args": {"path": "a.py"},
                           "result": {"ok": True}}],
                "history": self.history}

    def confirm(self, approved, history, area_slug="minigen", model=""):
        return {"status": "done", "text": "действие выполнено",
                "calls": [{"tool": "code.pr", "args": {}, "result": {"ok": True}}],
                "history": list(history)}


class _FakeCore:
    def refresh_env(self) -> None:
        pass


class _StoreTestCase(unittest.TestCase):
    """Общая подмена путей стора."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.history_file = root / "chat-history.json"
        self._saved_file = store.HISTORY_FILE
        store.HISTORY_FILE = self.history_file

    def tearDown(self) -> None:
        store.HISTORY_FILE = self._saved_file
        self.tmp.cleanup()


class StoreTest(_StoreTestCase):
    def test_replies_are_kept_on_disk(self) -> None:
        store.append({"role": "user", "text": "проверь интернет"})
        store.append({"role": "assistant", "text": "интернет живой",
                      "calls": [{"tool": "network.check", "args": {},
                                  "result": {"ok": True}}]})
        items = store.load()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["text"], "проверь интернет")
        self.assertEqual(items[1]["calls"][0]["tool"], "network.check")
        self.assertTrue(self.history_file.is_file())

    def test_history_lives_outside_the_repository(self) -> None:
        store.append({"role": "user", "text": "привет"})
        repo = Path(BRIDGE_DIR).parent.parent
        self.assertNotIn(str(repo).lower(), str(self.history_file).lower())

    def test_empty_replies_are_dropped(self) -> None:
        store.append({"role": "user", "text": ""})
        store.append({"role": "user", "text": "   "})
        self.assertEqual(store.load(), [])

    def test_unknown_role_is_dropped(self) -> None:
        store.append({"role": "director", "text": "что-то"})
        self.assertEqual(store.load(), [])

    def test_message_limit(self) -> None:
        for i in range(store.MAX_MESSAGES + 25):
            store.append({"role": "user", "text": "реплика %d" % i})
        items = store.load()
        self.assertEqual(len(items), store.MAX_MESSAGES)
        # старые уходят первыми
        self.assertEqual(items[0]["text"], "реплика 25")

    def test_size_limit_drops_the_oldest(self) -> None:
        big = "я" * 60_000
        for i in range(30):
            store.append({"role": "user", "text": "%d %s" % (i, big)})
        size = self.history_file.stat().st_size
        self.assertLessEqual(size, store.MAX_BYTES)

    def test_write_is_atomic(self) -> None:
        store.append({"role": "user", "text": "привет"})
        leftovers = list(self.history_file.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [], "временный файл не должен оставаться")

    def test_broken_file_is_not_a_crash(self) -> None:
        self.history_file.write_text("{ это не json", encoding="utf-8")
        self.assertEqual(store.load(), [])
        store.append({"role": "user", "text": "снова работаем"})
        self.assertEqual(len(store.load()), 1)

    def test_clear_removes_the_file(self) -> None:
        store.append({"role": "user", "text": "привет"})
        store.clear()
        self.assertFalse(self.history_file.is_file())
        self.assertEqual(store.load(), [])

    def test_agent_memory_is_restored_from_the_visible_history(self) -> None:
        store.append({"role": "user", "text": "вопрос"})
        store.append({"role": "assistant", "text": "ответ",
                      "calls": [{"tool": "code.read", "args": {}, "result": {}}]})
        memory = store.agent_history(store.load())
        self.assertEqual(memory, [{"role": "user", "content": "вопрос"},
                                  {"role": "assistant", "content": "ответ"}])


class BridgeChatTest(_StoreTestCase):
    """Команды моста: отправка, подтверждение, очистка, чтение истории."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_settings = state.read_settings
        state.read_settings = lambda: {}
        self.bridge = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
        self.bridge.chat_history = []
        self.bridge.agent = _FakeAgent()
        self.bridge.core = _FakeCore()
        self.bridge._report_call = lambda cmd, result: None
        self.bridge._unread_reports = lambda: REPORT_FEED

    def tearDown(self) -> None:
        state.read_settings = self._saved_settings
        super().tearDown()

    def test_send_writes_both_replies_to_the_file(self) -> None:
        self.bridge.cmd_chat_send({"text": "проверь интернет"})
        items = store.load()
        self.assertEqual([i["role"] for i in items], ["user", "assistant"])
        self.assertEqual(items[0]["text"], "проверь интернет")
        self.assertEqual(items[1]["text"], "ответ модели")
        self.assertEqual(items[1]["calls"][0]["tool"], "code.read")

    def test_reports_are_a_hint_not_a_reply(self) -> None:
        """Отчёты сторожей — подсказка хода, а не реплика в истории."""
        self.bridge.cmd_chat_send({"text": "что нового?"})
        self.assertIn("Отчёты программы", self.bridge.agent.note)
        blob = json.dumps(store.load(), ensure_ascii=False)
        self.assertNotIn("Отчёты программы", blob)
        self.assertNotIn("ДЕМОНЫ", blob)

    def test_unavailable_model_leaves_an_honest_note(self) -> None:
        self.bridge.agent.status = "unavailable"
        self.bridge.agent.ask = lambda *a, **k: {
            "status": "unavailable", "error": "вендор недоступен",
            "text": "", "calls": []}
        out = self.bridge.cmd_chat_send({"text": "привет"})
        self.assertEqual(out["status"], "unavailable")
        items = store.load()
        self.assertEqual(items[-1]["role"], "system")
        self.assertIn("вендор недоступен", items[-1]["text"])

    def test_pending_confirmation_is_written_without_calls(self) -> None:
        """До подтверждения вызовов в ходе ещё нет — дублировать их нельзя."""
        self.bridge.agent.status = "confirm"
        self.bridge.cmd_chat_send({"text": "почини"})
        items = store.load()
        self.assertNotIn("calls", items[-1])

    def test_confirm_writes_the_final_reply(self) -> None:
        self.bridge.cmd_chat_confirm({"approved": True})
        items = store.load()
        self.assertEqual(items[-1]["role"], "assistant")
        self.assertEqual(items[-1]["calls"][0]["tool"], "code.pr")

    def test_reset_cleans_the_file_and_the_memory(self) -> None:
        self.bridge.cmd_chat_send({"text": "привет"})
        self.bridge.chat_history = [{"role": "user", "content": "привет"}]
        self.bridge.cmd_chat_reset({})
        self.assertEqual(self.bridge.chat_history, [])
        self.assertEqual(store.load(), [])
        self.assertFalse(self.history_file.exists())

    def test_history_command_returns_the_stored_replies(self) -> None:
        self.bridge.cmd_chat_send({"text": "привет"})
        out = self.bridge.cmd_chat_history({})
        self.assertEqual(len(out["messages"]), 2)
        self.assertEqual(out["messages"][0]["text"], "привет")
        self.assertIn("chat-history.json", out["path"])


if __name__ == "__main__":
    unittest.main()
