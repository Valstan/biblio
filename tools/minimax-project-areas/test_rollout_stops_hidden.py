# -*- coding: utf-8 -*-
"""Приёмка: раскатка останавливает и то, чего WMI не видит.

Живой случай 24.11: программа, поднятая с повышенными правами, не видна
ни WMI (у `Win32_Process.CommandLine` пусто — `OpenProcess` на такой процесс
даёт «доступ запрещён»), ни `taskkill` («доступ запрещён»). Право
`PROCESS_TERMINATE` при этом есть ⇒ остановить можно.

Почему это важно для раскатки:

- **менеджер** (надзиратель) держит порт `:8097` ⇒ новый менеджер выходит
  («фоновый режим уже работает»), и программа живёт на **старом** коде;
- **демоны** в повышенных правах не видны новому надзирателю ⇒ он считал бы,
  что демонов нет, и поднял **вторые** копии (урок 22.11: два лечащих
  брали аккаунты дважды).

Проверяем:

1. в раскатке есть помощник остановки по pid (`terminate_pid`) — и он
   останавливает процесс, который мы сами подняли;
2. раскатка знает pid надзирателя (файл блокировки) — того самого, которого
   WMI не показывает;
3. остановка демонов идёт **через надзирателя** (API), а не только через WMI.

Запуск: python3 tools/minimax-project-areas/test_rollout_stops_hidden.py
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent


def load_rollout():
    spec = importlib.util.spec_from_file_location(
        "mg_rollout_check", str(HERE / "minigen_rollout.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def source_checks(failures: list[str], rollout) -> None:
    print("1) раскатка умеет останавливать непрозрачное")
    source = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
    for marker in ("def terminate_pid", "PROCESS_TERMINATE", "def supervisor_pid",
                   "def stop_daemons"):
        if marker not in source:
            failures.append("в раскатке нет %s" % marker)
            print("   FAIL: в раскатке нет %s" % marker)
    if "stop_daemons()" not in source:
        failures.append("раскатка не зовёт stop_daemons()")
        print("   FAIL: раскатка не зовёт stop_daemons()")
    # демонов останавливаем, пока надзиратель ещё отвечает
    try:
        daemons_at = source.index("stopped_daemons = stop_daemons()")
        managers_at = source.index("stopped = stop_managers()")
    except ValueError:
        failures.append("раскатка не зовёт stop_daemons()/stop_managers()")
        print("   FAIL: раскатка не зовёт stop_daemons()/stop_managers()")
    else:
        if daemons_at > managers_at:
            failures.append("демонов надо останавливать до менеджеров — "
                            "иначе надзиратель не ответит и pid'ов не будет")
            print("   FAIL: демонов останавливаем после менеджеров")
    print("   раскатка: останавливает и непрозрачное — %s"
          % ("да" if not failures else "нет"))


def live_check(failures: list[str], rollout) -> None:
    print("2) живая остановка процесса по pid")
    if os.name != "nt":
        print("   пропущено (нужна Windows)")
        return
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(1)
    if not rollout.terminate_pid(proc.pid):
        failures.append("остановить свой процесс не вышло")
        print("   FAIL: остановить свой процесс не вышло")
    else:
        print("   ок: процесс остановлен (pid %d)" % proc.pid)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        failures.append("процесс после остановки жив")
        print("   FAIL: процесс после остановки жив")
        proc.kill()

    print("3) pid надзирателя — из файла блокировки")
    info = (pathlib.Path(os.environ.get("LOCALAPPDATA", str(pathlib.Path.home())))
            / "minigen-gui" / "supervisor.json")
    if not info.is_file():
        print("   пропущено (нет файла блокировки — надзирателя не было)")
        return
    pid = rollout.supervisor_pid()
    if pid <= 0:
        failures.append("pid надзирателя не прочитан")
        print("   FAIL: pid надзирателя не прочитан")
    else:
        print("   ок: надзиратель pid %d" % pid)

    print("4) демоны находятся по родителю (их видно даже без командных строк)")
    # родитель, у которого есть ребёнок
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time; "
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
         "time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(3)
    try:
        children = rollout.daemon_children_of(parent.pid)
        if not children:
            failures.append("дети процесса не нашлись — поиск по родителю не работает")
            print("   FAIL: дети процесса не нашлись — поиск по родителю не работает")
        else:
            print("   ок: дети найдены: %s" % children)
    finally:
        parent.kill()


def main() -> int:
    failures: list[str] = []
    rollout = load_rollout()
    source_checks(failures, rollout)
    live_check(failures, rollout)
    if failures:
        print("❌ приёмка остановки непрозрачного: FAIL (%d)" % len(failures))
        for item in failures:
            print("   - %s" % item)
        return 1
    print("✅ приёмка остановки непрозрачного: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
