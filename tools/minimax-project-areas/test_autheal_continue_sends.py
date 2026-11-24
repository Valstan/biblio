# -*- coding: utf-8 -*-
"""Приёмка: «Продолжай» доходит до отправки, даже без ядра.

Живой случай (24.11): лечащий брал аккаунт у КАРМАНА, а «Продолжай» **не
уходило** в область — лечение сорвалось на последнем шаге:

```
[2026-11-24 15:23:53] Управляющий: аккаунт получен (аккаунт 074@pochtaminimax.ru, …)
[2026-11-24 15:23:55] Управляющий: лечение сорвалось: name 'core' is not defined
```

Причина: ядро грузится **лениво** (`_core()`), а в двух местах имя `core` не
было определено вовсе ⇒ `NameError` ⇒ команда не собиралась.

Проверяем:

1. **`_continue_once` без ядра** — команда собирается и уходит: ядро here
   **мягкая зависимость** (лечащий намеренно живёт без ядра);
2. **`_continue_once` с ядром** — флаги модели попадают в команду;
3. **в исходнике лечащего** нет функции, где зовут `core.`, но не берут
   `core = _core()` — такая поломка не повторится молча.

Запуск: python3 tools/minimax-project-areas/test_autheal_continue_sends.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load_healer():
    spec = importlib.util.spec_from_file_location(
        "minimax_autheal_check", str(HERE / "minimax-autheal.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeProc:
    """Процесс, который «идёт» (как только что отправленный ход)."""

    pid = 4242

    def poll(self):
        return None


class FakeTime:
    """Время без ожидания (тест не должен спать три секунды)."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def time(self) -> float:
        return 0.0


def send_once(mod, core):
    """Одна попытка отправки «Продолжай» — с подменённым запуском процесса.

    Возвращает (результат, команда).
    """
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return FakeProc()

    class FakeSubprocess:
        DEVNULL = -3
        CREATE_NO_WINDOW = 0x08000000
        Popen = staticmethod(fake_popen)

    old_subprocess = mod.subprocess
    old_time = mod.time
    old_core = mod._core
    old_kids = mod.broken_subagents
    mod.subprocess = FakeSubprocess
    mod.time = FakeTime()
    mod._core = lambda: core
    mod.broken_subagents = lambda area_dir: []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            area_dir = pathlib.Path(tmp) / "p-test"
            area_dir.mkdir(parents=True, exist_ok=True)
            folder = pathlib.Path(tmp) / "project"
            folder.mkdir(parents=True, exist_ok=True)
            logfile = open(pathlib.Path(tmp) / "continue.log", "a", encoding="utf-8")
            try:
                status, info = mod._continue_once(area_dir, str(folder), logfile)
            finally:
                logfile.close()
    finally:
        mod.subprocess = old_subprocess
        mod.time = old_time
        mod._core = old_core
        mod.broken_subagents = old_kids
    return status, info, captured.get("cmd") or []


def core_usage_problems(source: str) -> list[str]:
    """Функции, где зовут `core.`, но не берут `core = _core()`."""
    problems: list[str] = []
    for block in re.split(r"(?m)^(?=def )", source):
        match = re.match(r"def\s+([A-Za-z_0-9]+)", block)
        if not match:
            continue
        if "core." in block and "core = _core()" not in block:
            problems.append(match.group(1))
    return problems


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("1) «Продолжай» уходит даже без ядра (ядро — мягкая зависимость)")
    mod = load_healer()
    try:
        status, info, cmd = send_once(mod, None)
    except Exception as exc:  # noqa: BLE001
        check(False, "без ядра отправка упала: %s: %s" % (type(exc).__name__, exc))
        cmd = []
        status = ""
        info = ""
    check(status == "ok", "без ядра отправка не удалась: %s (%s)" % (status, info))
    check("exec" in cmd, "команда не собрана: %s" % cmd)
    check("--continue" in cmd, "в команде нет --continue: %s" % cmd)
    check(any("Продолжай" in part for part in cmd),
          "в команде нет «Продолжай»: %s" % cmd)
    print("   команда: %s" % (" ".join(str(part) for part in cmd) or "—"))

    print("2) с ядром флаги модели попадают в команду")
    class FakeCore:
        @staticmethod
        def model_flags(area_dir):
            return ["--model", "minimax/MiniMax-M2"]

    status2, info2, cmd2 = send_once(mod, FakeCore())
    check(status2 == "ok", "с ядром отправка не удалась: %s (%s)" % (status2, info2))
    check("--model" in cmd2 and "minimax/MiniMax-M2" in cmd2,
          "флаги модели не попали в команду: %s" % cmd2)

    print("3) в исходнике нет обращения к неопределённому `core`")
    source = (HERE / "minimax-autheal.py").read_text(encoding="utf-8")
    problems = core_usage_problems(source)
    check(not problems,
          "функции зовут `core.`, но не берут `core = _core()`: %s" % problems)

    if failures:
        print("❌ приёмка отправки «Продолжай»: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка отправки «Продолжай»: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
