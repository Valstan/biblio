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
 2. Останавливает **устаревших менеджеров фонового режима** — копии из дерева
    репозитория (программа живёт в своём месте, PR #79). Оставшийся такой
    менеджер — второй надзиратель демонов: демоны поднимаются в один и тот
    же момент (живой случай 23.11).
 3. Ставит программу в своё место и поднимает фоновый режим **оттуда**.
 4. Останавливает демонов — поднимает их программа (дублей не будет).
 5. Проверяет: компиляция, живые демоны, вердикты сессий, состояние сети.
"""
import json
import re
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
         "minimax-autheal.py", "minimax-switch.py", "minimax-orphans.py",
         "mcode-project-start.ps1", "mcode-project-stop.ps1",
         "mcode-project-title.ps1", "make-mcode-project-shortcuts.ps1",
         # 24.11: клиент канала промтов раздела — та же история, что и у соседей:
         # утилита проекта лежит рядом с остальными, чтобы её можно было звать
         # отовсюду, а не только из дерева репозитория.
         "karman_prompts.py",
         # 26.11 (D-190, промт #100): у программы «МиниГен» свой раздел промтов
         # (`MiniGen`) и свой токен (`PROMPTS_TOKEN_MINIGEN`) ⇒ свой клиент.
         # Лежит рядом с клиентом дома утилит, чтобы его тоже можно было звать
         # отовсюду.
         "minigen_karman_prompts.py",
         # 24.11: время программы — везде Москва, Россия (UTC+3). Модуль нужен
         # и демонам, и мосту (он грузит ядро из bin).
         "minigen_time.py",
         # 26.11 (промт #172): паспорт нитки — рядом с ядром: сторож сессий
         # берёт его при переносе нитки (что делалось, что осталось, ветка,
         # worktree, PR, критерии приёмки). Без этого файла на машине перенос
         # нитки пошёл бы по-старому (без паспорта).
         "minigen_passport.py")
# ⚠️ 23.11: `minimax-autheal.py` в списке НЕ БЫЛ — раскатка выкладывала
# сторожей, но не лечащего. Отсюда и расхождение «репозиторий ↔ машина»:
# правки лечащего жили только на машине, а эталон (master) отставал, и
# раскатка откатывала бы свежие правки. Лечащий — самый опасный демон
# (перезапускает десктопы), значит его-то и не выкладывали.
#
# ⚠️ 23.11 (четвёртый раз, ночь): `minimax-switch.py` в списке НЕ БЫЛ — а его
# грузит ядро (`core.running_areas()`) и лечащий. Правка «не мигать окнами»
# (CREATE_NO_WINDOW) жила в репозитории, но на машину не доехала ⇒ окна
# мигали по-прежнему: сторож сессий зовёт эту проверку каждые ~10 с.
# `minimax-orphans.py` — тоже в списке теперь (детектор оборванных
# субагентов; раньше ехал только случайно).
#
# ⚠️ Правило на будущее: список файлов раскатки — это **все** файлы, которые
# демоны грузят в рантайме. Приёмы `test_rollout_covers_runtime_files.py`
# проверяет это автоматически (иначе класс «забыли файл» повторяется).
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


# ─────────────────────────────────────────────── установка программы

PROGRAM_SRC = REPO / "tools" / "minigen-gui"
PROGRAM_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "МиниГен"
# Локальный API программы: на нём и надзиратель демонов, и статус.
API_PORT = 8097

LAUNCHER_CMD = """@echo off
rem MiniGen launcher (installed copy, ASCII only).
setlocal
cd /d "%~dp0"
if not exist "%~dp0electron\\dist\\electron.exe" (
  echo Electron runtime not found - re-run the rollout.
  pause
  exit /b 1
)
start "" "%~dp0electron\\dist\\electron.exe" "%~dp0app"
"""

AUTOSTART_CMD = """@echo off
rem MiniGen background mode: the daemons (healer, network watcher, session
rem watcher) stay alive without a window. ASCII only.
setlocal
cd /d "%~dp0"
set "PYTHONW=%LOCALAPPDATA%\\Programs\\Python\\Python312\\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw.exe"
start "" "%PYTHONW%" "%~dp0app\\bridge\\minigen_bridge.py" --daemon
"""


def _copy_tree(src: Path, dst: Path, skip=()) -> int:
    """Скопировать дерево, **не падая** на занятых файлах.

    ⚠️ Живой случай (23.11, 00:00): открытое окно «МиниГена» держит файлы
    Electron-рантайма (`WinError 32`), и раскатка на этом падала целиком —
    хотя демоны к тому моменту уже были обновлены, а не обновлялся только
    рантайм (он меняется редко). Теперь занятые файлы пропускаем и говорим
    о них: программа подхватит их при следующем запуске.
    """
    import fnmatch
    import shutil

    if not src.is_dir():
        return 0
    copied = 0
    locked: list[str] = []
    for path in src.rglob("*"):
        if any(fnmatch.fnmatch(path.name, pat) for pat in skip):
            continue
        target = dst / path.relative_to(src)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
            copied += 1
        except OSError:
            locked.append(path.name)
    if locked:
        print("   ⚠️ %d файлов заняты (открыто окно «МиниГена»?) — пропущены: %s"
              % (len(locked), ", ".join(locked[:4]) + ("…" if len(locked) > 4 else "")))
    return copied


def _clean_stale_bundles(dst: Path) -> int:
    """Убрать старые бандли окна: имя сборки содержит хеш, и каждая раскатка
    оставляла в установленной папке ещё один мёртвый файл.

    Занятый файл (открытое окно программы) не удаляем — переживёт до следующей
    раскатки.
    """
    assets = dst / "out" / "renderer" / "assets"
    if not assets.is_dir():
        return 0
    keep = set()
    try:
        page = (dst / "out" / "renderer" / "index.html").read_text(encoding="utf-8")
        keep = set(re.findall(r"assets/([A-Za-z0-9_.-]+)", page))
    except OSError:
        pass
    removed = 0
    try:
        items = list(assets.iterdir())
    except OSError:
        return 0
    for item in items:
        if item.name in keep:
            continue
        try:
            item.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def built_commit(program_src: Path | None = None) -> str:
    """Коммит, из которого **собран бандл окна** (файл метки рядом с бандлом).

    ⚠️ Зачем (живой случай 24.11): «версия на месте» должна описывать
    установленный бандл, а не коммит рабочего дерева. Иначе раскатка (например,
    после мержа одних документов, без пересборки) записывала в `version.json`
    коммит master, а окно работало на старой сборке — и владелец вечно видел
    «на диске новее, окно старое», а перезапуск **не помогал**: перезапускался
    тот же бандл. Теперь версия берётся из самого бандла ⇒ расхождение
    невозможно по построению.

    Пусто — сборка без метки (тогда раскатка возьмёт коммит рабочего дерева).
    """
    src = program_src or PROGRAM_SRC
    try:
        data = json.loads((src / "out" / "build-info.json").read_text(encoding="utf-8"))
        commit = str((data or {}).get("commit") or "").strip()
        if commit:
            return commit
    except (OSError, ValueError, TypeError):
        pass
    return ""


def install_program() -> bool:
    """Поставить программу в своё место и переписать ярлыки.

    ⚠️ Зачем отдельно от репозитория (23.11): если программа живёт в
    рабочем дереве, её версия зависит от того, какая ветка там checkout.
    Любой `git checkout` (у нас этим занимаются агенты) молча меняет
    программу, запущенную у владельца, — и ярлыки «указывают на ветку».
    Своё место: версия определена, репозиторий её не трогает.

    Исходники при этом остаются в репозитории: программа знает их
    (настройка `codeRepo`) и правит их через PR.

    Рядом с программой пишется `version.json` — какая версия установлена.
    """
    import json
    import shutil

    if not (PROGRAM_SRC / "out" / "main" / "index.js").is_file():
        print("   ⚠️ программа не собрана (нет out/main/index.js) — "
              "запустите `npm run build` в tools/minigen-gui и повторите")
        return False
    app = PROGRAM_ROOT / "app"
    electron = PROGRAM_ROOT / "electron"
    app.mkdir(parents=True, exist_ok=True)
    electron.mkdir(parents=True, exist_ok=True)

    count = _copy_tree(PROGRAM_SRC / "node_modules" / "electron" / "dist",
                       electron / "dist")
    for name in ("package.json", "path.txt"):
        src = PROGRAM_SRC / "node_modules" / "electron" / name
        if src.is_file():
            shutil.copy2(src, electron / name)
    _copy_tree(PROGRAM_SRC / "out", app / "out")
    _clean_stale_bundles(app)
    _copy_tree(PROGRAM_SRC / "bridge", app / "bridge", skip=("__pycache__", "*.pyc"))
    (app / "docs").mkdir(parents=True, exist_ok=True)
    src = PROGRAM_SRC / "docs" / "knowledge-pack.md"
    if src.is_file():
        shutil.copy2(src, app / "docs" / "knowledge-pack.md")
    shutil.copy2(PROGRAM_SRC / "package.json", app / "package.json")
    (PROGRAM_ROOT / "Запустить.cmd").write_bytes(LAUNCHER_CMD.encode("ascii"))
    (PROGRAM_ROOT / "Автозапуск.cmd").write_bytes(AUTOSTART_CMD.encode("ascii"))

    # Версия установленной копии: **коммит, из которого собран бандл**, + время
    # установки. Урок 23.11: программа без версии меняется вместе с веткой
    # репозитория; установленная копия версию имеет, и показывает её в окне.
    # ⚠️ 24.11: коммит берётся из метки сборки (`out/build-info.json`), а не из
    # `HEAD` рабочего дерева — иначе «версия на месте» могла уехать вперёд
    # относительно бандла, и окно вечно просило перезапуск, который не помогал.
    import time
    commit = built_commit()
    if not commit:
        # запасной путь: сборка без метки — коммит рабочего дерева
        try:
            res = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=15)
            commit = (res.stdout or "").strip()
        except Exception:  # noqa: BLE001
            commit = ""
    version = {"commit": commit, "installed_at": int(time.time()),
               "source": str(REPO)}
    # Родословная копии (заказ владельца 24.11): в версии программы видно,
    # какая это копия, из какого семейства и от какой версии форк.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mg_lineage", str(PROGRAM_SRC / "bridge" / "minigen_lineage.py"))
        lineage_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lineage_mod)
        version = lineage_mod.stamp(version)
    except Exception:  # noqa: BLE001
        pass        # родословная — дополнительный штрих, без неё раскатка идёт
    (PROGRAM_ROOT / "version.json").write_text(
        json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")

    # настройки: где исходники (для правок и PR)
    settings_path = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                     / "minigen-gui" / "settings.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    if not data.get("codeRepo"):
        data["codeRepo"] = str(REPO)
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print("   программа поставлена: %s (electron: %d файлов)" % (PROGRAM_ROOT, count))

    # ярлыки: рабочий стол + автозапуск (старый ярлык панели — в архив)
    ps = r'''
$ErrorActionPreference = "SilentlyContinue"
$root = "%s"
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$link = $shell.CreateShortcut((Join-Path $desktop "МиниГен.lnk"))
$link.TargetPath = (Join-Path $root "Запустить.cmd")
$link.Arguments = ""
$link.WorkingDirectory = $root
$link.Description = "МиниГен — единая программа с графическим интерфейсом"
$link.Save()
$old = Join-Path $desktop "МиниГен — панель (управление).lnk"
if (Test-Path $old) { Move-Item $old (Join-Path $root "МиниГен — панель (управление).lnk") -Force }
$startup = [Environment]::GetFolderPath("Startup")
$pyw = @(
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe")) |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if ($pyw) {
  $auto = $shell.CreateShortcut((Join-Path $startup "МиниГен (демоны).lnk"))
  $auto.TargetPath = $pyw
  $auto.Arguments = '"' + (Join-Path $root "app\bridge\minigen_bridge.py") + '" --daemon'
  $auto.WorkingDirectory = (Join-Path $root "app\bridge")
  $auto.Description = "МиниГен — фоновый режим: сторожа аккаунтов, сети и сессий"
  $auto.Save()
}
$oldStartup = Join-Path $startup "МиниГен.lnk"
if (Test-Path $oldStartup) {
  $oldLink = $shell.CreateShortcut($oldStartup)
  if ($oldLink.Arguments -like "*mcode-panel-supervisor*") { Remove-Item $oldStartup -Force }
}
''' % str(PROGRAM_ROOT)
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        print("   ⚠️ ярлыки: %s" % ((proc.stderr or "").strip()[:200]))
    else:
        print("   ярлыки: рабочий стол + автозапуск → %s" % PROGRAM_ROOT)
    return True


# ─────────────────────────────────────────────── остановка процессов

def terminate_pid(pid: int) -> bool:
    """Остановить процесс по pid — **даже если он повышенный**.

    ⚠️ Живой случай 24.11: программа, поднятая с повышенными правами, не
    видна ни WMI (командной строки нет — `Win32_Process.CommandLine` пуст),
    ни `taskkill` («доступ запрещён»). Право `PROCESS_TERMINATE` на такой
    процесс у нас есть ⇒ останавливаем через него.

    Проверено на этой машине: `OpenProcess(PROCESS_TERMINATE)` — открылся,
    `OpenProcess(PROCESS_VM_READ)` — «доступ запрещён» (оттого и пустая
    командная строка в WMI).
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def supervisor_pid() -> int:
    """Pid надзирателя — по файлу-подсказке блокировки (0 — неизвестно).

    ⚠️ Живой случай 24.11: надзиратель, поднятый с повышенными правами,
    WMI не показывает ⇒ раскатка его не видела и оставляла жить: старый
    код держал порт `:8097`, новый выходил («фоновый режим уже работает»),
    и программа жила на старом коде. А надзиратель известен — он записан
    в файле блокировки.
    """
    info = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "minigen-gui" / "supervisor.json")
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        return int((data or {}).get("pid") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def daemon_pids_visible() -> list[int]:
    """Pid'ы демонов, которых видит WMI (те, что с обычными правами)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*minimax-netwatch.py' -or "
          "$_.CommandLine -like '*minimax-sessionwatch.py' -or "
          "$_.CommandLine -like '*minimax-autheal.py' } | "
          "ForEach-Object { $_.ProcessId }")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pids: list[int] = []
    for token in (res.stdout or "").split():
        if token.strip().isdigit():
            pids.append(int(token.strip()))
    return pids


def daemon_children_of(supervisor_pid: int) -> list[int]:
    """Дети надзирателя — их видно даже без командных строк.

    ⚠️ Живой случай 24.11: демоны, поднятые **повышенным** надзирателем, не
    видны ни WMI (у них пустая командная строка), ни `taskkill`. Но **родитель**
    у них виден всегда ⇒ по нему их и находим. Иначе раскатка оставила бы их
    жить, а новый надзиратель поднял бы **вторые** копии (урок 22.11: два
    лечащих брали аккаунты дважды).
    """
    if not supervisor_pid:
        return []
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq %d } | "
          "ForEach-Object { $_.ProcessId }" % int(supervisor_pid))
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pids: list[int] = []
    for token in (res.stdout or "").split():
        if token.strip().isdigit():
            pids.append(int(token.strip()))
    return pids


def process_started_at(pid: int) -> str:
    """Когда процесс был создан (по данным WMI) — пусто, если его нет."""
    ps = ("Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
          "ForEach-Object { $_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') }" % int(pid))
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (res.stdout or "").strip().splitlines()[0] if (res.stdout or "").strip() else ""


def daemon_pids_from_journal() -> list[int]:
    """Pid'ы демонов, которых поднял надзиратель, — по журналу.

    ⚠️ Живой случай 24.11: демоны могут быть не видны ни WMI (нет командной
    строки), ни через API (надзиратель уже мёртв), ни по родителю (файл
    блокировки устарел). Но **надзиратель писал их pid'ы в журнал**
    («ДЕМОНЫ: лечащий не жил — поднял (pid …)») ⇒ это последний способ найти
    их и остановить, чтобы новый надзиратель не поднял вторые копии.

    ⚠️ Pid переиспользуется! Поэтому берём только те, что **живут и созданы
    не позже** записи в журнале: иначе можно убить чужой процесс, которому
    достался этот номер.
    """
    journal = Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web" / "journal.log"
    try:
        tail = journal.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    except OSError:
        return []
    pids: list[int] = []
    for line in tail:
        match = re.search(r"^\[(?P<ts>[\d\-]+ [\d:]+)\].*поднял\s*\(pid\s+(?P<pid>\d+)\)",
                          line)
        if not match:
            continue
        pid = int(match.group("pid"))
        if pid in pids:
            continue
        born = process_started_at(pid)
        if born and born <= match.group("ts"):
            pids.append(pid)
    return pids


def stop_daemons() -> int:
    """Остановить демонов — и тех, кого WMI не видит.

    ⚠️ Живой случай 24.11: демоны, поднятые с повышенными правами, WMI не
    показывает ⇒ обычная остановка их пропускала, а новый надзиратель (с
    правами пониже) считал, что демонов нет, и поднимал **вторые** копии
    (урок 22.11: два лечащих брали аккаунты дважды). Поэтому pid'ы берём у
    надзирателя (API — он видит демонов своими правами) и останавливаем их
    напрямую.
    """
    pids: list[int] = []
    data = api_status()
    for item in (data.get("daemons") or []):
        for pid in item.get("pids") or []:
            try:
                pids.append(int(pid))
            except (TypeError, ValueError):
                continue
    for pid in daemon_pids_visible():
        if pid not in pids:
            pids.append(pid)
    # API мог и не ответить (надзиратель уже мёртв) ⇒ берём демонов по
    # родителю: они — дети надзирателя, и родитель виден даже тогда, когда
    # командная строка процесса не видна (живой случай 24.11).
    for pid in daemon_children_of(supervisor_pid()):
        if pid not in pids:
            pids.append(pid)
    # и последний способ — журнал: надзиратель писал, кого поднял.
    for pid in daemon_pids_from_journal():
        if pid not in pids:
            pids.append(pid)
    stopped = 0
    for pid in pids:
        if terminate_pid(pid):
            stopped += 1
            print("   остановлен демон (pid %d)" % pid)
    return stopped


# ─────────────────────────────────────────────── менеджеры фонового режима

def manager_processes() -> list[tuple[int, str]]:
    """Живые фоновые менеджеры программы: (pid, командная строка).

    ⚠️ Кодировка вывода PowerShell — UTF-8 **явно**: в команде менеджера есть
    кириллица (путь установки «МиниГен»), и при декодировании по умолчанию
    (кодовая страница консоли) она превращалась в мусор ⇒ сравнение с
    `PROGRAM_ROOT` не сходилось, и установленный менеджер в журнале раскатки
    назывался «устаревшим». Раньше это было безобидно (старый код пропускал
    установленного), теперь — нет: всех надо останавливать.
    """
    ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
          "Where-Object { $_.CommandLine -like '*minigen_bridge.py*' } | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    out: list[tuple[int, str]] = []
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid_text, _, cmd = line.partition("|")
        try:
            pid = int(pid_text.strip())
        except ValueError:
            continue
        out.append((pid, cmd.strip()))
    return out


def stop_managers() -> int:
    """Остановить **все** фоновые менеджеры программы.

    ⚠️ ВНИМАНИЕ: функция гасит **живого** надзирателя (по файлу блокировки).
    Из приёмок её можно звать только с подменой `supervisor_pid` — иначе
    приёмка гасит фоновый режим на живой машине (живой случай 24.11: так
    программа осталась без API, а окно — без надзирателя).

    ⚠️ Зачем (живой случай 23.11): программа живёт в своём месте (PR #79), а
    копии из дерева репозитория живут до перезагрузки. Каждая такая копия —
    ещё один надзиратель демонов: два надзирателя поднимают демона в один и
    тот же момент (наблюдалось: демоны перезапустились дважды за две минуты),
    а на Windows перехват порта `:8097` ещё и делит вызовы между экземплярами.

    Останавливаем **и установленный** тоже: раскатка ставит новую программу,
    и старый её экземпляр не должен продолжать работать (он держит порт
    `:8097` — новый экземпляр без порта сразу выйдет, и надзирателем снова
    окажется старый код).

    ⚠️ Живой случай 24.11: раньше мы убивали менеджера и **сразу** поднимали
    нового. Порт ещё держал умирающий процесс ⇒ новый мост не мог поднять
    API и выходил («локальный API не поднялся — выхожу») ⇒ надзирателя не
    было, а раскатка рапортовала «готово» (демоны поднимал лечащий). Теперь
    ждём, пока менеджеры реально ушли.

    Страховка, если новый менеджер не поднимется: демоны умеют поднимать себя
    сами при изменении файла (`respawn_self`) — а файлы раскатка только что
    перезаписала.
    """
    stopped = 0
    for pid, cmd in manager_processes():
        kind = ("устаревший (из дерева репозитория)"
                if str(PROGRAM_ROOT).lower() not in cmd.lower()
                else "установленный")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print("   остановлен менеджер [%s] (pid %d): %s" % (kind, pid, cmd[:90]))
        stopped += 1
    # ⚠️ Живой случай 24.11: менеджер, поднятый с повышенными правами, в
    # списке WMI не виден (нет командной строки) — а он держит порт `:8097` и
    # надзирает за демонами. Новый менеджер из-за него выходит («фоновый
    # режим уже работает»), и программа живёт на старом коде. Его pid
    # известен: он записан в файле блокировки.
    owner = supervisor_pid()
    if owner and all(pid != owner for pid, _ in manager_processes()):
        if terminate_pid(owner):
            print("   остановлен надзиратель (pid %d) — в WMI его не было" % owner)
            stopped += 1
    # ждём ухода процессов: новый надзиратель без свободного порта выйдет
    deadline = time.time() + 30
    while time.time() < deadline:
        alive = manager_processes()
        if not alive:
            break
        time.sleep(1)
    else:
        print("   ⚠️ менеджеры не ушли за 30 с — новый может не поднять API")

    # ⚠️ Живой случай 24.11: **процесс ушёл — порт ещё отвечает.** Убитый
    # менеджер отдаёт API ещё секунду, и новый менеджер, увидев живой ответ,
    # честно уходит («фоновый режим уже работает — выхожу») ⇒ после раскатки
    # программа остаётся **без API**: ни окно, ни демоны его не поднимут.
    # Поэтому ждём не только ухода процессов, но и освобождения порта.
    deadline = time.time() + 30
    while time.time() < deadline:
        if not api_status():
            break
        time.sleep(1)
    else:
        print("   ⚠️ API ещё отвечает — порт не освободился, новый менеджер "
              "может выйти, не подняв API")
    return stopped


def api_status(port: int = API_PORT, token_file: Path | None = None) -> dict:
    """Ответ локального API программы (пусто — не отвечает).

    `token_file` — файл с токеном (по умолчанию токен программы). Если токена
    нет, запрос не делаем: API без токена всё равно не ответит.
    """
    path = token_file or (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                          / "minigen-gui" / "bridge-token.txt")
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not token:
        return {}
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/status?token=%s" % (port, token),
                timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def wait_api(timeout: int = 60, port: int = API_PORT) -> dict:
    """Дождаться, пока API программы начнёт отвечать."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = api_status(port=port)
        if data:
            return data
        time.sleep(2)
    return {}


def start_installed_manager() -> bool:
    """Поднять фоновый режим из установленного места (как при входе в Windows).

    ⚠️ Вывод перенаправляем в DEVNULL, а **не перехватываем**: `Автозапуск.cmd`
    запускает менеджера через `start`, и тот наследует дескрипторы. При
    `capture_output=True` труба остаётся открытой, пока жив менеджер (а он живёт
    всегда), ⇒ `subprocess.run` ждёт EOF **вечно** и раскатка зависает на
    последнем шаге. С DEVNULL дескриптор не наследуется по трубе — команда
    возвращается сразу.

    ⚠️ И проверка: «запустили» ≠ «надзиратель жив» (живой случай 24.11 —
    новый мост вышел, не сумев поднять API, а раскатка сказала «готово»).
    Поэтому ждём ответа API и только потом считаем, что надзиратель есть.
    """
    autostart = PROGRAM_ROOT / "Автозапуск.cmd"
    if not autostart.is_file():
        print("   ⚠️ нет %s — фоновый режим не поднят" % autostart)
        return False
    # ⚠️ Попыток несколько (живой случай 24.11): новый менеджер увидел ответ
    # умирающего старого и ушёл («фоновый режим уже работает — выхожу») ⇒ API
    # не поднялся вовсе. Повтор после освобождения порта — дешёвое лечение.
    data = {}
    for attempt in (1, 2, 3):
        subprocess.run(["cmd", "/c", str(autostart)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = wait_api(timeout=60)
        if data:
            break
        print("   API не поднялся (попытка %d) — ждём порт и пробуем снова" % attempt)
        time.sleep(10)
    if not data:
        print("   ⚠️ API программы не отвечает — надзиратель не поднялся")
        print("      (смотри журнал: «локальный API не поднялся — выхожу»)")
        print("      порт мог держать умирающий менеджер: закройте окно "
              "«МиниГен» и повторите раскатку")
        return False
    supervisor = data.get("supervisor") or {}
    print("   надзиратель: pid %s, демонов %s/%s" % (
        supervisor.get("pid"), data.get("alive_count"), data.get("total")))
    return True


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

    print("--- остановка демонов (поднимет программа «МиниГен», дублей не будет) ---")
    # ⚠️ Порядок важен: демонов останавливаем **до** менеджеров, пока
    # надзиратель ещё отвечает — он видит их своими правами (живой случай
    # 24.11: демоны с повышенными правами не видны ни WMI, ни окну).
    stopped_daemons = stop_daemons()
    if not stopped_daemons:
        print("   демонов не было (или их не видно)")
    time.sleep(6)

    print("--- фоновые менеджеры программы (старые и установленный) ---")
    # Раскатка ставит новую программу — старый её экземпляр (и копии из дерева
    # репозитория) должны уйти, иначе порт :8097 достанется старому коду.
    stopped = stop_managers()
    if not stopped:
        print("   менеджеров не было")

    print("--- проверка ---")
    ok = True
    for name in ("minigen_core.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
                 "minigen_passport.py", "minimax-autheal.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", str(BIN / name)],
                           capture_output=True, text=True)
        good = r.returncode == 0
        ok = ok and good
        print("   компиляция %-26s %s" % (name, "ок" if good else "ОШИБКА " + r.stderr[-200:]))

    print("--- установка программы (Ф6: раскатка ставит новую программу) ---")
    if "--no-program" not in sys.argv:
        try:
            ok = install_program() and ok
        except OSError as exc:
            # ⚠️ Чаще всего: открыто окно «МиниГена» — файлы Electron заняты
            # (WinError 32). Демоны к этому моменту УЖЕ обновлены, поэтому
            # ронять всю раскатку нельзя: говорим и идём дальше.
            print("   ⚠️ программа не обновлена: %s" % (exc,))
            print("      (обычно это открытое окно «МиниГен» — файлы заняты; "
                  "закройте окно и повторите раскатку)")
            ok = False
        print("--- фоновый режим из установленного места ---")
        if start_installed_manager():
            # демоны поднимаются с паузой (20 с) по очереди — сторож может
            # поднять себя сам, вторая копия не нужна. На всех троих уходит
            # до 60 с, поэтому ждём 80 с: иначе раскатка ругается «не все
            # демоны поднялись», хотя они поднимутся через минуту.
            time.sleep(80)
            alive = daemons_alive()
            print("   живых демонов: %d из 3%s" % (
                len(alive), (" (" + ", ".join(alive) + ")") if alive else ""))
            if len(alive) != 3:
                ok = False
                print("   ⚠️ не все демоны поднялись — смотри журнал")
        else:
            ok = False
    else:
        print("   пропущено (--no-program)")
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
