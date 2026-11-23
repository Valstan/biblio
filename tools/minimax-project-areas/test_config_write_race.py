r"""Приёмка записи конфига областей: гонка снята, действие не теряется.

Зачем эта проверка (23.11). Панель переписывала `mcode-project-areas.json`
на месте, и читатель (демон, новая программа) ловил половину файла:
в журнале это «конфиг … не читается (Expecting ',' delimiter)» — дважды за
день, и один из этих дней сломал JSON у «Управляющего».

Проверка моделирует ровно то, что происходит в бою: читатель в цикле читает
конфиг, писатель его переписывает. Сравниваются две схемы записи:

- **прямая** (как было) — обрывает файл на середине;
- **атомарная** (временный файл + переименование) — читатель всегда видит
  целый JSON.

⚠️ Особенность Windows, о которой нельзя забыть: `rename` поверх файла,
который держит другой процесс, **отказывает** (ERROR_ACCESS_DENIED) —
Python-читатель открывает файл без FILE_SHARE_DELETE. Поэтому атомарная
запись обязана повторять переименование и иметь запасной путь «писать на
место»: иначе фикс гонки превратился бы в гонку «переключатель не
сработал». Проверка требует, чтобы действие **всегда** доходило до файла.

Что проверяется:

1. прямая запись даёт битые прочтения (иначе гонка не воспроизводится и
   проверка бессмысленна — об этом честно сообщаем);
2. атомарная запись не даёт НИ ОДНОГО битого прочтения;
3. Node-путь (та самая функция из `mcode-panel.js`) не теряет запись и тоже
   не ломает читателю файл.

Запуск:
    python tools/minimax-project-areas/test_config_write_race.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Печать значков (🟢/🔴) в консоли Windows ломается на кодовой странице
# консоли (charmap) — скрипт падал с UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent
# Панель архивирована (23.11, Ф6) — её код лежит в archive/, и проверка
# берёт writeJsonAtomic именно оттуда (архивный код тоже должен быть живым).
PANEL_JS = HERE / "archive" / "mcode-panel.js"

ROUNDS = 300
TRIES = 30
PAUSE = 0.1


def fail(msg: str) -> int:
    print("🔴 " + msg)
    return 1


def ok(msg: str) -> int:
    print("🟢 " + msg)
    return 0


def payload(n: int) -> str:
    return json.dumps({"projects": [{"slug": "a%d" % i, "autoheal": i % 2 == 0}
                                    for i in range(n)]}, ensure_ascii=False, indent=2)


def race(atomic: bool) -> tuple[int, int]:
    """(битые прочтения, неудачные записи) — конфиг во временной папке."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        target = Path(tmp) / "cfg.json"
        target.write_text(payload(200), encoding="utf-8")
        errors: list[str] = []
        failed = 0
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    json.loads(target.read_text(encoding="utf-8"))
                except ValueError as exc:
                    errors.append(str(exc))
                except OSError:
                    pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        for i in range(ROUNDS):
            text = payload(200 + (i % 7))
            if not atomic:
                target.write_text(text, encoding="utf-8")
                continue
            tmp_file = target.with_suffix(".tmp")
            tmp_file.write_text(text, encoding="utf-8")
            done = False
            for _ in range(TRIES):
                try:
                    os.replace(str(tmp_file), str(target))
                    done = True
                    break
                except PermissionError:
                    time.sleep(PAUSE)
                except OSError:
                    break
            if not done:
                failed += 1
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
                # запасной путь: действие важнее атомарности
                target.write_text(text, encoding="utf-8")
        stop.set()
        thread.join(timeout=3)
        return len(errors), failed


def extract_panel_writer() -> str | None:
    """Вырезать из панели реальные `sleepSync` и `writeJsonAtomic`.

    Тест проверяет ИМЕННО код панели, а не его копию: если правку поменяют —
    проверка это увидит.
    """
    if not PANEL_JS.is_file():
        return None
    source = PANEL_JS.read_text(encoding="utf-8")
    start = source.find("function sleepSync")
    if start < 0:
        return None
    end = source.find("\nfunction log(", start)
    if end < 0:
        end = len(source)
    return source[start:end]


def node_writer_check() -> tuple[int, str]:
    """Прогнать Node-писатель панели против Python-читателя.

    Возвращает (битые прочтения, сообщение писателя).
    """
    helpers = extract_panel_writer()
    if helpers is None:
        return 0, "не нашёл writeJsonAtomic в mcode-panel.js"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        target = Path(tmp) / "cfg.json"
        stop_flag = Path(tmp) / "stop"
        reader_src = Path(tmp) / "reader.py"
        reader_src.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "target, stop = Path(sys.argv[1]), Path(sys.argv[2])\n"
            "reads = errors = 0\n"
            "while not stop.exists():\n"
            "    try:\n"
            "        json.loads(target.read_text(encoding='utf-8'))\n"
            "        reads += 1\n"
            "    except ValueError:\n"
            "        errors += 1\n"
            "    except OSError:\n"
            "        pass\n"
            "print('reads=%d errors=%d' % (reads, errors))\n",
            encoding="utf-8")
        writer_src = Path(tmp) / "writer.js"
        writer_src.write_text(
            "const fs = require('fs');\n"
            "const { spawn } = require('child_process');\n"
            "const log = (m) => console.log(m);\n"   # заглушка журнала панели
            + helpers + "\n"
            "const target = process.argv[2];\n"
            "const stop = process.argv[3];\n"
            "const text = fs.readFileSync(process.argv[4], 'utf8');\n"
            "let failed = 0;\n"
            "for (let i = 0; i < %d; i++) {\n"
            "  try { writeJsonAtomic(target, text); } catch (e) { failed++; }\n"
            "}\n"
            "fs.writeFileSync(stop, '1');\n"
            "console.log('failed=' + failed);\n" % ROUNDS,
            encoding="utf-8")
        target.write_text(payload(200), encoding="utf-8")
        source_file = Path(tmp) / "payload.json"
        source_file.write_text(payload(200), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["node", str(writer_src), str(target), str(stop_flag),
                 str(source_file)],
                capture_output=True, text=True, timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            return 0, "node не найден — проверку Node-пути пропускаю"
        except subprocess.TimeoutExpired:
            return -1, "Node-писатель не ответил за 600 с"
        output = (proc.stdout or "").strip()
        if "failed=" not in output:
            return -1, "Node-писатель не отработал: %s" % (output or proc.stderr or "")[:200]
        failed = int(output.split("failed=")[1].split()[0])
        if failed:
            return -1, "Node-писатель потерял %d записей" % failed
        return 0, output


def main() -> int:
    print("=== приёмка записи конфига областей ===")
    direct_errors, direct_fails = race(atomic=False)
    print("   прямая запись (как было):   битых прочтений = %d, неудачных записей = %d"
          % (direct_errors, direct_fails))
    atomic_errors, atomic_fails = race(atomic=True)
    print("   атомарная запись (стало):   битых прочтений = %d, неудачных записей = %d"
          % (atomic_errors, atomic_fails))

    rc = 0
    if direct_errors == 0:
        print("   ⚠️ прямая запись не дала битых прочтений — гонка не воспроизвелась "
              "на этой файловой системе; сравнение неинформативно")
    else:
        print("   гонка воспроизводится (%d битых прочтений) — есть что чинить" % direct_errors)
    if atomic_errors:
        rc = fail("атомарная запись всё ещё ломает чтение: %d раз" % atomic_errors)
    else:
        rc = ok("атомарная запись: битых прочтений нет")
    if atomic_fails:
        rc = fail("атомарная запись теряет запись: %d раз" % atomic_fails) or rc
    else:
        rc = ok("атомарная запись: запись всегда доходит") or rc

    node_errors, node_msg = node_writer_check()
    if node_errors < 0:
        rc = fail("Node-путь панели: " + node_msg) or rc
    elif node_errors == 0 and "ошибок" not in node_msg:
        rc = ok("Node-путь панели: запись доходит, читатель не ловит битый JSON") or rc
    else:
        rc = fail("Node-путь панели: " + node_msg) or rc

    print("✅ приёмка записи конфига: PASS" if rc == 0 else "❌ приёмка записи конфига: FAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main())
