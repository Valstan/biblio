"""Тесты канала промтов КАРМАНА: секрет из vault, fail-closed, идемпотентность.

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем (и почему):

- **секрет канала берётся из vault-комнаты** — владелец просил «сделай
  автоматически, чтобы я ничего руками не копировал»: значение лежит в
  vault-комнате и приходит по токену комнаты, а не в файле, который надо
  копировать;
- **файл побеждает vault** (если владелец положил секрет руками — он важнее);
- **нет секрета — ни одного запроса наружу** (fail-closed: заявка не теряется,
  она лежит в репозитории агента, но в КАРМАН не уходит);
- **тела запросов ровно те, что ждёт сервис** (контракт вычитан из его
  исходников);
- **публикация идемпотентна**: повтор той же заявкой не плодит дубли в
  разделе (владелец увидит копии и не поймёт, что делать).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge   # noqa: E402
import minigen_karman as karman   # noqa: E402

SECRET = "channel-secret-from-vault"
SECTION = "MiniGen"
TITLE = "МиниГен: вкладка «Агенты» в окне"
BODY = "# Заявка\n\n## Что сделать\n1. Сделать.\n"


class Handler(BaseHTTPRequestHandler):
    """Подставной сервис: vault + машинный канал промтов."""

    calls: list = []
    secret = SECRET
    prompts: list = []

    def log_message(self, *args):  # тишина
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization") or ""
        Handler.calls.append(("GET", self.path, auth, None))
        if self.path.startswith("/api/secrets"):
            if Handler.secret:
                self._send(200, {"secrets": {"PROMPTS_INGEST_SECRET": Handler.secret}})
            else:
                self._send(404, {"error": "нет такого ключа",
                                 "missing": ["PROMPTS_INGEST_SECRET"]})
            return
        if self.path.startswith("/api/prompts/projects"):
            self._send(200, {"sections": [SECTION], "added": []})
            return
        if self.path.startswith("/api/prompts"):
            self._send(200, {"prompts": Handler.prompts})
            return
        self._send(404, {"error": "нет такого пути"})

    def do_POST(self):  # noqa: N802
        auth = self.headers.get("Authorization") or ""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        Handler.calls.append(("POST", self.path, auth, body))
        if self.path.startswith("/api/prompts/projects"):
            self._send(200, {"added": body.get("projects") or [], "sections": [SECTION]})
            return
        if self.path.startswith("/api/prompts"):
            Handler.prompts.append({"id": 77, "title": body.get("title"),
                                    "body": body.get("body")})
            self._send(201, {"id": 77, "project": body.get("project"),
                             "title": body.get("title"), "created_at": "2026-11-24"})
            return
        self._send(404, {"error": "нет такого пути"})


class KarmanVaultTest(unittest.TestCase):
    """Секрет канала берётся из vault-комнаты — автоматически."""

    def setUp(self) -> None:
        Handler.calls = []
        Handler.prompts = []
        Handler.secret = SECRET
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        keys = Path(self.tmp.name)
        (keys / "vault-url").write_text("http://127.0.0.1:%d" % self.port,
                                        encoding="utf-8")
        (keys / "room-token").write_text("skm_test-token", encoding="utf-8")
        self._old_dir = os.environ.get("BIBLIO_KEYS_DIR")
        os.environ["BIBLIO_KEYS_DIR"] = str(keys)
        self._reset_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._old_dir is None:
            os.environ.pop("BIBLIO_KEYS_DIR", None)
        else:
            os.environ["BIBLIO_KEYS_DIR"] = self._old_dir
        self._reset_cache()
        self.tmp.cleanup()

    def _reset_cache(self) -> None:
        karman._secret_cache.update({"value": "", "source": "", "at": 0.0})

    def test_secret_comes_from_vault(self) -> None:
        value, source = karman.read_secret()
        self.assertEqual(value, SECRET)
        self.assertEqual(source, "vault")

    def test_file_wins_over_vault(self) -> None:
        keys = Path(os.environ["BIBLIO_KEYS_DIR"])
        (keys / "prompts-ingest-secret").write_text("secret-from-file",
                                                    encoding="utf-8")
        self._reset_cache()
        value, source = karman.read_secret()
        self.assertEqual(value, "secret-from-file")
        self.assertEqual(source, "file")

    def test_status_reports_source(self) -> None:
        status = karman.status()
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["secret_source"], "vault")
        self.assertNotIn(SECRET, json.dumps(status, ensure_ascii=False))

    def test_publish_sends_exact_body(self) -> None:
        code, payload = karman.publish(SECTION, TITLE, BODY)
        self.assertEqual(code, 201)
        self.assertEqual(payload.get("id"), 77)
        posts = [c for c in Handler.calls if c[0] == "POST"]
        self.assertTrue(posts, "запрос на публикацию не ушёл")
        self.assertEqual(posts[-1][2], "Bearer " + SECRET)
        self.assertEqual(posts[-1][3], {"project": SECTION, "title": TITLE,
                                        "body": BODY})

    def test_ensure_project_sends_list(self) -> None:
        code, _ = karman.ensure_project("kurilka")
        self.assertEqual(code, 200)
        posts = [c for c in Handler.calls if c[0] == "POST"]
        self.assertEqual(posts[-1][3], {"projects": ["kurilka"]})

    def test_without_secret_no_request_goes_out(self) -> None:
        Handler.secret = ""
        self._reset_cache()
        status = karman.status()
        self.assertFalse(status["ok"])
        self.assertEqual(status["http"], 0)
        self.assertIn("секрет", status["reason"])
        Handler.calls = []
        code, payload = karman.publish(SECTION, TITLE, BODY)
        self.assertEqual(code, 0)
        self.assertIn("секрет", str(payload.get("error")))
        # Поиск секрета в vault — законный запрос; а вот записи промта быть
        # не должно: без секрета канал закрыт.
        writes = [c for c in Handler.calls if c[0] == "POST"]
        self.assertEqual(writes, [], "промт ушёл в КАРМАН без секрета")

    def test_publishing_is_idempotent(self) -> None:
        Handler.prompts.append({"id": 55, "title": TITLE, "body": BODY})
        found = bridge._published_prompt_id(SECTION, TITLE, BODY)
        self.assertEqual(found, 55)
        missing = bridge._published_prompt_id(SECTION, TITLE, "# другая\n")
        self.assertEqual(missing, "")

    def test_bridge_command_reports_channel_state(self) -> None:
        """`karman.status` — состояние канала для окна (без секрета)."""
        result = bridge.Bridge.cmd_karman_status(
            bridge.Bridge.__new__(bridge.Bridge), {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["secret_source"], "vault")
        self.assertIn(SECTION, result["sections"])
        self.assertNotIn(SECRET, json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
