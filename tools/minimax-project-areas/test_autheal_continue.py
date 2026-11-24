# -*- coding: utf-8 -*-
"""Приёмка: «Продолжай» **доходит до отправки**, а не падает NameError.

Живой случай 24.11 (жалоба владельца): «да и самолечение программы не
получилось, через встроенную нейросеть». В журнале:

    [2026-11-24 15:23:53] Управляющий: аккаунт получен (аккаунт 074@…, …)
    [2026-11-24 15:23:55] Управляющий: лечение сорвалось: name 'core' is not defined

То есть аккаунт у Кармана берётся, а команда «Продолжай» в область **не
уходит**: в двух местах модуля стояло `core.model_flags(area_dir)` при
неопределённом `core` (ядро грузится лениво, через `_core()`).

Что проверяем:

1. `_continue_once()` доходит до отправки команды (команда собрана, процесс
   запущен) — без NameError;
2. запасной путь «новая сессия» (когда основная занята) — тоже доходит до
   отправки;
3. ядро — **мягкая зависимость**: без него «Продолжай» всё равно уходит.

Запуск: python3 tools/minimax-project-areas/test_autheal_continue.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load(name: str):
    """Подключить модуль по файлу (имя файла ≠ имя модуля)."""
    spec = importlib.util.spec_from_file_location(
        "autheal_check_%s" % name.replace("-", "_").replace(".", "_"), str(HERE / name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeProcess:
    """Процесс, который «жив» (ход идёт) или сразу умер."""

    def __init__(self, pid: int = 4242, alive: bool = True) -> None:
        self.pid = pid
        self.returncode = 0
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class Recorder:
    """Вместо `subprocess.Popen` — запись команды (ничего не запускаем).

    `alive_seq` — поведение по вызовам: например, [умер, умер, жив] — две
    неудачные попытки и потом успешная отправка в новую сессию.
    """

    def __init__(self, alive: bool = True, message: str = "",
                 alive_seq: list[bool] | None = None) -> None:
        self.commands: list[list[str]] = []
        self._alive = alive
        self._message = message
        self._seq = list(alive_seq or [])

    def __call__(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        alive = self._seq.pop(0) if self._seq else self._alive
        out = kwargs.get("stdout")
        if self._message and out not in (None, getattr(__import__("subprocess"), "DEVNULL")):
            try:
                out.write(self._message)
                out.flush()
            except Exception:  # noqa: BLE001
                pass
        return FakeProcess(alive=alive)


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("1) модуль лечащего подключается")
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    heal = load("minimax-autheal.py")
    check(hasattr(heal, "_continue_once"), "в модуле нет _continue_once")
    check(hasattr(heal, "_model_flags"), "в модуле нет _model_flags")

    with tempfile.TemporaryDirectory() as tmp:
        area_dir = pathlib.Path(tmp) / "p-brain"
        area_dir.mkdir(parents=True, exist_ok=True)
        folder = pathlib.Path(tmp) / "work"
        folder.mkdir(parents=True, exist_ok=True)
        log_path = pathlib.Path(tmp) / "continue.log"

        print("2) «Продолжай» доходит до отправки (основной путь)")
        recorder = Recorder(alive=True)
        old_popen, old_sleep, old_log = heal.subprocess.Popen, heal.time.sleep, heal.log
        heal.subprocess.Popen = recorder
        heal.time.sleep = lambda _seconds: None
        heal.log = lambda _msg: None
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                status, info = heal._continue_once(area_dir, str(folder), fh)
        finally:
            heal.subprocess.Popen, heal.time.sleep, heal.log = old_popen, old_sleep, old_log
        check(status == "ok", "статус не «ok», а «%s» (%s)" % (status, info))
        check(len(recorder.commands) == 1, "команда не отправлена: %s" % recorder.commands)
        if recorder.commands:
            cmd = recorder.commands[0]
            check("--continue" in cmd, "в команде нет --continue: %s" % cmd)
            check(str(folder) in cmd, "в команде нет папки проекта: %s" % cmd)
            check(any("Продолжай" in part for part in cmd),
                  "в команде нет промпта «Продолжай»: %s" % cmd)

        print("3) запасной путь: сессия занята — «Продолжай» в новую сессию")
        recorder = Recorder(alive=False,
                            message="Session already has an active Turn\n",
                            alive_seq=[False, False, True])
        old = (heal.subprocess.Popen, heal.time.sleep, heal.log, heal.active_turn,
               heal.stuck_turn, heal.wait_for_idle, heal.desktop_running,
               heal.restart_area, heal.wait_activation)
        heal.subprocess.Popen = recorder
        heal.time.sleep = lambda _seconds: None
        heal.log = lambda _msg: None
        heal.active_turn = lambda _dir: None
        heal.stuck_turn = lambda _area: None
        heal.wait_for_idle = lambda _area, _seconds: True
        heal.desktop_running = lambda _slug: True
        heal.restart_area = lambda _slug: None
        heal.wait_activation = lambda _area: (True, 0, "")
        try:
            note = heal.say_continue({"dir": area_dir, "folder": str(folder),
                                      "slug": "brain", "title": "Мозг"})
        finally:
            (heal.subprocess.Popen, heal.time.sleep, heal.log, heal.active_turn,
             heal.stuck_turn, heal.wait_for_idle, heal.desktop_running,
             heal.restart_area, heal.wait_activation) = old
        check("НОВУЮ сессию" in note or "отправил" in note,
              "«Продолжай» в новую сессию не ушёл: %s" % note)
        check(len(recorder.commands) >= 1, "команда не отправлена: %s" % recorder.commands)
        if recorder.commands:
            cmd = recorder.commands[-1]
            check(any("Продолжай" in part for part in cmd),
                  "в команде нет промпта «Продолжай»: %s" % cmd)

        print("4) ядро — мягкая зависимость: без него «Продолжай» всё равно уходит")
        recorder = Recorder(alive=True)
        old_popen, old_sleep, old_log, old_core = (heal.subprocess.Popen, heal.time.sleep,
                                                    heal.log, heal._core)
        heal.subprocess.Popen = recorder
        heal.time.sleep = lambda _seconds: None
        heal.log = lambda _msg: None
        heal._core = lambda: None
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                status, info = heal._continue_once(area_dir, str(folder), fh)
        finally:
            (heal.subprocess.Popen, heal.time.sleep, heal.log,
             heal._core) = old_popen, old_sleep, old_log, old_core
        check(status == "ok", "без ядра статус не «ok», а «%s» (%s)" % (status, info))
        check(len(recorder.commands) == 1, "без ядра команда не отправлена")
        if recorder.commands:
            cmd = recorder.commands[0]
            check("--continue" in cmd, "без ядра в команде нет --continue: %s" % cmd)
            check(any("Продолжай" in part for part in cmd),
                  "без ядра в команде нет промпта «Продолжай»: %s" % cmd)

    if failures:
        print("❌ приёмка «Продолжай»: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка «Продолжай»: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
