# -*- coding: utf-8 -*-
r"""Приёмка: раскатка везёт ВСЕ файлы, которые демоны грузят в рантайме.

Урок, который повторялся трижды за один день (23.11):

1. `minimax-autheal.py` не был в списке раскатки ⇒ правки лечащего жили
   только на машине, эталон отставал, раскатка откатывала бы свежие правки;
2. `mcode-panel.js` не был в списке ⇒ атомарная запись конфига не доехала;
3. `minimax-switch.py` не был в списке ⇒ правка «не мигать окнами» не доехала,
   и окна мигали дальше: `core.running_areas()` грузит этот файл, и сторож
   сессий зовёт его **каждые ~10 секунд**.

Каждый раз «забытый» файл означал одно и то же: репозиторий и машина
разъезжаются, и правка, проверенная в репозитории, на машине не работает.

⚠️ **Проверка была слепа к файлам не-Python** (живой случай 27.11): список
подгружаемых файлов искался регуляркой `\.py|\.ps1` — а `mcode-project-areas.json`
читается демонами по пути (`core.CONFIG = BIN / "mcode-project-areas.json"`) и в
проверку **не попадал**. Из-за этого конфиг областей не был в списке раскатки:
четыре области, заведённые на машине, в репозиторий не переносились, а правка
поля «Раздел КАРМАНА» (G732) не доезжала. Теперь регулярка знает и про `.json`,
а файлы без запасного пути обязаны быть в списке.

Поэтому проверка простая и строгая: найти в коде демонов все файлы, которые
подгружаются в рантайме (`BIN / "X.py"`, `import minigen_core`), и убедиться,
что каждый из них есть в списке раскатки — либо что у него есть **запасной путь**
в самом коде (тогда файл необязателен, и это проверяется отдельно).

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

#: Файлы, которые демон читает, но **без них работает** (в коде есть запасной
#: путь). Такой файл в списке раскатки необязателен — но запасной путь обязан
#: **остаться в коде**: это проверяется по тексту источника.
OPTIONAL_FILES = {
    "minigen-net.json": ("minigen_core.py", "Нет файла ⇒ дефолты"),
    "happ-servers.json": ("minimax-netwatch.py", "читается, если есть"),
}

#: Расширения файлов, которые демоны подгружают из `bin`.
RUNTIME_EXT = ("py", "ps1", "json")


def rollout_files() -> set[str]:
    """Список файлов, которые раскатка везёт на боевое место."""
    text = (HERE / "minigen_rollout.py").read_text(encoding="utf-8-sig")
    # ⚠️ Комментарии убираем **до** разбора: в списке раскатки есть пояснения
    # со скобками («(UTC+3)», «(D-190, промт #100)»), а разбор по первому `)`
    # обрезал бы список — и файлы из его конца выпадали бы из проверки молча
    # (живой случай 26.11: `minigen_time.py` в проверку не попадал).
    code = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))
    match = re.search(r"^FILES\s*=\s*\((.*?)\)", code, re.S | re.M)
    if not match:
        return set()
    exts = "|".join(RUNTIME_EXT)
    return set(re.findall(r'"([^"]+\.(?:%s))"' % exts, match.group(1)))


def runtime_files() -> set[str]:
    """Файлы, которые демоны подгружают в рантайме (из `C:\\Users\\valstan\\bin`)."""
    found: set[str] = set()
    for name in DAEMON_SOURCES:
        path = HERE / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig")
        # `BIN / "minimax-switch.py"`, `core.BIN / "..."`, `ORPHANS = BIN / "..."`,
        # а также PowerShell-скрипты, которые демоны зовут (`BIN / "mcode-....ps1"`).
        # ⚠️ И `.json` тоже: `core.CONFIG = BIN / "mcode-project-areas.json"` —
        # конфиг областей читается демонами, и без него у них **ноль областей**
        # (живой случай 27.11: файл не был в списке раскатки ⇒ правки не доехали).
        exts = "|".join(RUNTIME_EXT)
        found |= set(re.findall(r'BIN\s*/\s*"([^"]+\.(?:%s))"' % exts, text))
        # ⚠️ Родные модули рядом: `import minigen_core as core`,
        # `import minigen_thread as thread`, `import minigen_time as mtime`.
        # Раньше проверка знала только про `minigen_core` — и новый модуль
        # (промт #172: паспорт нитки) в список раскатки не попал бы, а демон
        # на машине упал бы при старте. Теперь ловим **любой** такой импорт.
        found |= set(re.findall(
            r"(?m)^\s*import\s+(minigen_[a-z_]+)\b", text))
    # Модули Python едут как `.py`, скрипты PowerShell и json — как есть.
    suffixes = tuple("." + ext for ext in RUNTIME_EXT)
    return {name if name.endswith(suffixes) else name + ".py"
            for name in found}


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

    missing = sorted(runtime - files - set(OPTIONAL_FILES))
    check(not missing,
          "в списке раскатки нет файлов, которые демоны грузят в рантайме: %s "
          "(правка в репозитории не доедет до машины)" % missing)

    for name in sorted(files):
        check((HERE / name).is_file(),
              "файл из списка раскатки не существует в репозитории: %s" % name)

    print("   необязательные (есть запасной путь в коде): %s"
          % ", ".join(sorted(OPTIONAL_FILES)))
    for name, (source, marker) in sorted(OPTIONAL_FILES.items()):
        check(name in runtime,
              "необязательный файл %s демоны не читают — список устарел" % name)
        check(source in DAEMON_SOURCES,
              "необязательный файл %s: источник %s не в списке демонов"
              % (name, source))
        path = HERE / source
        text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        check(marker in text,
              "в %s нет запасного пути для %s (маркер «%s») — файл стал обязательным"
              % (source, name, marker))

    if failures:
        print("❌ приёмка списка раскатки: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка списка раскатки: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
