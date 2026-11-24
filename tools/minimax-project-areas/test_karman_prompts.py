#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Приёмка: канал промтов раздела biblio (мандат brain D-190).

Заказ: «дом утилит ходит в КАРМАН, берёт СВОИ промты (живые, новые) вместе с
готовыми планами, уточняет план под себя, выполняет и отправляет промт в
архив».

Проверяем на подставном сервисе (тот же контракт, что у соседних проектов):

1. **Токен читается из файла, а не только из env** (урок G690: «канал закрыт ≠
   токена нет»): значение в файле побеждает значение в переменной;
2. **нет токена — ни одного запроса наружу** (честный отказ, а не 503);
3. коды ответа читаются по-человечески: 200 / 403 / 404 / 503;
4. **план отличается от промта** (заголовок начинается на «План: »);
5. **не выполнил — не архивируй**: без подтверждения выполнения запрос на
   архив не уходит вовсе;
6. архив/возврат шлют ровно те поля, которые ждёт сервис;
7. **мёртвый прокси из env не роняет запрос** (тот же урок G690);
8. значение токена не утекает в отчёт.

Запуск: python tools/minimax-project-areas/test_karman_prompts.py
"""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent

PROMPTS = [
    {"id": 11, "title": "Сделать приёмку панели", "body": "Проверь панель и почини",
     "createdAt": "2026-11-20 09:00", "archived": False},
    {"id": 12, "title": "План: обновить окно", "body": "План: добавить вкладку\nшаг 1",
     "createdAt": "2026-11-21 10:00", "archived": False},
    {"id": 13, "title": "Старый промт", "body": "уже сделано",
     "createdAt": "2026-11-10 08:00", "archived": True},
]


class Handler(BaseHTTPRequestHandler):
    """Подставной сервис: тот же контракт, что у КАРМАНА."""

    mode = 200          # 200 | 403 | 503 — как отвечает сервис сейчас
    calls: list = []    # журнал запросов: метод, путь, авторизация, тело

    def log_message(self, *args):  # тишина
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        auth = self.headers.get("Authorization") or ""
        Handler.calls.append(("GET", self.path, auth, None))
        if Handler.mode != 200:
            self._send(Handler.mode, {"error": "служебная ошибка"})
            return
        if self.path.startswith("/api/prompts/section"):
            wanted = "archived=1" in self.path
            items = [p for p in PROMPTS if bool(p["archived"]) == wanted]
            self._send(200, {"prompts": items})
            return
        self._send(404, {"error": "нет такого пути"})

    def do_POST(self):
        auth = self.headers.get("Authorization") or ""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        Handler.calls.append(("POST", self.path, auth, body))
        if Handler.mode != 200:
            self._send(Handler.mode, {"error": "служебная ошибка"})
            return
        if body.get("project") != "biblio":
            self._send(400, {"error": "чужой раздел"})
            return
        found = None
        for p in PROMPTS:
            if p["id"] == body.get("id"):
                found = p
                break
        if found is None:
            self._send(404, {"error": "такого промта в разделе нет"})
            return
        found["archived"] = bool(body.get("archived"))
        self._send(200, {"id": found["id"], "archived": found["archived"]})


def load_client():
    spec = importlib.util.spec_from_file_location(
        "karman_prompts_under_test", str(HERE / "karman_prompts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    client = load_client()
    Handler.mode = 200
    Handler.calls = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    tmp = tempfile.mkdtemp(prefix="biblio-keys-")
    keys = pathlib.Path(tmp)
    (keys / "prompts-token").write_text("token-from-file\n", encoding="utf-8")
    (keys / "vault-url").write_text("http://127.0.0.1:%d" % port, encoding="utf-8")

    saved_env = {k: os.environ.get(k) for k in
                 ("BIBLIO_KEYS_DIR", "PROMPTS_TOKEN_BIBLIO", "KARMAN_URL",
                  "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "KARMAN_USE_ENV_PROXY")}
    os.environ["BIBLIO_KEYS_DIR"] = str(keys)
    os.environ.pop("PROMPTS_TOKEN_BIBLIO", None)
    os.environ.pop("KARMAN_URL", None)
    os.environ.pop("KARMAN_USE_ENV_PROXY", None)

    try:
        print("1) токен и адрес читаются из файла (не только из env)")
        status = client.status()
        check(status["ok"], "канал не открылся: %s" % status["reason"])
        check(status["http"] == 200, "код не 200: %s" % status["http"])
        check(status["token_source"] == "file",
              "источник токена не файл: %s" % status["token_source"])
        check(status["url_source"] == "file",
              "источник адреса не файл: %s" % status["url_source"])
        check(status["prompts"] == 2, "живых промтов не 2: %s" % status["prompts"])
        check(Handler.calls and Handler.calls[0][2] == "Bearer token-from-file",
              "сервер не получил токен из файла: %s" % (Handler.calls[:1],))

        print("2) файл побеждает env (канал закрыт ≠ токена нет)")
        os.environ["PROMPTS_TOKEN_BIBLIO"] = "token-from-env"
        Handler.calls = []
        code, items = client.list_prompts()
        check(code == 200, "запрос с токеном из env/file не прошёл: %s" % code)
        check(Handler.calls[0][2] == "Bearer token-from-file",
              "взялся токен из env, а не из файла: %s" % Handler.calls[0][2])
        os.environ.pop("PROMPTS_TOKEN_BIBLIO", None)

        print("3) без токена — ни одного запроса наружу")
        Handler.calls = []
        empty = pathlib.Path(tmp) / "empty"
        empty.mkdir()
        (empty / "vault-url").write_text("http://127.0.0.1:%d" % port, encoding="utf-8")
        os.environ["BIBLIO_KEYS_DIR"] = str(empty)
        status = client.status()
        check(not status["ok"], "канал «открылся» без токена")
        check(status["http"] == 0, "код не 0: %s" % status["http"])
        check("токен" in status["reason"], "причина не про токен: %s" % status["reason"])
        check(not Handler.calls, "запрос ушёл наружу без токена: %s" % Handler.calls)
        os.environ["BIBLIO_KEYS_DIR"] = str(keys)

        print("4) план отличается от промта")
        code, items = client.list_prompts()
        check(code == 200, "список не прочитан: %s" % code)
        kinds = {i["id"]: i["kind"] for i in items}
        check(kinds.get(11) == "промт", "промт 11 не промт: %s" % kinds.get(11))
        check(kinds.get(12) == "план", "план 12 не план: %s" % kinds.get(12))
        check(len(items) == 2, "живых промтов не 2: %s" % len(items))
        code, items = client.list_prompts(archived=True)
        check(code == 200 and len(items) == 1 and items[0]["id"] == 13,
              "архивный список неверен: %s" % items)

        print("5) не выполнил — не архивируй")
        Handler.calls = []
        code, payload = client.archive(11, done=False)
        check(code == 0, "архив без подтверждения прошёл: %s" % code)
        check("не выполнил" in payload.get("error", ""),
              "причина не про «не выполнил»: %s" % payload.get("error"))
        check(not Handler.calls, "запрос на архив ушёл без подтверждения: %s" % Handler.calls)

        print("6) архив и возврат шлют ровно те поля")
        code, payload = client.archive(11, done=True)
        check(code == 200, "архив не прошёл: %s" % code)
        post = [c for c in Handler.calls if c[0] == "POST"]
        check(len(post) == 1, "запросов на архив не 1: %s" % len(post))
        if post:
            check(post[0][3] == {"project": "biblio", "id": 11, "archived": True},
                  "тело архива неверное: %s" % post[0][3])
        code, payload = client.unarchive(11)
        check(code == 200, "возврат не прошёл: %s" % code)
        post = [c for c in Handler.calls if c[0] == "POST"]
        check(post[-1][3] == {"project": "biblio", "id": 11, "archived": False},
              "тело возврата неверное: %s" % post[-1][3])

        print("7) чужой раздел и несуществующий промт")
        code, payload = client.archive(999, done=True)
        check(code == 404, "несуществующий промт: код %s" % code)
        check("нет" in client.REASONS.get(404, ""),
              "404 не расшифрован: %s" % client.REASONS.get(404))

        print("8) мёртвый прокси из env не роняет запрос")
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:10809"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:10809"
        os.environ["ALL_PROXY"] = "http://127.0.0.1:10809"
        status = client.status()
        check(status["ok"], "запрос упал на мёртвом прокси: %s" % status["reason"])

        print("9) коды ответа читаются по-человечески")
        for mode, needle in ((503, "канал не настроен"), (403, "не от того раздела")):
            Handler.mode = mode
            status = client.status()
            check(not status["ok"], "код %s прошёл как успех" % mode)
            check(needle in status["reason"],
                  "код %s не расшифрован: %s" % (mode, status["reason"]))
        Handler.mode = 200

        print("10) значение токена не утекает в отчёт")
        status = client.status()
        blob = json.dumps(status, ensure_ascii=False)
        check("token-from-file" not in blob, "значение токена попало в отчёт")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            client.main(["status"])
        check("token-from-file" not in printed.getvalue(),
              "значение токена попало в вывод команды")
    finally:
        server.shutdown()
        server.server_close()
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if failures:
        print("❌ приёмка канала промтов: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка канала промтов: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
