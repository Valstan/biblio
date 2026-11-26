"""Ожидающее подтверждение опасного действия переживает перезапуск окна.

Заказ владельца (промт #129, п. 3–4): чат «Управляющий» не теряет историю при
смене вкладки и **перезапуске окна**. Переписка на диске уже лежит
(`minigen_chat_store.py`), а ожидающее подтверждение жило только в памяти
моста: перезапустил окно — и вопрос «подтвердите действие» исчез, а действие
молча не выполнилось. Владелец даже не знал, что на него ждали ответа.

Проверяем:

- снимок ожидания лежит на диске машины (**вне репозитория**, D-038);
- в снимке **нет токена вендора** (конфиг запроса не попадает на диск);
- после «перезапуска» агент снова ждёт **того же** подтверждения;
- подтверждение после перезапуска **выполняет** действие;
- мост отдаёт окну ожидание (`chat.pending`) и честно помечает, что оно
  восстановлено (владелец отвечает на старый вопрос);
- `chat.reset` чистит и файл ожидания.

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

import minigen_agent as agent      # noqa: E402
import minigen_bridge as bridge_mod  # noqa: E402
import minigen_pending as pending_store  # noqa: E402

TOKEN = "секретный-токен-вендора"


def _tool_use(call_id: str, name: str, args: dict) -> dict:
    """Блок вызова инструмента в ответе модели."""
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def _agent_tools() -> list[dict]:
    return [
        {"name": "safe.one", "description": "", "parameters": {},
         "command": "areas.list", "dangerous": False},
        {"name": "risky.one", "description": "", "parameters": {},
         "command": "areas.restart", "dangerous": True,
         "confirm_hint": "перезапуск области — подтвердите"},
    ]


class _StoreTestCase(unittest.TestCase):
    """Общая подмена путей стора (живой стор программы не трогаем)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pending_file = root / "chat-pending.json"
        self._saved_file = pending_store.PENDING_FILE
        pending_store.PENDING_FILE = self.pending_file

    def tearDown(self) -> None:
        pending_store.PENDING_FILE = self._saved_file
        self.tmp.cleanup()


class PendingStoreTest(_StoreTestCase):
    """Файл ожидания на диске."""

    def test_snapshot_is_kept_on_disk(self) -> None:
        pending_store.save({"tool": "risky.one", "args": {"slug": "brain"},
                             "dangerous": [{"name": "risky.one",
                                            "input": {"slug": "brain"},
                                            "id": "t1"}]})
        self.assertTrue(self.pending_file.is_file())
        snapshot = pending_store.load()
        self.assertEqual(snapshot["tool"], "risky.one")
        self.assertEqual(snapshot["dangerous"][0]["id"], "t1")

    def test_history_lives_outside_the_repository(self) -> None:
        pending_store.save({"tool": "risky.one", "args": {}})
        repo = Path(BRIDGE_DIR).parent.parent
        self.assertNotIn(str(repo).lower(), str(self.pending_file).lower())

    def test_no_file_means_no_pending(self) -> None:
        self.assertIsNone(pending_store.load())

    def test_broken_file_is_not_a_crash(self) -> None:
        self.pending_file.write_text("{ это не json", encoding="utf-8")
        self.assertIsNone(pending_store.load())

    def test_clear_removes_the_file(self) -> None:
        pending_store.save({"tool": "risky.one", "args": {}})
        pending_store.clear()
        self.assertFalse(self.pending_file.is_file())
        self.assertIsNone(pending_store.load())

    def test_save_without_a_snapshot_clears(self) -> None:
        pending_store.save({"tool": "risky.one", "args": {}})
        pending_store.save(None)
        self.assertFalse(self.pending_file.is_file())

    def test_write_is_atomic(self) -> None:
        pending_store.save({"tool": "risky.one", "args": {}})
        leftovers = list(self.pending_file.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [], "временный файл не должен оставаться")


class AgentPendingTest(unittest.TestCase):
    """Агент: снимок ожидания и его восстановление."""

    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[dict] = []

        def fake_chat(messages, system, tool_payload, cfg, timeout=120):
            return self.responses.pop(0)

        self._orig_chat = agent.chat_completion
        self._orig_prompt = agent.system_prompt
        self._orig_config = agent.provider_config
        agent.chat_completion = fake_chat
        agent.system_prompt = lambda: "системный промпт"
        agent.provider_config = lambda area_slug: {
            "base_url": "http://local", "model": "test", "token": TOKEN,
            "api_key": "", "headers": {},
        }

    def tearDown(self) -> None:
        agent.chat_completion = self._orig_chat
        agent.system_prompt = self._orig_prompt
        agent.provider_config = self._orig_config

    def _agent(self) -> agent.Agent:
        def dispatch(cmd, args):
            self.calls.append((cmd, args))
            return {"ok": True, "cmd": cmd}

        return agent.Agent(dispatch, _agent_tools())

    def test_the_token_never_reaches_the_snapshot(self) -> None:
        """⚠️ В снимке — конфиг запроса, а в нём токен вендора (D-038)."""
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"})]},
        ]
        cli = self._agent()
        cli.ask([], "перезапусти область")
        snapshot = cli.pending_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertNotIn("cfg", snapshot)
        self.assertNotIn(TOKEN, json.dumps(snapshot, ensure_ascii=False, default=str))

    def test_the_view_is_what_the_owner_sees(self) -> None:
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"})]},
        ]
        cli = self._agent()
        result = cli.ask([], "перезапусти область")
        view = cli.pending_view()
        self.assertEqual(view["tool"], "risky.one")
        self.assertEqual(view["args"], {"slug": "brain"})
        self.assertEqual(view["reason"], "перезапуск области — подтвердите")
        self.assertEqual(view, result["pending"])

    def test_restore_brings_the_same_question_back(self) -> None:
        """Перезапуск окна — и вопрос владельцу на месте."""
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"})]},
        ]
        first = self._agent()
        first.ask([], "перезапусти область")
        snapshot = first.pending_snapshot()

        second = self._agent()
        self.assertTrue(second.restore_pending(snapshot))
        view = second.pending_view()
        self.assertEqual(view["tool"], "risky.one")
        self.assertEqual(view["args"], {"slug": "brain"})
        self.assertEqual(view["reason"], "перезапуск области — подтвердите")

    def test_confirmation_after_restart_runs_the_action(self) -> None:
        """Подтверждение после перезапуска **выполняет** действие."""
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"})]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        first = self._agent()
        first.ask([], "перезапусти область")
        snapshot = first.pending_snapshot()

        second = self._agent()
        second.restore_pending(snapshot)
        result = second.confirm(True, [{"role": "user", "content": "перезапусти"}])
        self.assertEqual(result["status"], "done")
        self.assertIn(("areas.restart", {"slug": "brain"}), self.calls)

    def test_a_snapshot_without_a_queue_is_not_a_confirmation(self) -> None:
        """Снимок без очереди опасных вызовов — подтверждать нечего."""
        second = self._agent()
        self.assertFalse(second.restore_pending({"tool": "risky.one", "args": {}}))
        self.assertIsNone(second.pending_view())

    def test_restore_without_a_snapshot_clears_the_pending(self) -> None:
        cli = self._agent()
        self.assertFalse(cli.restore_pending(None))
        self.assertIsNone(cli.pending_view())


class _FakeAgent:
    """Агент-заглушка для команд моста."""

    def __init__(self, pending: dict | None = None) -> None:
        self.pending = pending

    def pending_view(self):
        if not self.pending:
            return None
        return {"tool": self.pending.get("tool", ""),
                "args": self.pending.get("args", {}),
                "reason": self.pending.get("reason", ""),
                "call_id": self.pending.get("call_id", "")}

    def pending_snapshot(self):
        return dict(self.pending) if self.pending else None

    def restore_pending(self, snapshot):
        self.pending = dict(snapshot) if snapshot else None
        return bool(self.pending)


class _FakeCore:
    def refresh_env(self) -> None:
        pass

    def log(self, msg: str) -> None:
        pass


class BridgePendingTest(_StoreTestCase):
    """Мост: восстановление при старте и команда `chat.pending`."""

    def setUp(self) -> None:
        super().setUp()
        self.bridge = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
        self.bridge.chat_history = []
        self.bridge.agent = _FakeAgent()
        self.bridge.core = _FakeCore()
        self.bridge._pending_from_disk = False

    def test_pending_command_is_empty_without_a_pending(self) -> None:
        out = self.bridge.cmd_chat_pending({})
        self.assertIsNone(out["pending"])
        self.assertFalse(out["restored"])

    def test_restored_pending_is_reported_honestly(self) -> None:
        """Владелец должен знать, что отвечает на вопрос **до** перезапуска."""
        self.bridge.agent = _FakeAgent({"tool": "risky.one",
                                        "args": {"slug": "brain"},
                                        "reason": "перезапуск области — подтвердите",
                                        "call_id": "t1"})
        self.bridge._pending_from_disk = True
        out = self.bridge.cmd_chat_pending({})
        self.assertEqual(out["pending"]["tool"], "risky.one")
        self.assertTrue(out["restored"])

    def test_reset_clears_the_pending_file(self) -> None:
        pending_store.save({"tool": "risky.one", "args": {}})
        self.bridge.cmd_chat_reset({})
        self.assertFalse(self.pending_file.is_file())
        self.assertIsNone(self.bridge.agent.pending)


if __name__ == "__main__":
    unittest.main()
