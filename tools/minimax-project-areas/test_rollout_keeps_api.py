# -*- coding: utf-8 -*-
"""Приёмка: раскатка не должна оставлять программу без API.

Живой случай (24.11): раскатка убила фоновой менеджер программы (он держит
API на `:8097`), и новый менеджер **увидел ответ умирающего старого** —
«фоновый режим уже работает — выхожу». Итог: раскатка отчиталась, а программа
осталась **без API**: ни окно, ни демоны его не поднимают, и сеть/сторож
сессий молча работают вслепую.

Проверяем:

1. **раскатка ждёт освобождения порта**, а не только ухода процессов;
2. **фоновой режим поднимается с несколькими попытками** (порт мог быть занят);
3. **если API всё-таки не поднялся — раскатка говорит об этом и возвращает
   неуспех** (честный отказ, а не «готово»).

Запуск: python3 tools/minimax-project-areas/test_rollout_keeps_api.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import types

HERE = pathlib.Path(__file__).resolve().parent


def load_rollout():
    spec = importlib.util.spec_from_file_location(
        "rollout_api_under_test", str(HERE / "minigen_rollout.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock:
    """Часы, которые идут, но не ждут: тест не должен спать по-настоящему."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        self.now += 1.0
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, min(float(seconds), 1.0))


class FakeSubprocess:
    DEVNULL = -3

    def __init__(self) -> None:
        self.calls: list = []

    def run(self, *args, **kwargs):
        self.calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    rollout = load_rollout()
    rollout.time = FakeClock()
    fake_proc = FakeSubprocess()
    rollout.subprocess = fake_proc

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "МиниГен"
        root.mkdir(parents=True)
        (root / "Автозапуск.cmd").write_text("@echo off\n", encoding="utf-8")
        rollout.PROGRAM_ROOT = root

        print("1) раскатка ждёт освобождения порта API")
        rollout.manager_processes = lambda: []
        answers = [{"supervisor": {"pid": 1}}, {"supervisor": {"pid": 1}}, {}]
        seen = []

        def fake_api_status(*_args, **_kwargs):
            seen.append(1)
            return answers.pop(0) if answers else {}

        rollout.api_status = fake_api_status
        # ⚠️ Живой случай 24.11: `stop_managers()` гасит **живого** надзирателя
        # (по файлу блокировки — его не видно в WMI). Приёмка зовёт её
        # по-настоящему ⇒ на живой машине она гасила фоновый режим программы
        # (и окно оставалось без API). Поэтому здесь надзиратель — «никто».
        rollout.supervisor_pid = lambda: 0
        rollout.stop_managers()
        check(len(seen) >= 3,
              "порт не ждали: проверок API %d (ждать надо, пока не освободится)"
              % len(seen))

        print("2) фоновой режим поднимается с несколькими попытками")
        waits = [{}, {"alive_count": 3, "total": 3, "supervisor": {"pid": 42}}]

        def fake_wait_api(*_args, **_kwargs):
            return waits.pop(0) if waits else {}

        rollout.wait_api = fake_wait_api
        fake_proc.calls = []
        ok = rollout.start_installed_manager()
        check(ok, "фоновой режим не поднялся, хотя вторая попытка дала API")
        check(len(fake_proc.calls) >= 2,
              "попытка подъёма была одна: %d" % len(fake_proc.calls))

        print("3) без API раскатка честно говорит об этом")
        rollout.wait_api = lambda *_a, **_k: {}
        ok = rollout.start_installed_manager()
        check(not ok, "раскатка сказала «поднялось», а API мёртв")

        print("4) в коде есть ожидание порта")
        src = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
        check("API ещё отвечает" in src,
              "в раскатке нет ожидания освобождения порта")
        check("попытка" in src, "в раскатке нет повторных попыток подъёма")

    if failures:
        print("❌ приёмка API при раскатке: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка API при раскатке: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
