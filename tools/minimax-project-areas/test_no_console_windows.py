# -*- coding: utf-8 -*-
"""Тест: демоны не открывают окон консоли (23.11, жалоба владельца).

Симптом, о котором жаловался владелец: «окна терминала или повершела
постоянно появляются на экране и закрываются тут же», и «слишком много
запросов выполняется каждую секунду».

Механизм (проверен на этой машине, а не по памяти):

- демон запущен **отсоединённо** — у него НЕТ консоли (`GetConsoleWindow()`
  возвращает 0 при любых флагах: и `DETACHED_PROCESS`, и `CREATE_NO_WINDOW`);
- поэтому любой его дочерний **консольный** процесс (`powershell`, `cmd`)
  получает **свою** консоль — и на экране появляется окно, которое тут же
  закрывается. Тот же замер: `powershell` без `CREATE_NO_WINDOW` ⇒ окно есть
  (дескриптор ненулевой), с флагом ⇒ окна нет;
- `CREATE_NO_WINDOW` действует только на консольные приложения и
  игнорируется рядом с `DETACHED_PROCESS`/`CREATE_NEW_CONSOLE` — то есть
  флаг надо ставить на **дочерние** запуски.

Отдельно: проверка «слушает ли порт» жила в PowerShell
(`Get-NetTCPConnection`) и вызывалась десятки раз за проход сетевого
сторожа — это и было основным источником мигания (плюс лишняя нагрузка).
Теперь она чистая, на сокетах, и процессов не порождает вовсе.

Проверяем:
 1. **каждый** запуск процесса в боевых демонах имеет `creationflags`
    (окон быть не должно нигде);
 2. проверка порта не запускает ни одного процесса и отвечает верно.

Запуск: python3 tools/minimax-project-areas/test_no_console_windows.py
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import socket
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Боевые демоны и ядро. `archive/` — не боевой код, `test_*` — тесты.
DAEMON_FILES = ("minimax-netwatch.py", "minimax-switch.py", "minimax-autheal.py",
                "minimax-sessionwatch.py", "minigen_core.py")
SPAWN_METHODS = ("run", "Popen", "call", "check_call", "check_output")


def spawns_without_flag(path: pathlib.Path) -> list[tuple[int, str]]:
    """Запуски процессов без `creationflags` (каждый такой — риск окна)."""
    # «utf-8-sig»: часть боевых файлов лежит с BOM — парсеру он мешает.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in SPAWN_METHODS):
            continue
        base = func.value
        if not (isinstance(base, ast.Name) and base.id == "subprocess"):
            continue
        if any(keyword.arg == "creationflags" for keyword in node.keywords):
            continue
        bad.append((node.lineno, "subprocess.%s" % func.attr))
    return bad


def load_netwatch():
    spec = importlib.util.spec_from_file_location(
        "nw_windows_under_test", str(HERE / "minimax-netwatch.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("1) ни одного запуска процесса без creationflags")
    for name in DAEMON_FILES:
        path = HERE / name
        if not path.is_file():
            print("   пропущен (нет файла): %s" % name)
            continue
        bad = spawns_without_flag(path)
        check(not bad, "%s: запуск без creationflags — %s (окно может мигать)"
              % (name, bad))

    print("2) проверка порта не порождает процессов и отвечает верно")
    netwatch = load_netwatch()

    def boom(*_args, **_kwargs):
        raise AssertionError("проверка порта не должна запускать процессы")

    original_run = netwatch.subprocess.run
    netwatch.subprocess.run = boom
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            check(netwatch.port_listening(port) is True,
                  "слушающий порт должен считаться живым")
            server.close()
            check(netwatch.port_listening(port) is False,
                  "закрытый порт не должен считаться живым")
        finally:
            try:
                server.close()
            except OSError:
                pass
    finally:
        netwatch.subprocess.run = original_run

    if failures:
        print("❌ окна консоли: FAIL (%d)" % len(failures))
        return 1
    print("✅ окна консоли: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
