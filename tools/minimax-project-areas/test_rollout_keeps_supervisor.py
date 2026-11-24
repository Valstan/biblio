# -*- coding: utf-8 -*-
"""Приёмка: раскатка оставляет программе **надзирателя** (живой случай 24.11).

Что было (живая машина, 24.11, 07:31): после раскатки порт `:8097` не
слушался, локального API не было, а демоны жили — их поднял лечащий. То есть
надзирателя демонов не было вовсе, а раскатка рапортовала «готово»: она
проверяла демонов, а не надзирателя.

Причина: раскатка убивала фонового менеджера и **сразу** поднимала нового.
Порт `:8097` ещё держал умирающий процесс ⇒ новый мост не мог поднять API и
выходил («локальный API не поднялся — выхожу»).

Проверяем:

- **раскатка ждёт ухода менеджеров** перед стартом нового;
- **раскатка проверяет API**, а не только «процесс запустился»;
- **проверка ответа API работает**: на закрытом порту — пусто, на живом
  сервере — ответ;
- **провал надзирателя ⇒ раскатка не «зелёная»**.

Запуск: python3 tools/minimax-project-areas/test_rollout_keeps_supervisor.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = pathlib.Path(__file__).resolve().parent
FAILURES: list[str] = []


def check(cond: bool, message: str) -> None:
    if cond:
        print("   [ок] " + message)
    else:
        FAILURES.append(message)
        print("   FAIL: " + message)


def load_rollout():
    spec = importlib.util.spec_from_file_location(
        "rollout_under_test", str(HERE / "minigen_rollout.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"alive_count": 3, "total": 3,
                           "supervisor": {"pid": 4242, "alive": True}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # тишина
        return


def main() -> int:
    print("=== раскатка оставляет надзирателя демонов ===")
    rollout = load_rollout()
    source = (HERE / "minigen_rollout.py").read_text(encoding="utf-8-sig")

    print("1) ответ локального API")
    check(callable(getattr(rollout, "api_status", None)),
          "в раскатке есть проверка ответа API")
    check(callable(getattr(rollout, "wait_api", None)),
          "в раскатке есть ожидание ответа API")

    # токен: без него запрос не делается (API без токена не ответит)
    tmp_token = pathlib.Path(tempfile.gettempdir()) / "mg-test-token.txt"
    tmp_token.write_text("test-token", encoding="utf-8")
    try:
        # закрытый порт ⇒ пусто
        closed = rollout.api_status(port=9, token_file=tmp_token)
        check(closed == {}, "на закрытом порту ответ пустой (получили %r)" % (closed,))

        # живой сервер ⇒ ответ
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            data = rollout.api_status(port=port, token_file=tmp_token)
        finally:
            server.shutdown()
        check(bool(data), "живой API отвечает")
        check((data or {}).get("alive_count") == 3, "в ответе есть счётчик демонов")

        # без токена запрос не делается — это тоже по умолчанию
        check(rollout.api_status(port=port,
                                 token_file=pathlib.Path(tempfile.gettempdir())
                                 / "mg-no-such-token.txt") == {},
              "без токена ответ пустой (запрос не делается)")
    finally:
        try:
            tmp_token.unlink()
        except OSError:
            pass

    print("2) остановка менеджеров ждёт их ухода")
    stop_src = re.search(r"def stop_managers\(.*?(?=\ndef )", source, re.S)
    stop_src = stop_src.group(0) if stop_src else ""
    check("manager_processes()" in stop_src and "time.sleep" in stop_src,
          "stop_managers ждёт, пока менеджеры уйдут")

    print("3) старт надзирателя проверяется по API")
    start_src = re.search(r"def start_installed_manager\(.*?(?=\ndef )", source, re.S)
    start_src = start_src.group(0) if start_src else ""
    check("wait_api" in start_src, "start_installed_manager ждёт ответа API")
    check("return False" in start_src,
          "start_installed_manager честно говорит «не поднялся»")
    check("надзиратель не поднялся" in start_src,
          "о провале надзирателя сказано вслух")

    print("4) провал надзирателя делает раскатку не-зелёной")
    main_src = re.search(r"def main\(.*", source, re.S)
    main_src = main_src.group(0) if main_src else ""
    check(bool(re.search(r"if start_installed_manager\(\):.*?else:\s*\n\s*ok = False",
                         main_src, re.S)),
          "ветка «надзиратель не поднялся» ставит ok = False")

    if FAILURES:
        print("❌ приёмка надзирателя: FAIL (%d)" % len(FAILURES))
        for item in FAILURES:
            print("   - %s" % item)
        return 1
    print("✅ приёмка надзирателя: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
