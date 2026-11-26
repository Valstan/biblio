# -*- coding: utf-8 -*-
"""Решение о лечении сессии принимает встроенная модель «Управляющий» (промт #172).

Проверяем:

1. **Разбор ответа модели**: ровно одно из трёх действий; JSON, JSON в
   ```` ``` ````, ответ словами — всё понимается; мусор ⇒ действие не
   распознано (⇒ решает правило-фолбэк, а не мы);
2. **Факты** на входе модели — факты, а не простыня логов;
3. **Запрос к модели** — тот же протокол `/messages`, и при недоступности
   вендора честный `ok: False` (фолбэк на стороне сторожа);
4. **Канал**: сторож спрашивает программу через локальный API `:8097`
   (`POST /manager-decision`) — и получает решение.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import minigen_agent as agent
import minigen_api
import minigen_manager as manager


class ParseDecisionTest(unittest.TestCase):
    """Ответ модели разбирается в решение."""

    def test_clean_json(self) -> None:
        got = manager.parse_decision(json.dumps({
            "action": "continue", "instruction": "продолжи с тестов",
            "reason": "отчёта нет"}))
        self.assertEqual(got["action"], manager.ACTION_CONTINUE)
        self.assertEqual(got["instruction"], "продолжи с тестов")
        self.assertEqual(got["reason"], "отчёта нет")

    def test_json_in_code_fence(self) -> None:
        text = "Думаю так:\n```json\n{\"action\": \"rotate\", \"reason\": \"нитка переносится\"}\n```"
        got = manager.parse_decision(text)
        self.assertEqual(got["action"], manager.ACTION_ROTATE)

    def test_russian_action_names(self) -> None:
        self.assertEqual(manager.normalize_action("продолжать"),
                         manager.ACTION_CONTINUE)
        self.assertEqual(manager.normalize_action("завершать"),
                         manager.ACTION_FINISH)
        self.assertEqual(manager.normalize_action("новая сессия с продолжением нитки"),
                         manager.ACTION_ROTATE)

    def test_plain_text_answer(self) -> None:
        got = manager.parse_decision("Продолжай работу по нитке.\n"
                                     "Причина: отчёта о выполнении нет.")
        self.assertEqual(got["action"], manager.ACTION_CONTINUE)

    def test_garbage_is_not_a_decision(self) -> None:
        got = manager.parse_decision("не знаю, что делать")
        self.assertEqual(got["action"], "",
                         "мусорный ответ не должен превращаться в решение")

    def test_finish_old_flag(self) -> None:
        got = manager.parse_decision(json.dumps({
            "action": "rotate", "finish_old": True}))
        self.assertTrue(got["finish_old"])


class FactsTest(unittest.TestCase):
    """На вход модели идут факты, а не логи."""

    def test_facts_text_has_the_essentials(self) -> None:
        text = manager.facts_text({
            "area": "minigen", "session_id": "s-1", "verdict": "скомпактилась",
            "has_report": False, "context_fill": 87.5, "idle_minutes": 12,
            "branch": "feat/x", "worktree": "D:\\repo\\wt",
            "todo": ["доделать тест"],
            "parallel_sessions": [{"session_id": "s-2", "branch": "feat/y",
                                   "worktree": "D:\\repo\\wt2"}],
        })
        self.assertIn("Отчёт о выполнении", text)
        self.assertIn("НЕТ", text)
        self.assertIn("87.5", text)
        self.assertIn("feat/x", text)
        self.assertIn("доделать тест", text)
        self.assertIn("s-2", text)

    def test_facts_are_bounded(self) -> None:
        text = manager.facts_text({"report_excerpt": "x" * 100_000})
        self.assertLessEqual(len(text), manager.FACTS_LIMIT + 100)


class DecideTest(unittest.TestCase):
    """Запрос к встроенной модели."""

    def setUp(self) -> None:
        self._old = agent.chat_completion

    def tearDown(self) -> None:
        agent.chat_completion = self._old

    def test_decision_from_model(self) -> None:
        seen = {}

        def fake(messages, system, tools, cfg, timeout=0):
            seen["messages"] = messages
            seen["system"] = system
            seen["tools"] = tools
            return {"content": [{"type": "text", "text": json.dumps({
                "action": "finish", "instruction": "закрой", "reason": "работа закончена"})}]}

        agent.chat_completion = fake
        got = manager.decide({"session_id": "s-1", "has_report": True},
                             cfg={"base_url": "http://x", "model": "m"})
        self.assertTrue(got["ok"])
        self.assertEqual(got["action"], manager.ACTION_FINISH)
        self.assertEqual(got["reason"], "работа закончена")
        self.assertEqual(seen["tools"], [], "решение лечения — без инструментов")
        self.assertIn("s-1", json.dumps(seen["messages"], ensure_ascii=False),
                      "в запрос к модели ушли не те факты")

    def test_vendor_down_is_honest(self) -> None:
        def fake(messages, system, tools, cfg, timeout=0):
            raise agent.ModelUnavailable("вендор лежит")

        agent.chat_completion = fake
        got = manager.decide({"session_id": "s-1"}, cfg={"base_url": "http://x",
                                                         "model": "m"})
        self.assertFalse(got["ok"])
        self.assertIn("вендор лежит", got["error"])

    def test_off_schema_answer_is_not_a_decision(self) -> None:
        def fake(messages, system, tools, cfg, timeout=0):
            return {"content": [{"type": "text", "text": "хм, посмотрю позже"}]}

        agent.chat_completion = fake
        got = manager.decide({"session_id": "s-1"}, cfg={"base_url": "http://x",
                                                         "model": "m"})
        self.assertFalse(got["ok"])
        self.assertIn("не по схеме", got["error"])


class ApiDecisionTest(unittest.TestCase):
    """Канал «сторож → программа → встроенная модель»."""

    def setUp(self) -> None:
        self._token_file = minigen_api.TOKEN_FILE
        self._local = minigen_api.LOCAL
        self._decider = minigen_api.DECIDER
        self.tmp = tempfile.TemporaryDirectory()
        minigen_api.LOCAL = Path(self.tmp.name)
        minigen_api.TOKEN_FILE = Path(self.tmp.name) / "bridge-token.txt"
        self.server = minigen_api.HTTPServer(("127.0.0.1", 0), minigen_api.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.token = minigen_api.token()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        minigen_api.DECIDER = self._decider
        minigen_api.TOKEN_FILE = self._token_file
        minigen_api.LOCAL = self._local
        self.tmp.cleanup()

    def _post(self, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:%d/manager-decision" % self.port, data=body,
            method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-MiniGen-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_decision_reaches_the_daemon(self) -> None:
        seen = []

        def decider(facts):
            seen.append(facts)
            return {"ok": True, "action": "continue", "instruction": "продолжи",
                    "reason": "отчёта нет"}

        minigen_api.set_decider(decider)
        code, data = self._post({"session_id": "s-1", "has_report": False})
        self.assertEqual(code, 200)
        self.assertEqual(data["action"], "continue")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["session_id"], "s-1")

    def test_without_decider_the_daemon_gets_a_fact(self) -> None:
        minigen_api.set_decider(None)
        code, data = self._post({"session_id": "s-1"})
        self.assertEqual(code, 503)
        self.assertFalse(data["ok"])
        self.assertIn("решение недоступно", data["error"])

    def test_token_is_required(self) -> None:
        minigen_api.set_decider(lambda facts: {"ok": True, "action": "continue"})
        body = json.dumps({"session_id": "s-1"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:%d/manager-decision" % self.port, data=body,
            method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
