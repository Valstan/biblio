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
| `POST /start?name=<демон>` / `POST /stop?name=<демон>` | запуск и остановка конкретного демона |
| `POST /start-all` / `POST /stop-all` / `POST /restart-all` | то же для всех демонов |
| `POST /manager-decision` | решение о лечении сессии: встроенная нейросеть «Управляющий» решает (промт #172) |

⚠️ Зачем окну ходить в API (живой случай 24.11): демоны, поднятые с
повышенными правами, окно не видит — WMI не отдаёт их командные строки, а
`OpenProcess` возвращает «доступ запрещён». Надзиратель (фоновый режим) их
видит всегда. Поэтому окно спрашивает демонов **у него** — иначе врало «демоны
0/3» при живых демонах, а «запустить» означало бы «поднять вторую копию»
(урок 22.11: два лечащих брали аккаунты дважды).

⚠️ `POST /manager-decision` (промт #172) — канал от сторожа сессий к
**встроенной** нейросети «Управляющий». Сервер живёт в том же процессе, что и
модель, поэтому сторож на машине получает решение той же встроенной модели,
что отвечает в чате. Тело запроса — факты о ситуации (JSON), ответ — решение:
действие (`continue` / `finish` / `rotate`), инструкция обслуживаемому агенту
и причина. Если программа не запущена или модель недоступна — ответ
`{"ok": false, ...}`, и сторож применяет **правило-фолбэк** (оно не требует
завершения сессии без отчёта о выполнении).

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

# Порт держит **один** экземпляр программы — обычно фоновый режим
# (надзиратель демонов). Флаг «порт держим мы» нужен, чтобы окно знало,
# кому говорить, а сам надзиратель не звал бы себя по сети.
SERVING = False

#: Кто решает лечение сессии (промт #172). Ставится мостом при старте:
#: `set_decider(bridge.manager_decision)`. Пока он не задан — решения нет,
#: и сторож применяет правило-фолбэк (это предсказуемо и безопасно).
DECIDER = None


def set_decider(func) -> None:
    """Задать исполнителя решения о лечении сессии."""
    global DECIDER
    DECIDER = func


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


class ApiServer(HTTPServer):
    """Сервер API, который **нельзя перехватить**.

    ⚠️ На Windows `HTTPServer` по умолчанию ставит `SO_REUSEADDR`
    (`allow_reuse_address = 1`), а с ним второй процесс **занимает тот же
    порт** — проверено на этой машине: две копии API слушали `:8097`
    одновременно. Значит, обращение сетевого сторожа («перезапусти контур»)
    могло уйти не к тому экземпляру: у одного был API без надзирателя, у
    другого — надзиратель без API. Итог — восстановление сети работает
    наполовину, а снаружи выглядит как «интернет плохой».

    Поэтому на Windows: `SO_REUSEADDR` выключаем, а эксклюзивность задаём
    `SO_EXCLUSIVEADDRUSE` (это штатный способ для серверов: перехват
    запрещён, а после закрытия порт освобождается как надо). На POSIX
    `SO_REUSEADDR` безопасен (перехватить активный слушатель нельзя), и он
    нужен, чтобы порт освобождался мгновенно после TIME_WAIT.
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            import socket

            try:
                self.socket.setsockopt(socket.SOL_SOCKET,
                                       socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass
        super().server_bind()


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

    def _read_json(self) -> dict[str, Any]:
        """Тело запроса — JSON-объект (пустой, если тела нет)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8", "replace")
            data = json.loads(raw or "{}")
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _decision(self) -> None:
        """Решение о лечении сессии — от встроенной модели (промт #172)."""
        if DECIDER is None:
            self._send(503, {"ok": False,
                             "error": "решение недоступно: программа не дала «Управляющего»"})
            return
        facts = self._read_json()
        try:
            result = DECIDER(facts)
        except Exception as exc:  # noqa: BLE001 — ответ всегда фактом
            self._send(200, {"ok": False, "error": str(exc), "source": "model"})
            return
        payload = result if isinstance(result, dict) else {"ok": False,
                                                           "error": "пустой ответ"}
        self._send(200, payload)

    def do_GET(self) -> None:                               # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            self._send(401, {"error": "нужен токен"})
            return
        # /restart-contour принимаем и на GET — старый супервизор отвечал на
        # любой метод, и поломка «GET ушёл в 404» стоила нам одного шага
        # диагностики. Лишним это не мешает: действие и так под токеном.
        if parsed.path == "/restart-contour":
            self._send(200, restart_contour())
            return
        if parsed.path != "/status":
            self._send(404, {"error": "not found"})
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
        if parsed.path == "/manager-decision":
            self._decision()
            return
        if parsed.path == "/start":
            name = (query.get("name") or [""])[0]
            spec = daemons.by_name(name)
            if not spec:
                self._send(404, {"error": "нет такого демона: %s" % name})
                return
            self._send(200, daemons.start(spec))
            return
        if parsed.path == "/stop":
            name = (query.get("name") or [""])[0]
            spec = daemons.by_name(name)
            if not spec:
                self._send(404, {"error": "нет такого демона: %s" % name})
                return
            self._send(200, daemons.stop(spec))
            return
        if parsed.path == "/restart-all":
            self._send(200, daemons.restart_all())
            return
        if parsed.path == "/start-all":
            self._send(200, daemons.start_all())
            return
        if parsed.path == "/stop-all":
            self._send(200, daemons.stop_all())
            return
        self._send(404, {"error": "not found"})


def serve(port: int = PORT) -> None:
    """Запустить локальный API (в потоке).

    ⚠️ Порт занимается **эксклюзивно**: если он уже занят, `serve()`
    бросает исключение, и вызывающий код обязан отказаться от роли
    менеджера (иначе получится второй надзиратель демонов — ровно то, чего
    мы избегаем). Раньше ошибка проглатывалась, и лишний процесс оставался
    жить (живой случай 23.11).
    """
    # Токен создаём СРАЗУ, при старте: сетевой сторож читает файл, чтобы
    # звать программу при смене прокси. Если токен появлялся бы только при
    # первом обращении, до него дело не дошло бы никогда (замкнутый круг:
    # позвать — нечем, токена нет; токен есть — никто не звал).
    token()
    server = ApiServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    global SERVING
    SERVING = True
    return None


def base_url(port: int = PORT) -> str:
    return "http://127.0.0.1:%d" % port


def another_instance_running(port: int = PORT, timeout: int = 15) -> bool:
    """Живой ли уже другой фоновый экземпляр программы.

    Проверка по **ответу API**, а не по файлу-флагу: флаг может остаться
    от процесса, который упал, и тогда новый экземпляр не поднимется
    (сторожа останутся без присмотра). API отвечает — значит, экземпляр
    жив и второй поднимать не надо: два процесса-менеджера демонов — это
    ровно то, из-за чего лечащий брал аккаунты дважды.

    ⚠️ Ответ `/status` считает живые демоны (WMI по процессам) — на тяжёлой
    машине это **дольше пяти секунд**, и короткий таймаут давал ложное
    «никого нет» ⇒ второй менеджер демонов поднимался (урок 22.11). Поэтому
    ждём щедрее: «медленный ответ» — это тоже ответ.
    """
    import urllib.request
    try:
        req = urllib.request.Request(
            base_url(port) + "/status",
            headers={"X-MiniGen-Token": token()})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def status(port: int = PORT, timeout: int = 10) -> dict[str, Any]:
    """Ответ API о демонах (пусто — API не отвечает).

    ⚠️ Именно так окно спрашивает про демонов: надзиратель (фоновый режим)
    смотрит их **своими** правами. Окно, запущенное обычным пользователем,
    не читает командные строки демонов, поднятых с повышенными правами
    (WMI отдаёт пустоту) ⇒ окно врало «демоны 0/3» (живой случай 24.11).
    """
    import urllib.request
    try:
        req = urllib.request.Request(
            base_url(port) + "/status",
            headers={"X-MiniGen-Token": token()})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def call_or_none(path: str, port: int = PORT, timeout: int = 60) -> dict[str, Any] | None:
    """Дёрнуть свой API; `None` — API не отвечает (а не «ошибка ответа»).

    Разница важна: «API не отвечает» ⇒ делаем сами, «API ответил ошибкой» ⇒
    ошибку и показываем.
    """
    import urllib.request
    url = "%s%s?token=%s" % (base_url(port), path, token())
    req = urllib.request.Request(url, method="POST" if path != "/status" else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


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
