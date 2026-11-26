"""Управление демонами «МиниГена» из самой программы.

Зачем этот модуль. Раньше демонов держал супервизор старой панели
(`mcode-panel-supervisor.js`): он поднимал веб-панель `:8099` и страницу
управления `:8098`, а вместе с ними — лечащего и двух сторожей. Старая
панель архивирована (23.11, фаза Ф6), и демонов держит сама программа
«МиниГен»: окно владельца и headless-режим при входе в Windows — это одна и
та же программа, просто в двух режимах.

Правила:

- **демон — отдельный процесс**, у каждого своя кулдаун-логика; смешивать
  их в один цикл нельзя (лечащий лечит АККАУНТЫ, сетевой сторож — СЕТЬ,
  сторож сессий — СЕССИИ);
- **второй экземпляр не поднимаем**: перед стартом проверяем, не жив ли уже
  такой процесс (правило «один писатель на область» и «демоны не должны
  дублироваться» — урок 22.11: две копии лечащего брали аккаунты у Кармана
  дважды);
- **надзиратель — один**: следить за демонами имеет право только владелец
  системной блокировки (`minigen_lock`). Надзор нужен и фоновой копии, и
  окну программы — но получают его по очереди, а не вдвоём; иначе два
  надзирателя поднимают демона в один и тот же момент (живой случай 23.11:
  демоны перезапустились дважды за две минуты);
- **процесс переживает окно**: демоны запускаются отсоединёнными
  (DETACHED_PROCESS), поэтому закрытие окна программы их не убивает;
- **страховка без программы**: если демон упал, а супервизора нет, он
  поднимает себя сам (`minigen_core.respawn_self`);
- **прокси-переменные — из реестра**: окружение демонов обновляется перед
  каждым стартом (иначе после починки прокси процессы не наследуют новые
  значения).
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import minigen_lock

# Три демона: имя — для журнала и окна, скрипт — что запускать.
DAEMONS = (
    {"name": "лечащий", "script": "minimax-autheal.py", "role": "аккаунты"},
    {"name": "сетевой сторож", "script": "minimax-netwatch.py", "role": "сеть"},
    {"name": "сторож сессий", "script": "minimax-sessionwatch.py", "role": "сессии"},
)

RESPAWN_EVERY = 60          # как часто проверять, что демоны живы
GRACE = 20                  # пауза перед подъёмом: демон мог подняться сам
START_TIMEOUT = 20          # сколько ждём появления процесса

#: Демон опознаётся по командной строке, которая **ЗАКАНЧИВАЕТСЯ** именем
#: скрипта: демон запускается без аргументов, а разовые вызовы (`--send`,
#: `--list`, `--status`) идут с аргументами и демоном не являются — иначе
#: «демон жив» окажется правдой, пока самого демона нет.
#:
#: ⚠️ Кавычка в конце допустима: путь с пробелом Windows оборачивает в
#: кавычки (`"C:\...\minimax-autheal.py"`), и без этого демон выглядел бы
#: мёртвым ⇒ надзиратель поднял бы второго (урок 22.11).
DAEMON_CMD_RE = re.compile(r"(minimax-(?:netwatch|sessionwatch|autheal)\.py)\"?\s*$", re.IGNORECASE)

#: Сколько живёт ответ «кто из демонов жив» (сек).
#:
#: ⚠️ 26.11 (живой замер на установленной программе): окно спрашивает снимок
#: состояния каждые 5 с, а снимок стоил **12,6–28,2 с** — из-за трёх вызовов
#: PowerShell (WMI) по одному на демона. Ответы копились, мост отвечал по
#: очереди, и окно голодало («мост не ответил»).
#:
#: Кэш — **только здесь** (путь отображения). `start`/`stop`/`supervise_loop`
#: спрашивают процессы напрямую (`daemon_pids`) и должны видеть их СВЕЖИМИ:
#: устаревший ответ ⇒ «уже работает» ⇒ демон не поднимется, либо наоборот
#: поднимется второй (урок 22.11: две копии лечащего брали аккаунты дважды).
STATUS_TTL = 10.0        # ≈ два интервала опроса окна по умолчанию

_STATUS_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def _bin() -> Path:
    return Path(os.environ.get("MINIGEN_BIN", r"C:\Users\valstan\bin"))


def script_path(spec: dict[str, str]) -> Path:
    return _bin() / spec["script"]


def _ps(cmd: str, timeout: int = 30) -> str:
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                              capture_output=True, text=True, errors="replace",
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return proc.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def daemon_pids_all() -> dict[str, list[int]]:
    """PID'ы всех демонов — ОДНИМ вызовом PowerShell.

    ⚠️ 26.11: раньше на каждого демона уходил свой вызов
    (`Get-CimInstance Win32_Process` — 0,7–1,3 с каждый, живой замер), а на
    один снимок состояния окна — три: **2,8 с**, и под нагрузкой — десятки
    секунд. Теперь список процессов читается **один раз**, а разбор делает
    Python.

    ⚠️ Кэша тут нет намеренно: этот ответ решает, поднимать ли второго
    демона (урок 22.11). Свежесть важнее скорости — скорость обеспечивает
    кэш в `status()` (см. `STATUS_TTL`).
    """
    out = _ps(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    found: dict[str, list[int]] = {}
    for line in (out or "").splitlines():
        pid_text, _, command = line.partition("|")
        pid_text = pid_text.strip()
        if not pid_text.isdigit():
            continue
        match = DAEMON_CMD_RE.search(command or "")
        if not match:
            continue
        found.setdefault(match.group(1), []).append(int(pid_text))
    return found


def daemon_pids(spec: dict[str, str]) -> list[int]:
    """PID'ы процессов демона (по имени скрипта в командной строке).

    ⚠️ Условие — «командная строка ЗАКАНЧИВАЕТСЯ именем скрипта»: демон
    запускается без аргументов, а разовые вызовы (`--send`, `--list`) идут
    с аргументами и не должны считаться «живым демоном» — иначе «демон жив»
    окажется правдой, пока самого демона нет.
    """
    return list(daemon_pids_all().get(str(spec.get("script") or ""), []))


def _supervisor_info() -> dict[str, Any] | None:
    """Кто надзирает за демонами — и надзирает ли ЭТОТ процесс.

    `is_self` нужно окну, чтобы сказать владельцу человеческим языком:
    «надзиратель — это окно» или «надзиратель — фоновый режим». Без этого
    непонятно, кто держит демонов (23.11, вопрос владельца).
    """
    owner = minigen_lock.read_owner()
    if owner is None:
        return None
    owner["is_self"] = int(owner.get("pid") or 0) == os.getpid()
    return owner


def supervisor_view() -> dict[str, Any] | None:
    """Демоны глазами надзирателя (фоновый режим) — или `None`, если его нет.

    ⚠️ Живой случай 24.11: окно запущено обычным пользователем, а демоны
    подняты с повышенными правами ⇒ WMI не отдаёт их командные строки ⇒
    окно показывало «демоны 0/3», хотя демоны живы (надзиратель их видел).
    Надзиратель смотрит их своими правами — спрашиваем его.

    Ничего не спрашиваем, если порт держим **мы** (мы и есть надзиратель).
    """
    try:
        import minigen_api
        if getattr(minigen_api, "SERVING", False):
            return None
        data = minigen_api.status()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or "daemons" not in data:
        return None
    return data


def _ask_supervisor(path: str, timeout: int = 90) -> dict[str, Any] | None:
    """Попросить надзирателя сделать что-то с демонами (None — не смог)."""
    try:
        import minigen_api
        if getattr(minigen_api, "SERVING", False):
            return None
        data = minigen_api.call_or_none(path, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return None
    if data.get("error"):
        return {"ok": False, "error": str(data["error"])}
    return data


def status() -> dict[str, Any]:
    """Состояние демонов: кто жив, чем занят, где лежит скрипт.

    С 23.11 (Ф6) старая панель и её супервизор архивированы — демонов держит
    сама программа «МиниГен». Поэтому смотрим на процессы демонов, а не на
    порт 8098.

    ⚠️ Сначала спрашиваем надзирателя (фоновый режим): он смотрит демонов
    своими правами. Окно, запущенное обычным пользователем, командные строки
    демонов, поднятых с повышенными правами, не читает (WMI отдаёт пустоту)
    ⇒ окно показывало «демоны 0/3» при живых демонах (живой случай 24.11).
    """
    now = time.time()
    cached = _STATUS_CACHE.get("value")
    if cached is not None and now - float(_STATUS_CACHE.get("at") or 0.0) < STATUS_TTL:
        # Ответ свежий — отдаём его и не идём в WMI (см. `STATUS_TTL`).
        return copy.deepcopy(cached)
    result = _status_fresh()
    _STATUS_CACHE["at"] = now
    _STATUS_CACHE["value"] = result
    return copy.deepcopy(result)


def _status_fresh() -> dict[str, Any]:
    """Статус демонов — без кэша (кэш — в `status()`)."""
    remote = supervisor_view()
    if remote:
        out = dict(remote)
        supervisor = out.get("supervisor")
        if isinstance(supervisor, dict):
            supervisor = dict(supervisor)
            # «это окно» — по своему pid: ответ надзирателя говорил «это я».
            supervisor["is_self"] = int(supervisor.get("pid") or 0) == os.getpid()
            out["supervisor"] = supervisor
        return out
    found = daemon_pids_all()          # ОДИН вызов PowerShell на всех демонов
    out = []
    for spec in DAEMONS:
        pids = list(found.get(str(spec.get("script") or ""), []))
        path = script_path(spec)
        out.append({
            "name": spec["name"],
            "role": spec["role"],
            "script": str(path),
            "exists": path.is_file(),
            "alive": bool(pids),
            "pids": pids,
        })
    return {
        "daemons": out,
        "alive_count": len([d for d in out if d["alive"]]),
        "total": len(out),
        "checked_at": int(time.time() * 1000),
        # кто надзирает (по блокировке) — чтобы видеть дубль, а не гадать
        "supervisor": _supervisor_info(),
    }


def _child_env() -> dict[str, str]:
    """Окружение для демона: прокси-переменные из реестра (свежие)."""
    try:
        from minigen_state import load_daemons
        core = load_daemons()["core"]
        core.refresh_env()
    except Exception:  # noqa: BLE001
        pass
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _supervisor_path(path: str, name: str = "") -> str:
    """Путь для надзирателя (имя демона — в адресе, экранируем)."""
    import urllib.parse
    if not name:
        return path
    return "%s?name=%s" % (path, urllib.parse.quote(name))


def start(spec: dict[str, str], grace: float = 0.0) -> dict[str, Any]:
    """Поднять демона, если он не жив. Отсоединённо — переживёт окно.

    `grace` — пауза перед проверкой. Нужна, когда демон только что умер:
    он умеет поднимать себя сам (при изменении файла), и без паузы программа
    подняла бы вторую копию (урок 22.11: две копии лечащего брали аккаунты
    у Кармана дважды).

    ⚠️ Живой случай 24.11: демоны, поднятые с повышенными правами, окно
    не видит (WMI не отдаёт их командные строки) ⇒ «не нашёл» ≠ «не
    работает» ⇒ поднялся бы второй экземпляр. Поэтому сначала спрашиваем
    надзирателя — он смотрит демонов своими правами.
    """
    if grace:
        time.sleep(grace)
    remote = _ask_supervisor(_supervisor_path("/start", spec.get("script") or ""))
    if remote is not None:
        return remote
    existing = daemon_pids(spec)
    if existing:
        return {"ok": True, "already": True, "pids": existing,
                "note": "уже работает — второй не поднимаю"}
    path = script_path(spec)
    if not path.is_file():
        return {"ok": False, "error": "нет скрипта: %s" % path}
    # ⚠️ Демон должен быть БЕЗ консоли. Проверено на этой машине (23.11):
    # `DETACHED_PROCESS` даёт демону консоли нет вовсе (`GetConsoleWindow()`
    # = 0) — окно мигает не он, а его ДЕТИ: дочерний `powershell`/`cmd`
    # без `CREATE_NO_WINDOW` получает свою консоль. Поэтому флаг обязателен
    # на каждом дочернем запуске (см. `minimax-netwatch.py`, `minimax-switch.py`,
    # `minimax-autheal.py`), а не только здесь.
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        proc = subprocess.Popen(["python", str(path)], cwd=str(_bin()), env=_child_env(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        return {"ok": False, "error": "не запустился: %s" % exc}
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        pids = daemon_pids(spec)
        if pids:
            return {"ok": True, "already": False, "pids": pids, "pid": proc.pid}
        time.sleep(1)
    return {"ok": False, "error": "процесс не появился за %d с" % START_TIMEOUT}


def stop(spec: dict[str, str]) -> dict[str, Any]:
    """Остановить демона (вместе с деревом процессов).

    ⚠️ Как и в `start`: не видимый окном демон (повышенные права) мы
    останавливаем через надзирателя — иначе он бы и не остановился.
    """
    remote = _ask_supervisor(_supervisor_path("/stop", spec.get("script") or ""))
    if remote is not None:
        return remote
    pids = daemon_pids(spec)
    if not pids:
        return {"ok": True, "note": "не работал"}
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.time() + 30
    while time.time() < deadline:
        if not daemon_pids(spec):
            return {"ok": True, "pids": pids}
        time.sleep(1)
    return {"ok": False, "pids": pids, "error": "процесс не ушёл"}


def restart(spec: dict[str, str]) -> dict[str, Any]:
    """Перезапустить демона — целиком через надзирателя, если он есть.

    ⚠️ Остановка и запуск по отдельности проходят через надзирателя (см.
    `start` и `stop`), поэтому и перезапуск — через него: демоны с
    повышенными правами окно не видит и не смогло бы перезапустить.
    """
    remote = _ask_supervisor(_supervisor_path("/restart", spec.get("script") or ""))
    if remote is not None:
        return remote
    stopped = stop(spec)
    time.sleep(2)
    started = start(spec)
    return {"ok": bool(stopped.get("ok") and started.get("ok")),
            "stopped": stopped, "started": started}


def by_name(name: str) -> dict[str, str]:
    for spec in DAEMONS:
        if spec["name"] == name or spec["script"] == name:
            return spec
    return {}


def start_all() -> dict[str, Any]:
    """Поднять всех — через надзирателя, если он есть (см. `start`)."""
    remote = _ask_supervisor("/start-all")
    if remote is not None:
        return remote
    return {"results": [dict(start(spec), name=spec["name"]) for spec in DAEMONS]}


def stop_all() -> dict[str, Any]:
    """Остановить всех — через надзирателя, если он есть (см. `stop`)."""
    remote = _ask_supervisor("/stop-all")
    if remote is not None:
        return remote
    return {"results": [dict(stop(spec), name=spec["name"]) for spec in DAEMONS]}


def restart_all() -> dict[str, Any]:
    """Перезапустить всех — через надзирателя, если он есть (см. `restart`)."""
    remote = _ask_supervisor("/restart-all")
    if remote is not None:
        return remote
    return {"results": [dict(restart(spec), name=spec["name"]) for spec in DAEMONS]}


def supervise_loop(stop_flag: threading.Event | None = None,
                    lock: minigen_lock.SupervisorLock | None = None) -> None:
    """Следить за демонами: поднимать упавшего (пока программа жива).

    Вызывается из фонового потока моста. Остановка — через `stop_flag`.

    ⚠️ Пауза перед подъёмом (GRACE) — обязательна: демон умеет поднимать
    себя сам, когда его файл изменился (раскатка). Без паузы программа
    подняла бы вторую копию, а две копии лечащего брали аккаунты у КАРМАНА
    дважды (урок 22.11).

    ⚠️ Надзиратель — **один**. Блокировка (`minigen_lock`) достаётся тому
    процессу, кто первый её взял; остальные молча ждут своей очереди (и
    подхватят надзор, если владелец умрёт — блокировку снимет ОС). Поэтому
    окно программы и фоновая копия не мешают друг другу.
    """
    stop_flag = stop_flag or threading.Event()
    lock = lock if lock is not None else minigen_lock.get_lock()
    while not stop_flag.is_set():
        if not lock.acquire():
            # надзиратель есть — второй не нужен (две копии лечащего брали
            # аккаунты у КАРМАНА дважды)
            stop_flag.wait(RESPAWN_EVERY)
            continue
        for spec in DAEMONS:
            try:
                if not daemon_pids(spec):
                    res = start(spec, grace=GRACE)
                    if res.get("ok"):
                        try:
                            from minigen_state import load_daemons
                            load_daemons()["core"].log(
                                "ДЕМОНЫ: %s не жил — поднял (pid %s)"
                                % (spec["name"], (res.get("pids") or ["—"])[0]))
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
        stop_flag.wait(RESPAWN_EVERY)
