"""Режимы копии «МиниГена» и первый запуск (промт #131, заказ владельца 25.11).

Заказ владельца (25.11, задание 3): «при первом запуске копия спрашивает
разрешение на регистрацию своей папки и предлагает три режима работы (свой
GitHub / общий репозиторий / только локально), а копии узнают друг о друге и
предлагают подходящие наработки».

Здесь три вещи:

- **режимы копии** — «свой репозиторий на GitHub», «папка в общем
  репозитории», «только локально, без сети». Режим выбирает владелец (в
  окне), и он меняет, что копия делает с правками программы и как обменивается
  наработками;
- **первый запуск** — вопрос о регистрации папки копии в репозитории
  программы: какой репозиторий, какая папка, что это даёт и что будет при
  отказе. Ответ сохраняется в `instance.json` и в настройках, и вопрос
  показывается **один раз**;
- **локальный репозиторий** — в режиме «только локально» правки программы
  идут в git-репозиторий на машине: история, откат, diff — без сети.

Честность — правило этого модуля:

- отказ от регистрации **не ломает программу**: копия просто работает в
  локальном режиме;
- в локальном режиме **обмена с другими копиями нет** (сети нет) — это
  говорится прямо, а не прячется за формулировкой «пока пусто»;
- **PR без GitHub не открывается** — об этом сообщается текстом, а не
  пустым результатом;
- наружу (в общий репозиторий, в пул генов) уходят только идентификатор
  копии и хеши коммитов — ни имён машин, ни путей (D-038).

Развилки, решённые здесь (владельцем решение не принималось — записано явно):

- **дефолт после согласия** — «папка в общем репозитории»: копия правит
  программу там же, где живёт программа, и PR идёт в общий репозиторий;
  «свой репозиторий» включается вручную;
- **отказ** = режим «только локально»: программа продолжает работать, правки
  идут в локальный git-репозиторий на машине;
- **кто даёт согласие** — тот, кто сидит за машиной: вопрос и его последствия
  показываются целиком, а в тексте сказано, что репозиторий принадлежит
  владельцу. Права владельца на репозиторий программой не переиспользуются.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import minigen_lineage as lineage

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
#: Локальный git-репозиторий правок программы (режим «только локально»).
LOCAL_REPO = LOCAL / "local-repo"
#: Папка для ручного обмена наработками (выгрузил — отнёс соседу).
EXPORT_DIR = LOCAL / "genepool-export"

MODE_OWN = "own"
MODE_SHARED = "shared"
MODE_LOCAL = "local"
MODES = (MODE_OWN, MODE_SHARED, MODE_LOCAL)

MODE_LABELS = {
    MODE_OWN: "Свой репозиторий на GitHub",
    MODE_SHARED: "Папка в общем репозитории",
    MODE_LOCAL: "Только локально, без сети",
}

MODE_HINTS = {
    MODE_OWN: "Копия заводит свой репозиторий на GitHub, ветки и PR — в общий "
              "репозиторий программы. Обмен наработками доступен.",
    MODE_SHARED: "Правки и PR идут прямо в общий репозиторий программы. "
                 "Обмен наработками доступен.",
    MODE_LOCAL: "Правки идут в локальный git-репозиторий на этой машине "
                "(история, откат, diff). PR не открывается, обмен с другими "
                "копиями недоступен.",
}

#: Репозиторий программы — общий (туда идут правки и PR).
SHARED_REPO = "Valstan/biblio"
#: Пул наработок (обмен генами между копиями).
POOL_REPO = "Valstan/minigen-genepool"

#: Папка, которая появится в общем репозитории при регистрации копии.
COPIES_FOLDER = "copies"

#: Что не копируем в локальный репозиторий (тяжёлое, служебное, временное).
SKIP_NAMES = (".git", "node_modules", "out", "dist", "release", "__pycache__",
              ".venv", ".pytest_cache", ".mypy_cache", "venv", "worktrees")


def _now() -> int:
    return int(time.time() * 1000)


def _run(args: list[str], cwd: Path | None = None, timeout: int = 600) -> dict[str, Any]:
    """Запустить команду и вернуть результат (без исключений)."""
    try:
        proc = subprocess.run(args, cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return {"ok": False, "out": "", "err": "нет программы: %s" % args[0]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": "не ответил за %d с" % timeout}
    except OSError as exc:
        return {"ok": False, "out": "", "err": str(exc)}
    return {"ok": proc.returncode == 0, "code": proc.returncode,
            "out": (proc.stdout or "").strip(), "err": (proc.stderr or "").strip()}


def git(args: list[str], cwd: Path, timeout: int = 600) -> dict[str, Any]:
    return _run(["git"] + args, cwd=cwd, timeout=timeout)


# ──────────────────────────────────────────────────────────── режим копии

def stored_mode() -> str:
    """Режим, выбранный владельцем (пусто — не выбран).

    Переопределение окружением (`MINIGEN_COPY_MODE`) — для проверок и для
    запуска программы в конкретном режиме без правки настроек.
    """
    env = str(os.environ.get("MINIGEN_COPY_MODE") or "").strip()
    if env in MODES:
        return env
    try:
        import minigen_state as state
        raw = str(state.read_settings().get("copyMode") or "").strip()
    except Exception:  # noqa: BLE001
        raw = ""
    return raw if raw in MODES else ""


def derived_mode() -> str:
    """Режим по состоянию машины (когда владелец ещё не выбирал).

    ⚠️ Живой случай 25.11 (CI): режим выводился из «есть ли клон на диске»,
    и на чистой машине (клон ещё не взят) копия вдруг считалась локальной —
    а это меняло поведение программы (пул, публикация генов, PR) в зависимости
    от того, что уже настроено. Поэтому режим выводится только из **явных**
    следов: что записал владелец или что завела сама копия. Нет следов —
    прежнее поведение (`own`), а не догадка по наличию папки.
    """
    data = lineage.instance()
    explicit = str(data.get("copy_mode") or "").strip()
    if explicit in MODES:
        return explicit
    if data.get("copy_repo"):
        return MODE_OWN
    try:
        import minigen_clone as clone_mod
        if (clone_mod.CLONE_DIR / ".git").is_dir():
            return MODE_SHARED
    except Exception:  # noqa: BLE001
        pass
    return MODE_OWN


def mode() -> str:
    """Действующий режим копии."""
    return stored_mode() or derived_mode()


def is_local() -> bool:
    """Режим «только локально, без сети»?"""
    return mode() == MODE_LOCAL


def mode_label(value: str = "") -> str:
    return MODE_LABELS.get(value or mode(), "неизвестный режим")


def mode_options() -> list[dict[str, str]]:
    """Три режима — для окна (подпись + что режим значит)."""
    return [{"id": value, "label": MODE_LABELS[value], "hint": MODE_HINTS[value]}
            for value in MODES]


# ─────────────────────────────────────────────────────── первый запуск

def _instance_data() -> dict[str, Any]:
    return lineage.instance()


def _write_instance(data: dict[str, Any]) -> None:
    lineage._write_json(lineage.INSTANCE_FILE, data)


def first_run_state() -> dict[str, Any]:
    """Состояние первого запуска: спрашивали ли, ответ, выбранный режим."""
    data = _instance_data()
    record = data.get("first_run") or {}
    return {
        "asked": bool(record.get("asked_at")),
        "agreed": bool(record.get("agreed")),
        "mode": str(record.get("mode") or ""),
        "answered_at": int(record.get("answered_at") or 0),
        "instance": str(data.get("instance_id") or ""),
    }


def copies_folder() -> str:
    """Папка копии в общем репозитории (например `copies/mg-693f8fdd`)."""
    instance = str(_instance_data().get("instance_id") or "copy")
    return "%s/%s" % (COPIES_FOLDER, instance)


def offer() -> dict[str, Any]:
    """Текст вопроса первого запуска: репозиторий, папка, что даёт, отказ."""
    return {
        "title": "Регистрировать папку копии в репозитории программы?",
        "repository": SHARED_REPO,
        "folder": copies_folder(),
        "gives": [
            "правки программы и PR в общий репозиторий — их увидит владелец",
            "обмен наработками с другими копиями (пул генов)",
            "родословную копии: видно, от кого пришла наработка",
        ],
        "if_refuse": [
            "программа работает как обычно: чат, области, демоны, правки",
            "правки программы идут в локальный git-репозиторий на этой машине "
            "(история, откат, diff)",
            "PR в общий репозиторий не открываются, обмен с другими копиями "
            "недоступен",
        ],
        "who": "репозиторий принадлежит владельцу; согласие даётся на этой "
               "машине и касается только этой копии",
        "modes": mode_options(),
    }


def answer_first_run(agree: bool, wanted_mode: str = "") -> dict[str, Any]:
    """Ответ владельца на вопрос первого запуска (записывается один раз).

    `agree=False` — отказ: копия работает в локальном режиме, программа не
    ломается. `agree=True` — согласие: режим по умолчанию «папка в общем
    репозитории» (или тот, что выбрал владелец).
    """
    data = _instance_data()
    record = data.get("first_run") or {}
    if record.get("asked_at"):
        # Вопрос показывается один раз: повторный ответ — это уже смена режима.
        return {"ok": False,
                "error": "вопрос первого запуска уже задан (ответ: %s)"
                         % ("согласие" if record.get("agreed") else "отказ")}
    chosen = str(wanted_mode or "").strip()
    if chosen and chosen not in MODES:
        return {"ok": False, "error": "неизвестный режим: %s" % chosen}
    if not agree:
        chosen = MODE_LOCAL
    elif not chosen:
        chosen = MODE_SHARED

    record = {"asked_at": _now(), "answered_at": _now(),
              "agreed": bool(agree), "mode": chosen}
    data["first_run"] = record
    _write_instance(data)
    lineage.record("first_run", agreed=bool(agree), mode=chosen)

    report = _apply_mode(chosen)
    report["ok"] = True
    report["agreed"] = bool(agree)
    report["mode"] = chosen
    report["why"] = ("копия зарегистрирована в общем репозитории: %s"
                     % SHARED_REPO) if agree else (
        "отказ принят: копия работает локально, программа в порядке")
    return report


def _apply_mode(value: str) -> dict[str, Any]:
    """Перевести копию в состояние режима и записать режим в настройки."""
    steps: list[dict[str, Any]] = []
    try:
        import minigen_state as state
        state.write_settings({"copyMode": value})
        steps.append({"what": "настройки", "ok": True, "why": "режим: %s"
                      % MODE_LABELS.get(value, value)})
    except Exception as exc:  # noqa: BLE001
        steps.append({"what": "настройки", "ok": False, "why": str(exc)})
    # Режим — в родословную копии: он виден и без настроек, и переживает их
    # сброс (а настройки — как раз то, что владелец может снести).
    try:
        data = lineage.instance()
        data["copy_mode"] = value
        _write_instance(data)
        steps.append({"what": "родословная копии", "ok": True,
                      "why": "режим записан: %s" % MODE_LABELS.get(value, value)})
    except Exception as exc:  # noqa: BLE001
        steps.append({"what": "родословная копии", "ok": False, "why": str(exc)})

    if value == MODE_LOCAL:
        local = ensure_local_repo()
        steps.append({"what": "локальный репозиторий", "ok": bool(local.get("ok")),
                      "why": local.get("why")})
    else:
        import minigen_clone as clone_mod
        setup = clone_mod.setup(upstream=SHARED_REPO, pool=POOL_REPO,
                                 mode=value)
        steps.append({"what": "клон копии", "ok": bool(setup.get("ok")),
                      "why": setup.get("why") or setup.get("error")})
        for item in setup.get("steps") or []:
            steps.append(item)

    try:
        import minigen_code as code
        code.refresh_repo()
        steps.append({"what": "рабочий репозиторий", "ok": True,
                      "why": str(code.REPO)})
    except Exception as exc:  # noqa: BLE001
        steps.append({"what": "рабочий репозиторий", "ok": False, "why": str(exc)})

    return {"steps": steps}


def set_mode(value: str) -> dict[str, Any]:
    """Сменить режим копии (владелец решил в настройках)."""
    value = str(value or "").strip()
    if value not in MODES:
        return {"ok": False, "error": "неизвестный режим: %s" % (value or "—")}
    report = _apply_mode(value)
    report["ok"] = True
    report["mode"] = value
    report["why"] = "режим копии: %s" % MODE_LABELS[value]
    lineage.record("mode", mode=value)
    return report


# ────────────────────────────────────────────── локальный репозиторий

def local_repo() -> Path:
    """Каталог локального git-репозитория правок."""
    return LOCAL_REPO


def _looks_like_sources(path: Path) -> bool:
    """Есть ли в каталоге исходники программы (`tools/minigen-gui/bridge`)."""
    try:
        return (path / "tools" / "minigen-gui" / "bridge").is_dir()
    except OSError:
        return False


def source_candidates() -> list[Path]:
    """Где на машине могут быть исходники программы (по убыванию приоритета)."""
    out: list[Path] = []
    try:
        import minigen_state as state
        configured = str(state.read_settings().get("codeRepo") or "").strip()
        if configured:
            out.append(Path(configured))
    except Exception:  # noqa: BLE001
        pass
    try:
        import minigen_clone as clone_mod
        out.append(clone_mod.CLONE_DIR)
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _ignore(_directory: str, names: list[str]) -> set[str]:
    """Что не копируем в локальный репозиторий.

    Тяжёлое и служебное — без него; `settings.local.json` — локальные права
    агента, их в репозитории быть не должно (правило владельца: личные
    настройки не коммитятся).
    """
    skip = {name for name in names if name in SKIP_NAMES or name.endswith(".pyc")}
    if "settings.local.json" in names:
        skip.add("settings.local.json")
    return skip


def ensure_local_repo() -> dict[str, Any]:
    """Поднять локальный git-репозиторий для правок программы (если его нет).

    Копия берёт исходники программы с машины (каталог, который задал
    установщик, или клон копии) и делает из них **свой** git-репозиторий:
    история, откат и diff — без сети. Если исходников на машине нет, честно
    говорит об этом: без них править программу нечем.
    """
    repo = local_repo()
    if (repo / ".git").is_dir():
        return {"ok": True, "repo": str(repo),
                "why": "локальный репозиторий уже есть"}
    for source in source_candidates():
        try:
            if not source.is_dir() or not _looks_like_sources(source):
                continue
            if source.resolve() == repo.resolve():
                continue
        except OSError:
            continue
        repo.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(source, repo, ignore=_ignore, dirs_exist_ok=True)
        except (OSError, shutil.Error) as exc:
            return {"ok": False, "repo": str(repo),
                    "why": "не скопировать исходники: %s" % exc}
        init = git(["init"], repo)
        git(["branch", "-M", "master"], repo)
        git(["add", "-A"], repo)
        commit = git(["-c", "user.name=МиниГен", "-c", "user.email=minigen@local",
                      "commit", "-m", "основа программы: локальный репозиторий копии"],
                     repo)
        if not commit.get("ok"):
            return {"ok": False, "repo": str(repo),
                    "why": "коммит не прошёл: %s" % (commit.get("err") or commit.get("out"))}
        return {"ok": True, "repo": str(repo), "source": str(source),
                "why": "локальный репозиторий создан (история и откат работают "
                       "без сети)"}
    return {"ok": False, "repo": str(repo),
            "why": "локального репозитория нет и взять нечего: исходников "
                   "программы на этой машине не найдено"}


def local_repo_state() -> dict[str, Any]:
    """Состояние локального репозитория (для окна и `clone.status`)."""
    repo = local_repo()
    info: dict[str, Any] = {"path": str(repo), "exists": (repo / ".git").is_dir()}
    if info["exists"]:
        head = git(["rev-parse", "--short", "HEAD"], repo)
        info["commit"] = head.get("out") or ""
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        info["branch"] = branch.get("out") or ""
        dirty = git(["status", "--porcelain"], repo)
        info["dirty"] = bool(dirty.get("out"))
        info["remotes"] = git(["remote", "-v"], repo).get("out") or ""
    return info


# ────────────────────────────────────────────────────── обмен наработками

def exchange_state() -> dict[str, Any]:
    """Доступен ли обмен наработками с другими копиями (и почему — если нет)."""
    if is_local():
        return {"available": False, "transport": "нет",
                "pool": "", "pool_exists": False,
                "why": "локальный режим: сети нет — обмен с другими копиями "
                       "недоступен. Вручную: выгрузите папку наработок "
                       "(genes.export) и отнесите соседней копии"}
    try:
        import minigen_clone as clone_mod
        pool = clone_mod.POOL_DIR
        exists = (pool / ".git").is_dir()
    except Exception:  # noqa: BLE001
        pool, exists = None, False
    if not exists:
        return {"available": False, "transport": "нет",
                "pool": str(pool or ""), "pool_exists": False,
                "why": "пул наработок не взят — обмена нет (команда clone.setup)"}
    return {"available": True, "transport": "пул наработок",
            "pool": str(pool), "pool_exists": True,
            "why": "обмен идёт через пул наработок: %s" % pool}
