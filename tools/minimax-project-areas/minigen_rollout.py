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
REPO = Path(r"D:\valstan\REPO\biblio")
REL = "tools/minimax-project-areas"
FILES = ("minigen_core.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
         "minimax-autheal.py", "mcode-panel-supervisor.js", "mcode-panel.js")
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
# ⚠️ Файл панели надо ещё ПЕРЕЗАПУСТИТЬ: процесс панели держит старый код в
# памяти, а супервизор поднимает панель заново только после её смерти. Без
# перезапуска записанный файл не применится — панель продолжит писать конфиг
# неатомарно (см. шаг «перезапуск панели» ниже). Если владелец остановил
# панель намеренно (состояние «stopped»), её не поднимаем.



def master_file(name: str) -> bytes:
    r = subprocess.run(["git", "-C", str(REPO), "show", "origin/master:" + REL + "/" + name],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit("не прочитал из master: " + name)
    return r.stdout


def panel_alive(timeout: int = 5) -> bool:
    """Отвечает ли панель «МиниГена» на 8099."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8099/", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def panel_state(timeout: int = 5) -> str:
    """Состояние панели по странице управления супервизора (8098)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8098/status", timeout=timeout) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return str(data.get("state") or "")
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    print("=== раскатка «МиниГена» из репозитория (biblio master) на боевое место ===")
    print("--- файлы ---")
    for name in FILES:
        data = master_file(name)
        (BIN / name).write_bytes(data)
        print("   записан %-26s %d байт" % (name, len(data)))

    print("--- перезапуск демонов (поднимет супервизор, дублей не будет) ---")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*minimax-netwatch.py*' -or "
         "$_.CommandLine -like '*minimax-sessionwatch.py*' -or "
         "$_.CommandLine -like '*minimax-autheal.py*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        capture_output=True, text=True, timeout=60)
    time.sleep(12)

    print("--- перезапуск панели (процесс держит старый код в памяти) ---")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
         "Where-Object { $_.CommandLine -like '*mcode-panel.js*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        capture_output=True, text=True, timeout=60)
    time.sleep(8)
    if panel_alive():
        print("   панель отвечает на 8099 — новый код уже в работе")
    else:
        state = panel_state()
        print("   панель не отвечает (состояние супервизора: %s)" % (state or "неизвестно"))
        if state == "stopped":
            print("   владелец остановил панель намеренно — не поднимаем её, "
                  "правку панели он получит при следующем запуске")
        else:
            print("   ⚠️ панель не поднялась — проверьте супервизор (8098)")

    print("--- проверка ---")
    ok = True
    for name in ("minigen_core.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
                 "minimax-autheal.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", str(BIN / name)],
                           capture_output=True, text=True)
        good = r.returncode == 0
        ok = ok and good
        print("   компиляция %-26s %s" % (name, "ок" if good else "ОШИБКА " + r.stderr[-200:]))
    r = subprocess.run(["node", "--check", str(BIN / "mcode-panel-supervisor.js")],
                       capture_output=True, text=True)
    ok = ok and r.returncode == 0
    print("   node --check супервизора      %s" % ("ок" if r.returncode == 0 else "ОШИБКА"))
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
