"""Обновление программы «МиниГена» из окна (заказ владельца 24.11).

Вопрос владельца: «как мне запустить процесс обновления из графического
интерфейса программы? Сделай процесс обновления наглядным».

**Как обновление устроено на самом деле** (раньше это был ручной шаг владельца):

```
репозиторий → git pull → сборка окна (npm run build) →
раскатка (копирование файлов в своё место + перезапуск демонов) →
перезапуск окна
```

Здесь — тот же путь, но запускаемый из окна и показанный по шагам: видно, что
происходит, и понятно, обновилась программа или нет.

Правила:

- **одна реализация раскатки**: шаг «поставить программу» зовёт тот же
  скрипт, что и вручную (`tools/minimax-project-areas/minigen_rollout.py`),
  а не второй, «упрощённый»;
- **чужая работа не затирается**: если в исходниках есть незакоммиченные
  правки — обновление останавливается и говорит об этом;
- **окно перезапускается отдельно**: раскатка меняет файлы на диске, а
  запущенное окно продолжает работать на старом коде. Поэтому программа
  честно показывает «на диске новее, чем в окне» и предлагает перезапуск
  (метка сборки вшивается в окно при сборке — см. `electron.vite.config.ts`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
LOG_FILE = LOCAL / "update.log"
ROLLOUT_REL = ("tools", "minimax-project-areas", "minigen_rollout.py")
GUI_REL = ("tools", "minigen-gui")
MAX_LOG = 400
CACHE_SECONDS = 5

_lock = threading.Lock()
_steps: list[dict[str, Any]] = []
_running = False
_last: dict[str, Any] = {}
_cache: dict[str, Any] = {"at": 0.0, "value": {}}


# ──────────────────────────────────────────────────────────────── журнал

def _log(step: str, text: str, status: str = "info") -> None:
    """Запись в журнал обновления (и в окно, и на диск)."""
    record = {"ts": int(time.time() * 1000), "step": step,
              "status": status, "text": text[:400]}
    with _lock:
        _steps.append(record)
        del _steps[:-MAX_LOG]
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────── git

def _git(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str, str]:
    """Git без исключений: (код, stdout, stderr)."""
    try:
        proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return -1, "", "git не ответил за %d с" % timeout
    except OSError as exc:
        return -1, "", str(exc)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _stream(args: list[str], cwd: Path, step: str) -> int:
    """Запустить команду и писать её вывод в журнал построчно."""
    try:
        proc = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError) as exc:
        _log(step, "не запустилось: %s" % exc, "error")
        return -1
    if proc.stdout is not None:
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                _log(step, text)
    return proc.wait()


def _npm(args: list[str]) -> list[str]:
    """Команда npm: на Windows npm — это .cmd, его надо звать через cmd."""
    npm = shutil.which("npm") or "npm"
    if npm.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", npm] + args
    return [npm] + args


# ──────────────────────────────────────────────────────────── исходники

def repo_dir() -> Path:
    """Репозиторий, из которого ставится программа.

    Порядок:
    1. `settings.json → codeRepo` — его пишет установщик (у владельца это
       рабочее дерево с исходниками и `node_modules`);
    2. клон копии (у него `upstream` на репозиторий программы);
    3. репозиторий, в котором лежит сам мост.
    """
    try:
        data = json.loads((LOCAL / "settings.json").read_text(encoding="utf-8"))
        configured = str(data.get("codeRepo") or "").strip()
        if configured and (Path(configured) / ".git").exists():
            return Path(configured)
    except (OSError, ValueError):
        pass
    try:
        import minigen_clone
        clone = minigen_clone.CLONE_DIR
        if (clone / ".git").is_dir():
            return clone
    except Exception:  # noqa: BLE001
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return Path(".")


def _remote(repo: Path) -> str:
    """Какой remote смотрит на репозиторий программы."""
    code, out, _ = _git(["remote"], repo)
    if code != 0:
        return ""
    remotes = [line.strip() for line in out.splitlines() if line.strip()]
    for name in ("upstream", "origin"):
        if name in remotes:
            return name
    return remotes[0] if remotes else ""


def installed() -> dict[str, Any]:
    """Установленная копия: коммит, время установки, откуда запущена."""
    try:
        import minigen_state as state
        return state.jsonable(state.program_version())
    except Exception:  # noqa: BLE001
        return {"commit": "", "installed_at": 0, "installed": False}


def available() -> dict[str, Any]:
    """Что лежит в репозитории (по последнему fetch)."""
    repo = repo_dir()
    remote = _remote(repo)
    commit = ""
    behind = 0
    if remote:
        code, out, _ = _git(["rev-parse", "--short", "%s/master" % remote], repo)
        if code == 0:
            commit = out
            code, out, _ = _git(["rev-list", "--count", "HEAD..%s/master" % remote], repo)
            if code == 0 and out.isdigit():
                behind = int(out)
    return {"commit": commit, "behind": behind, "repo": str(repo),
            "remote": remote}


def _cached_available() -> dict[str, Any]:
    """То же, но не чаще раза в несколько секунд (окно опрашивает статус)."""
    now = time.time()
    with _lock:
        if now - float(_cache["at"]) < CACHE_SECONDS:
            return dict(_cache["value"])
    value = available()
    with _lock:
        _cache["at"] = now
        _cache["value"] = value
    return value


# ──────────────────────────────────────────────────────────────── статусы

def status() -> dict[str, Any]:
    """Состояние обновления — для окна (опрашивается раз в секунду)."""
    with _lock:
        steps = list(_steps)
        running = _running
        last = dict(_last)
    return {"running": running, "steps": steps, "installed": installed(),
            "available": _cached_available(), "last": last,
            "repo": str(repo_dir())}


def check() -> dict[str, Any]:
    """Проверить, есть ли новая версия (с походом на сервер)."""
    repo = repo_dir()
    if not (repo / ".git").exists():
        return {"ok": False, "why": "исходники программы не найдены: %s" % repo}
    remote = _remote(repo)
    if not remote:
        return {"ok": False, "why": "в исходниках нет удалённого репозитория"}
    code, out, err = _git(["fetch", "--quiet", remote, "master"], repo, timeout=300)
    if code != 0:
        return {"ok": False, "why": "сервер не отдал изменения: %s" % (err or out)}
    with _lock:
        _cache["at"] = 0.0
    info = available()
    current = installed()
    info.update({"ok": True, "installed": current,
                 "up_to_date": bool(current.get("commit")) and current.get("commit") == info.get("commit")})
    if info["up_to_date"]:
        info["why"] = "программа актуальна (%s)" % info.get("commit")
    else:
        info["why"] = "доступна версия %s (сейчас стоит %s)" % (
            info.get("commit") or "—", current.get("commit") or "—")
    return info


# ──────────────────────────────────────────────────────────────── запуск

def start() -> dict[str, Any]:
    """Запустить обновление (в фоне). Окно следит за шагами."""
    global _running
    with _lock:
        if _running:
            return {"ok": False, "why": "обновление уже идёт"}
        _running = True
        _steps.clear()
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"ok": True, "why": "обновление пошло — смотри шаги"}


def _fail(step: str, why: str) -> None:
    """Обновление остановилось — и сказало почему (это видно в окне)."""
    global _last
    _log(step, why, "error")
    with _lock:
        _last = {"ok": False, "why": why, "at": int(time.time() * 1000)}


def _run() -> None:
    """Обновление целиком: исходники → сборка → раскатка."""
    global _running, _last
    repo = repo_dir()
    try:
        # 1) исходники
        _log("repo", "исходники: %s" % repo)
        code, out, err = _git(["status", "--porcelain"], repo)
        if code != 0:
            _fail("repo", "исходники не читаются: %s" % (err or out))
            return
        if out.strip():
            _fail("repo", "в исходниках есть незакоммиченные правки — обновление "
                          "чужую работу не трогает; разберись с ними и повтори")
            return
        # ⚠️ Ветка (живой случай 24.11): обновление собирает окно и ставит его
        # на место — а сборка метку берёт из того, что в дереве. Если в дереве
        # была **ветка разработки**, на место попадала сборка из неё, и окно
        # потом вечно спорило с «версией на месте». Поэтому обновление идёт
        # только из master — и говорит об этом прямо.
        code, out, err = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        branch = out.strip() if code == 0 else ""
        if branch and branch not in ("master", "main"):
            _fail("repo", "исходники на ветке «%s» — обновление собирает окно из "
                          "master. Переключитесь на master (git checkout master) "
                          "и повторите" % branch)
            return
        gui = repo.joinpath(*GUI_REL)
        if not (gui / "node_modules").is_dir():
            _fail("repo", "в исходниках нет node_modules — поставь зависимости "
                          "(npm install в tools/minigen-gui) и повтори")
            return
        remote = _remote(repo)
        if not remote:
            _fail("repo", "в исходниках нет удалённого репозитория")
            return

        # 2) забрать новую версию
        _log("pull", "забираю изменения из %s" % remote)
        code, out, err = _git(["fetch", "--quiet", remote, "master"], repo, timeout=600)
        if code != 0:
            _fail("pull", "сервер не отдал изменения: %s" % (err or out))
            return
        before = _git(["rev-parse", "--short", "HEAD"], repo)[1]
        code, out, err = _git(["pull", "--ff-only", remote, "master"], repo, timeout=600)
        if code != 0:
            _fail("pull", "обновить исходники не вышло: %s" % (err or out))
            return
        after = _git(["rev-parse", "--short", "HEAD"], repo)[1]
        _log("pull", "исходники: %s → %s" % (before, after), "ok")

        # 3) сборка окна
        _log("build", "собираю окно (npm run build) — это минута-две")
        code = _stream(_npm(["run", "build"]), gui, "build")
        if code != 0:
            _fail("build", "сборка не прошла — смотри журнал")
            return
        _log("build", "окно собрано", "ok")

        # 4) раскатка (тот же скрипт, что и вручную)
        rollout = repo.joinpath(*ROLLOUT_REL)
        if not rollout.is_file():
            _fail("rollout", "нет скрипта раскатки: %s" % rollout)
            return
        _log("rollout", "ставлю программу на место и перезапускаю демонов")
        code = _stream([sys.executable, str(rollout)], repo, "rollout")
        if code != 0:
            _fail("rollout", "раскатка не прошла — смотри журнал")
            return
        _log("rollout", "программа поставлена", "ok")

        # 5) готово
        version = installed()
        with _lock:
            _cache["at"] = 0.0
        _log("done", "установлена версия %s — перезапусти окно, чтобы заработало"
             % version.get("commit"), "ok")
        with _lock:
            _last = {"ok": True, "installed": version, "at": int(time.time() * 1000)}
    except Exception as exc:  # noqa: BLE001
        _fail("done", "обновление прервалось: %s" % exc)
    finally:
        with _lock:
            _running = False
