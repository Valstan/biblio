"""Протокол хода агента: на каждый вызов модели — свой ответ.

Живая проба 26.11 (промт КАРМАНА #129): вендор отвергает ход, если на
`tool_use` нет `tool_result`:

    POST {baseURL}/messages → 400 invalid params,
    tool call result does not follow tool call (2013)

Причина была в агенте: ход с двумя вызовами уходил вендору, а ответ приходил
только на один из них (на опасный) — безопасные вызовы при этом даже не
выполнялись. Теперь:

- безопасные вызовы хода выполняются сразу;
- ход модели уходит вендору только после подтверждения **всех** опасных;
- ответ уходит на **каждый** вызов, в порядке вызовов модели;
- отмена отменяет все опасные вызовы хода и модель не зовёт.

Запуск:  python -m unittest discover -s bridge/tests -t . -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_agent as agent  # noqa: E402


def _tool_use(call_id: str, name: str, args: dict) -> dict:
    """Блок вызова инструмента в ответе модели."""
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


class AgentProtocolTest(unittest.TestCase):
    """Агент-цикл: ответ вендору на каждый вызов модели."""

    def setUp(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.seen: list[list[dict]] = []
        self.responses: list[dict] = []

        def fake_chat(messages, system, tool_payload, cfg, timeout=120):
            # копия хода: тест проверяет, что **ушло** вендору
            self.seen.append(json.loads(json.dumps(messages)))
            return self.responses.pop(0)

        self._orig_chat = agent.chat_completion
        self._orig_prompt = agent.system_prompt
        self._orig_config = agent.provider_config
        agent.chat_completion = fake_chat
        agent.system_prompt = lambda: "системный промпт"
        agent.provider_config = lambda area_slug: {
            "base_url": "http://local", "model": "test", "token": "t",
            "api_key": "", "headers": {},
        }

    def tearDown(self) -> None:
        agent.chat_completion = self._orig_chat
        agent.system_prompt = self._orig_prompt
        agent.provider_config = self._orig_config

    def _agent(self, results: dict[str, object] | None = None) -> agent.Agent:
        """Агент с тремя инструментами: безопасный и два опасных."""
        tools_list = [
            {"name": "safe.one", "description": "", "parameters": {},
             "command": "areas.list", "dangerous": False},
            {"name": "risky.one", "description": "", "parameters": {},
             "command": "areas.restart", "dangerous": True,
             "confirm_hint": "перезапуск области — подтвердите"},
            {"name": "risky.two", "description": "", "parameters": {},
             "command": "areas.stop", "dangerous": True,
             "confirm_hint": "остановка области — подтвердите"},
            {"name": "big.one", "description": "", "parameters": {},
             "command": "journal.read", "dangerous": False},
            {"name": "odd.one", "description": "", "parameters": {},
             "command": "odd.read", "dangerous": False},
        ]

        def dispatch(cmd, args):
            self.calls.append((cmd, args))
            if results and cmd in results:
                return results[cmd]
            return {"ok": True, "cmd": cmd}

        return agent.Agent(dispatch, tools_list)

    @staticmethod
    def _blocks(messages: list[dict], kind: str) -> list[dict]:
        """Блоки одного вида из последнего хода, что ушёл вендору."""
        for message in reversed(messages):
            content = message.get("content")
            if isinstance(content, list) and any(
                    isinstance(block, dict) and block.get("type") == kind
                    for block in content):
                return [block for block in content if block.get("type") == kind]
        return []

    def test_every_call_gets_an_answer(self) -> None:
        """Два вызова в ходе — два ответа вендору (живой случай 25.11)."""
        self.responses = [
            {"content": [_tool_use("t1", "safe.one", {"a": 1}),
                         _tool_use("t2", "risky.one", {"slug": "brain"})]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        cli = self._agent()
        result = cli.ask([], "сделай")
        # безопасный выполнился сразу, опасный ждёт владельца
        self.assertEqual(result["status"], "confirm")
        self.assertEqual(result["pending"]["tool"], "risky.one")
        self.assertEqual(self.calls, [("areas.list", {"a": 1})])

        result = cli.confirm(True, result["history"] or [])
        self.assertEqual(result["status"], "done")
        # вендор получил ответ на **каждый** вызов
        answers = self._blocks(self.seen[-1], "tool_result")
        self.assertEqual([item["tool_use_id"] for item in answers], ["t1", "t2"])
        # и в том же порядке, в каком модель их вызвала
        asked = self._blocks(self.seen[-1], "tool_use")
        self.assertEqual([block["id"] for block in asked], ["t1", "t2"])

    def test_answers_keep_model_order(self) -> None:
        """Опасный вызван первым — ответ идёт тоже первым, а не по факту."""
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"}),
                         _tool_use("t2", "safe.one", {"a": 1})]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        cli = self._agent()
        result = cli.ask([], "сделай")
        self.assertEqual(result["status"], "confirm")
        result = cli.confirm(True, result["history"] or [])
        self.assertEqual(result["status"], "done")
        answers = self._blocks(self.seen[-1], "tool_result")
        self.assertEqual([item["tool_use_id"] for item in answers], ["t1", "t2"])

    def test_two_dangerous_calls_confirmed_one_by_one(self) -> None:
        """Два опасных вызова — два подтверждения, показывается текущий."""
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"}),
                         _tool_use("t2", "risky.two", {"slug": "minigen"})]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        cli = self._agent()
        result = cli.ask([], "сделай")
        self.assertEqual(result["status"], "confirm")
        self.assertEqual(result["pending"]["tool"], "risky.one")
        self.assertEqual(self.calls, [])

        result = cli.confirm(True, result["history"] or [])
        # первый подтверждён — спрашиваем про второй
        self.assertEqual(result["status"], "confirm")
        self.assertEqual(result["pending"]["tool"], "risky.two")
        self.assertEqual(result["pending"]["reason"],
                         "остановка области — подтвердите")
        self.assertEqual(self.calls, [("areas.restart", {"slug": "brain"})])

        result = cli.confirm(True, result["history"] or [])
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.calls, [("areas.restart", {"slug": "brain"}),
                                      ("areas.stop", {"slug": "minigen"})])
        answers = self._blocks(self.seen[-1], "tool_result")
        self.assertEqual([item["tool_use_id"] for item in answers], ["t1", "t2"])

    def test_cancel_cancels_all_dangerous_calls(self) -> None:
        """Отмена отменяет все опасные вызовы хода и модель не зовёт."""
        self.responses = [
            {"content": [_tool_use("t1", "risky.one", {"slug": "brain"}),
                         _tool_use("t2", "risky.two", {"slug": "minigen"})]},
        ]
        cli = self._agent()
        result = cli.ask([], "сделай")
        result = cli.confirm(False, result["history"] or [])
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.seen), 1)          # модель второй раз не звали
        cancelled = [call for call in result["calls"]
                     if call.get("approved") is False]
        self.assertEqual(len(cancelled), 2)

    def test_owner_sees_full_result_vendor_sees_trimmed(self) -> None:
        """Владелец видит результат целиком, вендору уходит обрезанное."""
        big = "y" * (agent.TOOL_RESULT_LIMIT + 500)
        self.responses = [
            {"content": [_tool_use("t1", "big.one", {})]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        cli = self._agent({"journal.read": big})
        result = cli.ask([], "прочитай")
        self.assertEqual(result["status"], "done")
        # подробности не выбрасываются (промт #129)
        self.assertEqual(result["calls"][0]["result"], big)
        # а в контекст модели — только лимит
        answer = self._blocks(self.seen[-1], "tool_result")[0]
        payload = json.loads(answer["content"])
        self.assertEqual(len(payload), agent.TOOL_RESULT_LIMIT)

    def test_unserializable_result_does_not_break_the_turn(self) -> None:
        """Несериализуемый результат не роняет ход и не ломает JSON ответа."""
        self.responses = [
            {"content": [_tool_use("t1", "odd.one", {})]},
            {"content": [{"type": "text", "text": "Готово."}]},
        ]
        cli = self._agent({"odd.read": {"tags": {"a", "b"}}})
        result = cli.ask([], "прочитай")
        self.assertEqual(result["status"], "done")
        json.dumps(result["calls"][0]["result"], ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
