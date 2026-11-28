"""Удаление агента «МиниГена» целиком (промт КАРМАНА #208).

**Что такое «удалить агента».** Агент — это рабочая область проекта, и он
состоит из нескольких частей:

| часть | где |
|---|---|
| запись в настройке областей | `C:\\Users\\valstan\\bin\\mcode-project-areas.json` |
| рабочая папка проекта (с репозиторием) | поле `folder` в той же настройке |
| каталог данных области (авторизация) | `~/.minimax-areas/p-<slug>` |
| папка агента в данных программы (конституция, описание) | `%LOCALAPPDATA%\\minigen-gui\\agents\\<реестр>\\agents\\<slug>` |
| паспорта ниток области | `%LOCALAPPDATA%\\minigen-gui\\passports\\<slug>` |
| ярлыки на рабочем столе | `<Название> — MiniMax.lnk` и `… (стоп).lnk` |

**Порядок обязателен** (решение владельца, риск удаления необратим):

1. **проверить** — всё ли отправлено на GitHub (нет неотправленных коммитов
   и незакоммиченных правок);
2. **показать** — что именно исчезнет;
3. **спросить** — подтверждение владельца;
4. **удалить**.

Если что-то не отправлено — удаление **не проходит**, и в ответе видно, что
именно осталось неотправленным.

⚠️ **Раздел КАРМАНА агента не трогаем.** Там история заявок; её удаление —
отдельное решение владельца (риск для данных). То же и с записью агента в
реестре на GitHub: локальную папку чистим, а реестр — решение владельца.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from minigen_code import git

#: Стор программы на машине (данные «МиниГена»).
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"

#: Папка паспортов ниток (по области).
PASSPORTS_HOME = LOCAL / "passports"

#: Куда кладутся клоны реестра агентов.
REGISTRY_HOME = LOCAL / "agents"


# ────────────────────────────────────────────────────────────── проверка git

def git_sync_state(folder: Path) -> dict[str, Any]:
    """Всё ли отправлено на GitHub — по состоянию рабочей папки.

    Возвращает:

    - `is_repo` — папка вообще под git;
    - `branch`, `remote` — где мы;
    - `dirty` — незакоммиченные правки (список файлов);
    - `unpushed` — коммиты, которых нет на GitHub (список строк);
    - `ok` — можно ли удалять;
    - `why` — человеческим языком, что мешает.
    """
    out: dict[str, Any] = {
        "is_repo": False, "branch": "", "remote": "", "upstream": "",
        "dirty": [], "unpushed": [], "ok": True, "why": "",
    }
    if not folder or not folder.is_dir():
        out["why"] = "рабочей папки нет — проверять нечего"
        return out
    if not (folder / ".git").exists():
        out["why"] = "папка не под git — неотправленного ничего нет"
        return out
    out["is_repo"] = True
    out["branch"] = git(["rev-parse", "--abbrev-ref", "HEAD"], folder).get("out") or ""
    out["remote"] = git(["remote", "get-url", "origin"], folder).get("out") or ""
    upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                   folder).get("out") or ""
    out["upstream"] = upstream
    ref = upstream
    if not ref and out["branch"]:
        ref = "origin/%s" % out["branch"]
    status = git(["status", "--porcelain"], folder)
    for line in (status.get("out") or "").splitlines():
        if len(line) > 3:
            out["dirty"].append(line[3:].strip().strip('"'))
    if not ref:
        out["ok"] = False
        out["why"] = ("не удалось понять, с чем сравнивать (нет ветки-ориентира "
                      "на GitHub) — покажите ветку вручную")
        return out
    ahead = git(["rev-list", "--count", "%s..HEAD" % ref], folder)
    count = 0
    try:
        count = int((ahead.get("out") or "0").strip() or 0)
    except ValueError:
        count = 0
    if count:
        log = git(["log", "--oneline", "--no-decorate", "%s..HEAD" % ref], folder)
        out["unpushed"] = [line for line in (log.get("out") or "").splitlines() if line]
    out["ok"] = not out["dirty"] and not out["unpushed"]
    if out["ok"]:
        out["why"] = "всё отправлено на GitHub (%s)" % (out["branch"] or ref)
    return out


# ──────────────────────────────────────────────────────────────── что удаляем

def _desktop_dir() -> Path:
    """Рабочий стол владельца (тот же путь, что у скрипта ярлыков)."""
    home = Path.home()
    for name in ("Desktop", "Рабочий стол"):
        path = home / name
        if path.is_dir():
            return path
    return home / "Desktop"


def _registry_agents_dir() -> Path:
    """Папка `agents/` в локальном клоне реестра агентов."""
    try:
        import minigen_agents
        return minigen_agents.registry_dir() / "agents"
    except Exception:  # noqa: BLE001 — без реестра просто чистим остальное
        return REGISTRY_HOME


def collect(slug: str, title: str = "", folder: str = "",
            area_dir: str = "") -> dict[str, Any]:
    """Что именно удалится — список целей (ничего не удаляем)."""
    slug = str(slug or "").strip()
    desktop = _desktop_dir()
    targets: list[dict[str, Any]] = []

    def add(path: Path | str, what: str) -> None:
        text = str(path)
        if not text:
            return
        targets.append({"what": what, "path": text, "exists": Path(text).exists()})

    add(folder, "рабочая папка проекта")
    add(PASSPORTS_HOME / slug, "паспорта ниток области")
    add(_registry_agents_dir() / slug, "папка агента (конституция и описание)")
    # каталог данных области: там авторизация. Путь берём у ядра
    # (`core.areas()` → `area["dir"]`) — это единственный источник правды,
    # второй раз путь вычислять нельзя.
    if area_dir:
        add(area_dir, "каталог области (авторизация)")
    for suffix in (" — MiniMax.lnk", " — MiniMax (стоп).lnk"):
        add(desktop / ("%s%s" % (title or slug, suffix)), "ярлык на рабочем столе")
    return {
        "slug": slug,
        "title": title,
        "folder": folder,
        "targets": targets,
        "config": "C:\\Users\\valstan\\bin\\mcode-project-areas.json",
    }


# ───────────────────────────────────────────────────────────── удаление папок

def _remove_tree(path: Path) -> tuple[bool, str]:
    """Удалить папку с диска (на Windows бывают read-only файлы)."""
    if not path.exists():
        return True, ""
    try:
        shutil.rmtree(path)
    except OSError as exc:
        # read-only файлы (например, внутри .git) — снимаем атрибут и пробуем
        def _fix(func, target, _exc):
            try:
                os.chmod(target, 0o700)
                func(target)
            except OSError:
                pass
        try:
            shutil.rmtree(path, onerror=_fix)
        except OSError as exc2:
            return False, str(exc2)
        except Exception as exc3:  # noqa: BLE001
            return False, str(exc3)
    return True, ""


def _remove_file(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, ""
    try:
        path.unlink()
    except OSError as exc:
        return False, str(exc)
    return True, ""


def _stop_processes(core: Any, mods: dict[str, Any], slug: str) -> dict[str, Any]:
    """Остановить процессы области (если они идут)."""
    out: dict[str, Any] = {"stopped": False, "killed": 0, "why": ""}
    autheal = mods.get("autheal")
    if autheal is None:
        out["why"] = "лечащий недоступен — процессы не трогал"
        return out
    try:
        script = core.BIN / "mcode-project-stop.ps1"
        if script.is_file():
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", str(script), "-Project", slug],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=240, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        if autheal.wait_area_stopped(slug, 20):
            out["stopped"] = True
            return out
        out["killed"] = int(autheal.kill_area_processes(slug) or 0)
        out["stopped"] = bool(autheal.wait_area_stopped(slug, 15))
    except Exception as exc:  # noqa: BLE001 — процессы не должны мешать чистке
        out["why"] = str(exc)
    return out


def _drop_config_record(slug: str) -> tuple[bool, str]:
    """Убрать запись области из настройки областей."""
    import minigen_state as state
    cfg = state.read_projects()
    projects = cfg.get("projects") or []
    kept = [p for p in projects if str(p.get("slug") or "") != slug]
    if len(kept) == len(projects):
        return True, "записи не было"
    cfg["projects"] = kept
    ok, err = state.write_projects(cfg)
    return ok, err


def delete_agent(core: Any, mods: dict[str, Any], slug: str, title: str = "",
                 folder: str = "", area_dir: str = "") -> dict[str, Any]:
    """Удалить агента целиком (после проверки синхронизации с GitHub)."""
    slug = str(slug or "").strip()
    if not slug:
        return {"ok": False, "error": "не указан агент"}
    sync = git_sync_state(Path(folder)) if folder else {
        "is_repo": False, "branch": "", "remote": "", "upstream": "",
        "dirty": [], "unpushed": [], "ok": True, "why": "рабочей папки нет"}
    if not sync.get("ok"):
        return {"ok": False, "error": "удаление не проходит: не всё отправлено "
                                      "на GitHub", "git": sync}
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    stop = _stop_processes(core, mods, slug)
    ok, err = _drop_config_record(slug)
    if not ok:
        failed.append({"what": "запись в настройке областей", "why": err})
    else:
        removed.append("запись в настройке областей")
    plan = collect(slug, title, folder, area_dir)
    for target in plan["targets"]:
        path = Path(target["path"])
        if not target.get("exists"):
            continue
        if target["what"] == "рабочая папка проекта":
            good, why = _remove_tree(path)
        elif target["what"].startswith("ярлык"):
            good, why = _remove_file(path)
        else:
            good, why = _remove_tree(path)
        if good:
            removed.append("%s (%s)" % (target["what"], target["path"]))
        else:
            failed.append({"what": target["what"], "path": target["path"], "why": why})
    note = "раздел КАРМАНА не тронут (это отдельное решение владельца)"
    try:
        core.log("Панель «МиниГен»: удалён агент %s — %s%s" % (
            slug, "; ".join(removed) or "ничего",
            ("" if not failed else "; НЕ удалено: %s" % "; ".join(
                "%s (%s)" % (item["what"], item.get("why", "")) for item in failed))))
    except Exception:  # noqa: BLE001 — журнал не должен ломать удаление
        pass
    return {"ok": not failed, "slug": slug, "removed": removed, "failed": failed,
            "stopped": stop, "git": sync, "note": note}
