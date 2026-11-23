# -*- coding: utf-8 -*-
"""Приёмка: раскатка везёт ВСЕ файлы, которые демоны грузят в рантайме.

Урок, который повторялся трижды за один день (23.11):

1. `minimax-autheal.py` не был в списке раскатки ⇒ правки лечащего жили
   только на машине, эталон отставал, раскатка откатывала бы свежие правки;
2. `mcode-panel.js` не был в списке ⇒ атомарная запись конфига не доехала;
3. `minimax-switch.py` не был в списке ⇒ правка «не мигать окнами» не доехала,
   и окна мигали дальше: `core.running_areas()` грузит этот файл, и сторож
   сессий зовёт его **каждые ~10 секунд**.

Каждый раз «забытый» файл означал одно и то же: репозиторий и машина
разъезжаются, и правка, проверенная в репозитории, на машине не работает.

Поэтому проверка простая и строгая: найти в коде демонов все файлы, которые
подгружаются в рантайме (`BIN / "X.py"`, `import minigen_core`), и убедиться,
что каждый из них есть в списке раскатки.

Запуск: python3 tools/minimax-project-areas/test_rollout_covers_runtime_files.py
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Файлы демонов: в них ищем, что они подгружают в рантайме.
DAEMON_SOURCES = ("minigen_core.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
                  "minimax-autheal.py", "minimax-switch.py")


def rollout_files() -> set[str]:
    """Список файлов, которые раскатка везёт на боевое место."""
    text = (HERE / "minigen_rollout.py").read_text(encoding="utf-8-sig")
    match = re.search(r"^FILES\s*=\s*\((.*?)\)", text, re.S | re.M)
    if not match:
        return set()
    return set(re.findall(r'"([^"]+\.(?:py|ps1))"', match.group(1)))


def runtime_files() -> set[str]:
    """Файлы, которые демоны подгружают в рантайме (из `C:\\Users\\valstan\\bin`)."""
    found: set[str] = set()
    for name in DAEMON_SOURCES:
        path = HERE / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig")
        # `BIN / "minimax-switch.py"`, `core.BIN / "..."`, `ORPHANS = BIN / "..."`,
        # а также PowerShell-скрипты, которые демоны зовут (`BIN / "mcode-....ps1"`)
        found |= set(re.findall(r'BIN\s*/\s*"([^"]+\.(?:py|ps1))"', text))
        # `import minigen_core as core`
        if re.search(r"^\s*import\s+minigen_core\b", text, re.M):
            found.add("minigen_core.py")
    return found


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("=== раскатка везёт все файлы, которые демоны грузят в рантайме ===")
    files = rollout_files()
    runtime = runtime_files()
    print("   список раскатки: %s" % ", ".join(sorted(files)))
    print("   грузится в рантайме: %s" % ", ".join(sorted(runtime)))

    missing = sorted(runtime - files)
    check(not missing,
          "в списке раскатки нет файлов, которые демоны грузят в рантайме: %s "
          "(правка в репозитории не доедет до машины)" % missing)

    for name in sorted(files):
        check((HERE / name).is_file(),
              "файл из списка раскатки не существует в репозитории: %s" % name)

    if failures:
        print("❌ приёмка списка раскатки: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка списка раскатки: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
