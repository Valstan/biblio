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

import os
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


def daemon_pids(spec: dict[str, str]) -> list[int]:
    """PID'ы процессов демона (по имени скрипта в командной строке).

    ⚠️ Условие — «командная строка ЗАКАНЧИВАЕТСЯ именем скрипта»: демон
    запускается без аргументов, а разовые вызовы (`--send`, `--list`) идут
    с аргументами и не должны считаться «живым демоном» — иначе «демон жив»
    окажется правдой, пока самого демона нет.
    """
    out = _ps(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*%s' } | ForEach-Object { $_.ProcessId }"
        % spec["script"])
    pids = []
    for token in out.split():
        if token.strip().isdigit():
            pids.append(int(token.strip()))
    return pids


def status() -> dict[str, Any]:
    """Состояние демонов: кто жив, чем занят, где лежит скрипт."""
    out: list[dict[str, Any]] = []
    for spec in DAEMONS:
        pids = daemon_pids(spec)
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
        "supervisor": minigen_lock.read_owner(),
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


def start(spec: dict[str, str], grace: float = 0.0) -> dict[str, Any]:
    """Поднять демона, если он не жив. Отсоединённо — переживёт окно.

    `grace` — пауза перед проверкой. Нужна, когда демон только что умер:
    он умеет поднимать себя сам (при изменении файла), и без паузы программа
    подняла бы вторую копию (урок 22.11: две копии лечащего брали аккаунты
    у Кармана дважды).
    """
    if grace:
        time.sleep(grace)
    existing = daemon_pids(spec)
    if existing:
        return {"ok": True, "already": True, "pids": existing,
                "note": "уже работает — второй не поднимаю"}
    path = script_path(spec)
    if not path.is_file():
        return {"ok": False, "error": "нет скрипта: %s" % path}
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
    """Остановить демона (вместе с деревом процессов)."""
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
    return {"results": [dict(start(spec), name=spec["name"]) for spec in DAEMONS]}


def stop_all() -> dict[str, Any]:
    return {"results": [dict(stop(spec), name=spec["name"]) for spec in DAEMONS]}


def restart_all() -> dict[str, Any]:
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
