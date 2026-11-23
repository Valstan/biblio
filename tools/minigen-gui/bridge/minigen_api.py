"""Локальный API программы «МиниГен» — для демонов и для проверок.

Зачем. Сетевой сторож после смены прокси должен перезапустить «контур»:
процессы, запущенные до смены, наследуют **старые** прокси-переменные и
продолжают ходить по мёртвому пути. Раньше это делал супервизор старой
панели (`127.0.0.1:8098/restart-contour`) — после архивирования панели
(23.11, Ф6) звать стало некуда, и восстановление сети работало наполовину:
прокси переключался, а лечащий продолжал работать со старым окружением.

Теперь рольprocess-менеджера у программы, значит и перезапуск контура —
её. API минимальный, слушает только локальный адрес и требует токен:

| Запрос | Что делает |
|---|---|
| `GET /status` | состояние демонов |
| `POST /restart-contour` | перезапуск лечащего и сторожа сессий (сетевого сторожа — нет: он в этот момент работает) |
| `POST /restart?name=<демон>` | перезапуск конкретного демона |

Токен лежит в `%LOCALAPPDATA%\\minigen-gui\\bridge-token.txt` и создаётся при
первом запуске. Он нужен, чтобы случайная веб-страница не могла
перезапускать демоны через localhost.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import minigen_daemons as daemons

PORT = 8097
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
TOKEN_FILE = LOCAL / "bridge-token.txt"


def token() -> str:
    """Токен доступа (создаётся при первом обращении)."""
    try:
        if TOKEN_FILE.is_file():
            value = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    value = secrets.token_urlsafe(24)
    try:
        LOCAL.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


def contour_daemons() -> list[dict[str, str]]:
    """Демоны, которым нужен свежий прокси.

    Сетевой сторож исключён: он инициировал переключение и не должен
    умирать на середине восстановления (как и раньше: супервизор его не
    трогал).
    """
    return [spec for spec in daemons.DAEMONS
            if spec["script"] != "minimax-netwatch.py"]


def restart_contour() -> dict[str, Any]:
    """Перезапустить контур (лечащий + сторож сессий) со свежим окружением."""
    results = []
    for spec in contour_daemons():
        results.append(dict(daemons.restart(spec), name=spec["name"]))
    return {"ok": all(r.get("ok") for r in results), "results": results}


class Handler(BaseHTTPRequestHandler):
    server_version = "MiniGen/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:   # тихо
        pass

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        given = (self.headers.get("X-MiniGen-Token")
                  or (query.get("token") or [""])[0])
        return bool(given) and given == token()

    def do_GET(self) -> None:                               # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path != "/status":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized(query):
            self._send(401, {"error": "нужен токен"})
            return
        self._send(200, daemons.status())

    def do_POST(self) -> None:                              # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._send(401, {"error": "нужен токен"})
            return
        if parsed.path == "/restart-contour":
            self._send(200, restart_contour())
            return
        if parsed.path == "/restart":
            name = (query.get("name") or [""])[0]
            spec = daemons.by_name(name)
            if not spec:
                self._send(404, {"error": "нет такого демона: %s" % name})
                return
            self._send(200, daemons.restart(spec))
            return
        self._send(404, {"error": "not found"})


def serve(port: int = PORT) -> None:
    """Запустить локальный API (в потоке)."""
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return None


def base_url(port: int = PORT) -> str:
    return "http://127.0.0.1:%d" % port


def call(path: str, port: int = PORT, timeout: int = 60) -> dict[str, Any]:
    """Дёрнуть свой API (из демона или проверки)."""
    import urllib.request
    url = "%s%s?token=%s" % (base_url(port), path, token())
    req = urllib.request.Request(url, method="POST" if path != "/status" else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
