"""Решение о лечении сессии — встроенная нейросеть «Управляющего» (промт #172).

Заказ владельца (26.11, промт #172): «решение лечения принимает встроенная
нейросеть "Управляющего" — та же, что отвечает в чате», и «отсутствие отчёта о
выполнении — не повод требовать завершения».

Проверяем:

1. **разбор ответа модели**: ровно одно из трёх действий; синонимы; ответ не по
   схеме ⇒ решение не принимается (сторой применяет правило-фолбэк);
2. **на вход — факты**, а не простыня логов;
3. **агент спрашивает модель ровно один раз** и разбирает её ответ; модель
   недоступна ⇒ честный отказ (без «закрыть сессию»);
4. **API программы** отдаёт решение по фактам и не притворяется, что решателя
   нет.

Запуск:  python -m unittest discover -s bridge/tests -t . -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_agent as agent_mod  # noqa: E402
import minigen_api as api  # noqa: E402
import minigen_treatment as treatment  # noqa: E402


FACTS = {
    "verdict": "скомпактилась",
    "reason": "рантайм сжал контекст",
    "has_report": False,
    "report_kind": "",
    "compaction": {"event_at": 1, "marker": "context_compaction_checkpoint_generated"},
    "context": {"fill": 12.0, "peak_fill": 88.0},
    "last_turn": {"status": "completed", "idle_min": 12.0},
    "thread": {"repo": "C:/work/repo", "branch": "feat/thread-a",
               "worktree": "C:/work/repo", "prs": [], "files": ["a.py"],
               "done": "начал работу", "left": "доделать и открыть PR",
               "criteria": "гейт зелёный"},
    "parallel_sessions": [{"session_id": "sess-b", "branch": "feat/thread-b"}],
    "rules": ["ADR-0011: один пишущий агент — одно дерево"],
}


class ParseDecisionTest(unittest.TestCase):
    """Ответ модели → решение."""

    def test_three_actions(self) -> None:
        for action in treatment.ACTIONS:
            text = json.dumps({"action": action, "instruction": "иди", "reason": "потому"})
            res = treatment.parse_decision(text)
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["action"], action)
            self.assertEqual(res["instruction"], "иди")
            self.assertEqual(res["reason"], "потому")

    def test_aliases_are_normalized(self) -> None:
        for raw, want in (("продолжить", treatment.CONTINUE),
                          ("закрыть", treatment.FINISH),
                          ("новая сессия", treatment.NEW_SESSION),
                          ("перенос", treatment.NEW_SESSION),
                          ("продолжать.", treatment.CONTINUE)):
            res = treatment.parse_decision(json.dumps({"action": raw}))
            self.assertTrue(res["ok"], raw)
            self.assertEqual(res["action"], want, raw)

    def test_answer_off_schema_is_refused(self) -> None:
        # Модель ответила не по схеме — решение не принимается: сторой
        # применяет правило-фолбэк (оно не требует завершать без отчёта).
        res = treatment.parse_decision("я не знаю, что делать")
        self.assertFalse(res["ok"], res)
        self.assertIn("why", res)

    def test_json_in_code_fence(self) -> None:
        text = "```json\n{\"action\": \"завершать\", \"reason\": \"сводка есть\"}\n```"
        res = treatment.parse_decision(text)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], treatment.FINISH)

    def test_prose_answer_gives_action(self) -> None:
        res = treatment.parse_decision("Действие: новая_сессия, инструкция: продолжай нитку")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], treatment.NEW_SESSION)

    def test_empty_answer(self) -> None:
        self.assertFalse(treatment.parse_decision("")["ok"])


class FactsPromptTest(unittest.TestCase):
    """На вход модели — факты."""

    def test_prompt_contains_facts(self) -> None:
        text = treatment.user_prompt(FACTS)
        self.assertIn("скомпактилась", text)
        self.assertIn("отчёт о выполнении", text)
        self.assertIn("feat/thread-a", text)
        self.assertIn("ADR-0011", text)
        self.assertIn("параллельные", text.lower())

    def test_prompt_has_no_logs_dump(self) -> None:
        # Простыни логов в запросе быть не должно: модель получает факты.
        text = treatment.user_prompt(FACTS)
        self.assertLess(len(text), 4000)


class AgentDecisionTest(unittest.TestCase):
    """Агент спрашивает модель и разбирает ответ."""

    def setUp(self) -> None:
        self.seen: list[dict] = []
        self.responses: list[dict] = []
        self._orig_chat = agent_mod.chat_completion
        self._orig_config = agent_mod.provider_config

        def fake_chat(messages, system, tool_payload, cfg, timeout=120):
            self.seen.append({"messages": json.loads(json.dumps(messages)),
                              "system": system, "tools": tool_payload})
            if not self.responses:
                raise agent_mod.ModelUnavailable("вендор лежит")
            return self.responses.pop(0)

        agent_mod.chat_completion = fake_chat
        agent_mod.provider_config = lambda area_slug: {
            "base_url": "http://local", "model": "test", "token": "t",
            "api_key": "", "headers": {},
        }

    def tearDown(self) -> None:
        agent_mod.chat_completion = self._orig_chat
        agent_mod.provider_config = self._orig_config

    def _agent(self) -> agent_mod.Agent:
        return agent_mod.Agent(lambda cmd, args: {}, [])

    def _reply(self, text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}

    def test_decision_is_parsed(self) -> None:
        self.responses.append(self._reply(json.dumps(
            {"action": "продолжать", "instruction": "доделай нитку",
             "reason": "отчёта нет"})))
        res = self._agent().decide_treatment(FACTS, "minigen")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], treatment.CONTINUE)
        self.assertEqual(res["instruction"], "доделай нитку")
        self.assertTrue(res["model"])
        # модель спросили один раз и без инструментов (решение — не ход)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0]["tools"], [])

    def test_model_unavailable_is_honest(self) -> None:
        # Модель недоступна ⇒ честный отказ: решение принимает правило-фолбэк
        # на стороне демона (оно не требует завершать сессию без отчёта).
        res = self._agent().decide_treatment(FACTS, "minigen")
        self.assertFalse(res["ok"], res)
        self.assertIn("why", res)

    def test_off_schema_answer_is_refused(self) -> None:
        self.responses.append(self._reply("не знаю"))
        res = self._agent().decide_treatment(FACTS, "minigen")
        self.assertFalse(res["ok"], res)


class ApiDecisionTest(unittest.TestCase):
    """API программы: решение по фактам."""

    def setUp(self) -> None:
        self._old_token = api.TOKEN_FILE
        self._old_local = api.LOCAL
        self._old_decider = api.DECIDER

    def tearDown(self) -> None:
        api.TOKEN_FILE = self._old_token
        api.LOCAL = self._old_local
        api.set_decider(self._old_decider)

    def _server(self):
        server = api.HTTPServer(("127.0.0.1", 0), api.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def test_decision_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api.LOCAL = Path(tmp)
            api.TOKEN_FILE = Path(tmp) / "bridge-token.txt"
            seen: list[dict] = []

            def decider(facts):
                seen.append(facts)
                return {"ok": True, "action": treatment.CONTINUE,
                        "instruction": "доделай", "reason": "отчёта нет"}

            api.set_decider(decider)
            server = self._server()
            port = server.server_address[1]
            try:
                token = api.token()
                body = json.dumps({"facts": FACTS}).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/treat-decision?token=%s" % (port, token),
                    data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data.get("action"), treatment.CONTINUE)
                self.assertEqual(seen[0].get("verdict"), "скомпактилась")
            finally:
                server.shutdown()
                server.server_close()

    def test_without_decider_is_honest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api.LOCAL = Path(tmp)
            api.TOKEN_FILE = Path(tmp) / "bridge-token.txt"
            api.set_decider(None)
            server = self._server()
            port = server.server_address[1]
            try:
                token = api.token()
                body = json.dumps({"facts": FACTS}).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/treat-decision?token=%s" % (port, token),
                    data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                # Решателя нет ⇒ честный отказ (503): сторой применяет правило.
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(ctx.exception.code, 503)
                payload = json.loads(ctx.exception.read().decode("utf-8"))
                self.assertFalse(payload.get("ok"), payload)
            finally:
                server.shutdown()
                server.server_close()

    def test_facts_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api.LOCAL = Path(tmp)
            api.TOKEN_FILE = Path(tmp) / "bridge-token.txt"
            api.set_decider(lambda facts: {"ok": True, "action": treatment.CONTINUE})
            server = self._server()
            port = server.server_address[1]
            try:
                token = api.token()
                req = urllib.request.Request(
                    "http://127.0.0.1:%d/treat-decision?token=%s" % (port, token),
                    data=b"{}", method="POST")
                req.add_header("Content-Type", "application/json")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
