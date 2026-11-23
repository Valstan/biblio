"""Раскатка «МиниГена» из репозитория на боевое место (PC79).

Зачем этот скрипт. Демоны живут в `C:\\Users\\valstan\\bin` (вне репозитория), а
писать туда агент не может: гейт разрешений требует подтверждения, а хост
спросить не может ⇒ раскатку делает владелец **одной командой**:

    python D:\\valstan\\REPO\\biblio\\tools\\minimax-project-areas\\minigen_rollout.py

(каноническое место — `biblio/tools/minimax-project-areas/`, D-048: утилиты
машины живут там; копия в `brain_matrica/.claude/scripts/` — для быстрого
запуска из репо Мозга).

Направление — **из репозитория**: эталон версии живёт в `biblio` (`master`),
и боевое место должно быть его копией. Обратное (боевое место → репозиторий)
приводит к тому, что репозиторий перестаёт быть эталоном, и к двойным демонам:
скрипт сам поднимает процессы, а супервизор поднимает их же после смерти
процессов — в области появляются две копии сторожа (а лечащий берёт аккаунты
у Кармана дважды). Урок 22.11: раскатка — копия файлов + перезапуск, который
делает супервизор.

Что делает:
 1. Копирует файлы сторожей из `biblio` `master` в `C:\\Users\\valstan\\bin`.
 2. Останавливает демонов — поднимает их **супервизор** (дублей не будет).
 3. Проверяет: компиляция, вердикты сессий, состояние сети.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

BIN = Path(r"C:\Users\valstan\bin")
# Репозиторий-эталон — ТОТ ЖЕ, где лежит этот скрипт.
# ⚠️ 23.11: раньше путь был зашит (`D:\valstan\REPO\biblio`), и скрипт,
# запущенный из другой копии (например, из worktree на старой ветке),
# раскатывал по СТАРОМУ списку файлов и без перезапуска панели: правки
# в master доезжали не полностью. Теперь эталон — всегда тот репозиторий,
# из которого запущен скрипт, а список файлов — из его origin/master.
REPO = Path(__file__).resolve().parents[2]
REL = "tools/minimax-project-areas"
# Панель и её супервизор в списке НЕ БЫЛИ — 23.11 они архивированы (Ф6):
# веб-панель :8099 и страница управления :8098 заменены программой
# «МиниГен» (tools/minigen-gui). Демонов теперь держит сама программа.
FILES = ("minigen_core.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
         "minimax-autheal.py")
# ⚠️ 23.11: `minimax-autheal.py` в списке НЕ БЫЛ — раскатка выкладывала
# сторожей, но не лечащего. Отсюда и расхождение «репозиторий ↔ машина»:
# правки лечащего жили только на машине, а эталон (master) отставал, и
# раскатка откатывала бы свежие правки. Лечащий — самый опасный демон
# (перезапускает десктопы), значит его-то и не выкладывали.
#
# Плюс проверка журнала на «сбой прохода»: демон может молча падать на каждом
# проходе (процесс жив ⇒ супервизор его не перезапускает ⇒ 9,5 часов никто
# не оживал сессий). Скрипт обязан это видеть и сказать, а не отчитаться
# «готово».
#
# ⚠️ 23.11 (второй раз): `mcode-panel.js` в списке НЕ БЫЛ — правку атомарной
# записи конфига областей раскатка на машину не везла ⇒ гонка жила и после
# мержа в master. Проверено: живая копия панели отличалась от master РОВНО
# этой правкой (1 вставка, 7 удалений), посторонних правок на машине нет.
#
# ⚠️ 23.11 (третий раз, Ф6): с архивированием панели раскатка больше не
# перезапускает её — перезапуск панели заменён перезапуском ДЕМОНОВ:
# их поднимает программа «МиниГен» (мост в режиме `--daemon`).



def master_file(name: str, ref: str = "origin/master") -> bytes:
    r = subprocess.run(["git", "-C", str(REPO), "show", ref + ":" + REL + "/" + name],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit("не прочитал из " + ref + ": " + name)
    return r.stdout


def daemons_alive() -> list[str]:
    """Какие демоны живы (без аргументов — сам демон, не разовый вызов)."""
    import re
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*minimax-netwatch.py' -or "
         "$_.CommandLine -like '*minimax-sessionwatch.py' -or "
         "$_.CommandLine -like '*minimax-autheal.py' } | "
         "ForEach-Object { $_.CommandLine }"],
        capture_output=True, text=True, timeout=60)
    alive = []
    for line in (out.stdout or "").splitlines():
        match = re.search(r"(minimax-(?:netwatch|sessionwatch|autheal)\.py)\s*$", line.strip())
        if match:
            alive.append(match.group(1))
    return alive


def main() -> int:
    # Источник кода: по умолчанию master. `--ref` — раскатать из другой
    # ветки (нужно, когда правка срочная, а слияние PR ещё не прошло):
    # код всё равно едет из репозитория, и PR остаётся открытым.
    ref = "origin/master"
    if "--ref" in sys.argv:
        index = sys.argv.index("--ref")
        if index + 1 < len(sys.argv):
            ref = sys.argv[index + 1]
    print("=== раскатка «МиниГена» из репозитория (biblio %s) на боевое место ===" % ref)
    print("--- файлы ---")
    for name in FILES:
        data = master_file(name, ref)
        (BIN / name).write_bytes(data)
        print("   записан %-26s %d байт" % (name, len(data)))

    print("--- перезапуск демонов (поднимет программа «МиниГен», дублей не будет) ---")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*minimax-netwatch.py' -or "
         "$_.CommandLine -like '*minimax-sessionwatch.py' -or "
         "$_.CommandLine -like '*minimax-autheal.py' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        capture_output=True, text=True, timeout=60)
    time.sleep(12)
    alive = daemons_alive()
    print("   живых демонов: %d из 3%s" % (
        len(alive), (" (" + ", ".join(alive) + ")") if alive else ""))

    print("--- проверка ---")
    ok = True
    for name in ("minigen_core.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
                 "minimax-autheal.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", str(BIN / name)],
                           capture_output=True, text=True)
        good = r.returncode == 0
        ok = ok and good
        print("   компиляция %-26s %s" % (name, "ок" if good else "ОШИБКА " + r.stderr[-200:]))
    r = subprocess.run([sys.executable, str(BIN / "minimax-netwatch.py"), "--status"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=180)
    for line in (r.stdout or "").splitlines()[:8]:
        print("   " + line)
    r = subprocess.run([sys.executable, str(BIN / "minimax-sessionwatch.py"), "--list"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    interesting = [ln for ln in (r.stdout or "").splitlines()
                   if ln.strip() and ("готова" not in ln and "проба" not in ln)]
    print("   незакрытые дела:")
    for line in interesting:
        print("     " + line)
    live = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*minimax-*' }).Count"],
        capture_output=True, text=True, timeout=60)
    print("   живых процессов МиниГена: %s" % (live.stdout or "?").strip())

    # ── проверка журнала: демон может молча падать на каждом проходе ──
    # (процесс жив ⇒ супервизор его не перезапускает ⇒ «всё живо» снаружи,
    #  а внутри журнал полон «сбой прохода»). 22.11 это стоило 9,5 часов.
    journal = Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web" / "journal.log"
    fails = []
    try:
        if journal.is_file():
            tail = journal.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
            fails = [ln for ln in tail if "сбой прохода" in ln]
    except OSError:
        pass
    if fails:
        ok = False
        print("   ⚠️ ЖУРНАЛ: сбой прохода (сторож падает) — %d строк, последние:" % len(fails))
        for line in fails[-3:]:
            print("     " + line.strip())
        print("   ⚠️ раскатка без этого НЕ ЗАВЕРШЕНА: демон молча падает")
    else:
        print("   журнал: «сбой прохода» нет — сторож проходит")

    print("=== готово ===" if ok else "=== ЕСТЬ ОШИБКИ ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
