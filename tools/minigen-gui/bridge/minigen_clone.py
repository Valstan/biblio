"""Свой клон копии «МиниГена» на GitHub (заказ владельца 24.11).

Заказ: «для каждого нового запущенного минигена на каждом компьютере она
должна создавать свой клон на гитхабе и вести разработку уже со своим клоном
репозитория… взять репо и разрабатывать дальше самому».

Что здесь:

- **репозиторий копии** — своё место на GitHub, где копия правит программу;
  это её «свой клон»;
- **клон** рядом с программой (`<Programs>\\МиниГен\\clone`) — рабочее дерево,
  в котором правки делает встроенная нейросеть;
- **upstream** — репозиторий программы, куда идут PR;
- **пул генов** — отдельный репозиторий, где копии обмениваются наработками.

⚠️ Про «настоящий форк». GitHub **не даёт** завести форк своего же
репозитория в своём же аккаунте («A single user account cannot own both a
parent and fork»). Поэтому копия заводит **свой репозиторий**
(`minigen-<идентификатор копии>`) и указывает `upstream` на репозиторий
программы: ветка живёт в своём репозитории, PR идёт в upstream — ровно тот
рабочий процесс, ради которого форк и нужен. Если форк всё же возможен
(например, появится организация), код предпочтёт его.

Правила:

- **наружу — только идентификатор копии и хеши коммитов**: ни имён машин, ни
  путей, ни адресов (D-038);
- **решение о создании репозитория — за владельцем**: команда опасная, идёт
  через подтверждение;
- **пуш — только в свой репозиторий** и только из ветки; `master` не трогаем.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import minigen_lineage as lineage

PROGRAM_ROOT = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "Programs" / "МиниГен")
# Клон копии — рядом с программой (владелец: «в свою удобную папку, рядом»).
CLONE_DIR = PROGRAM_ROOT / "clone"
# Пул генов — общий репозиторий наработок между копиями.
POOL_DIR = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "minigen-gui" / "genepool")

DEFAULT_UPSTREAM = "Valstan/biblio"
DEFAULT_POOL = "Valstan/minigen-genepool"
COPY_REPO_PREFIX = "minigen"


def _run(args: list[str], cwd: Path | None = None, timeout: int = 300) -> dict[str, Any]:
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


def git(args: list[str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
    return _run(["git"] + args, cwd=cwd, timeout=timeout)


def gh(args: list[str], timeout: int = 300) -> dict[str, Any]:
    return _run(["gh"] + args, timeout=timeout)


def copy_repo_name(instance_id: str = "") -> str:
    """Имя репозитория копии: `minigen-<идентификатор>`."""
    ident = (instance_id or lineage.instance().get("instance_id") or "").replace("mg-", "")
    return "%s-%s" % (COPY_REPO_PREFIX, ident or "copy")


def _owner_of(slug: str) -> str:
    """Владелец репозитория из `owner/name`."""
    return (slug or "").split("/")[0] if "/" in (slug or "") else ""


def _repo_exists(name: str) -> bool:
    res = gh(["repo", "view", name, "--json", "name"])
    return bool(res.get("ok"))


def _remotes(cwd: Path) -> dict[str, str]:
    res = git(["remote", "-v"], cwd)
    out: dict[str, str] = {}
    for line in (res.get("out") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in out:
            out[parts[0]] = parts[1]
    return out


def status() -> dict[str, Any]:
    """Состояние клона копии и пула генов."""
    data = lineage.instance()
    info: dict[str, Any] = {
        "instance_id": data.get("instance_id"),
        "family": data.get("family"),
        "generation": data.get("generation"),
        "clone_dir": str(CLONE_DIR),
        "clone_exists": (CLONE_DIR / ".git").is_dir(),
        "pool_dir": str(POOL_DIR),
        "pool_exists": (POOL_DIR / ".git").is_dir(),
        "copy_repo": data.get("copy_repo") or "",
        "upstream": data.get("upstream") or DEFAULT_UPSTREAM,
    }
    if info["clone_exists"]:
        res = git(["rev-parse", "--abbrev-ref", "HEAD"], CLONE_DIR)
        info["branch"] = res.get("out") or ""
        info["remotes"] = _remotes(CLONE_DIR)
        dirty = git(["status", "--porcelain"], CLONE_DIR)
        info["dirty"] = bool(dirty.get("out"))
    if info["pool_exists"]:
        info["pool_remotes"] = _remotes(POOL_DIR)
    return info


def setup(upstream: str = DEFAULT_UPSTREAM, pool: str = DEFAULT_POOL,
          private: bool = True) -> dict[str, Any]:
    """Завести репозиторий копии на GitHub и склонировать его (+ пул генов).

    Опасное действие: создаёт репозитории в аккаунте владельца ⇒ только после
    подтверждения.
    """
    data = lineage.instance()
    instance_id = str(data.get("instance_id") or "")
    repo_name = copy_repo_name(instance_id)
    report: dict[str, Any] = {"instance_id": instance_id, "repo": repo_name,
                              "upstream": upstream, "pool": pool, "steps": []}

    # 1) свой репозиторий копии (или форк, если платформа его разрешит)
    owner = _owner_of(upstream) or "Valstan"
    full_name = "%s/%s" % (owner, repo_name)
    if _repo_exists(full_name):
        report["steps"].append({"repo": full_name, "how": "уже есть"})
    else:
        fork = gh(["repo", "fork", upstream, "--clone=false"])
        if fork.get("ok"):
            report["steps"].append({"repo": full_name, "how": "форк"})
        else:
            create = gh(["repo", "create", full_name, "--description",
                         "МиниГен: копия %s (своя разработка программы)" % instance_id]
                        + (["--private"] if private else []))
            if not create.get("ok"):
                report["ok"] = False
                report["why"] = "репозиторий не создался: %s" % (create.get("err") or create.get("out"))
                return report
            report["steps"].append({"repo": full_name, "how": "свой репозиторий"})
    url = "https://github.com/%s.git" % full_name

    # 2) клон рядом с программой
    if (CLONE_DIR / ".git").is_dir():
        report["steps"].append({"clone": str(CLONE_DIR), "how": "уже есть"})
    else:
        CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
        clone = git(["clone", url, str(CLONE_DIR)], CLONE_DIR.parent, timeout=900)
        if not clone.get("ok"):
            report["ok"] = False
            report["why"] = "клон не взялся: %s" % (clone.get("err") or clone.get("out"))
            return report
        report["steps"].append({"clone": str(CLONE_DIR), "how": "взят"})

    # 3) upstream — репозиторий программы
    remotes = _remotes(CLONE_DIR)
    if "upstream" not in remotes:
        add = git(["remote", "add", "upstream", "https://github.com/%s.git" % upstream],
                  CLONE_DIR)
        report["steps"].append({"upstream": upstream,
                                "how": "добавлен" if add.get("ok") else "не добавился"})
    if "origin" in remotes:
        git(["remote", "set-url", "origin", url], CLONE_DIR)

    # 3a) берём код программы в локальную ветку master (она смотрит в
    #     upstream). В **свой** репозиторий код программы кладём веткой `base`:
    #     на машине глобальный хук (ADR-0002, постулат 17) запрещает прямой
    #     пуш в master любого репозитория, а `base` — не master и ничего не
    #     нарушает: канон программы (её master) остаётся нетронутым, PR идут
    #     в upstream. Репозиторий копии живёт на ветках правок.
    fetch = git(["fetch", "upstream", "master"], CLONE_DIR, timeout=600)
    if fetch.get("ok"):
        git(["checkout", "-B", "master", "FETCH_HEAD"], CLONE_DIR)
        git(["branch", "--set-upstream-to=upstream/master", "master"], CLONE_DIR)
        push_base = git(["push", "origin", "FETCH_HEAD:refs/heads/base"], CLONE_DIR,
                         timeout=600)
        if push_base.get("ok"):
            gh(["repo", "edit", full_name, "--default-branch", "base"])
        report["steps"].append({"seed": "base",
                                "how": "код программы в ветке base (master пушить "
                                       "нельзя — правило машины)"})
    else:
        report["steps"].append({"seed": "base", "how": "upstream не отдал master"})

    # 4) пул генов
    if (POOL_DIR / ".git").is_dir():
        report["steps"].append({"pool": str(POOL_DIR), "how": "уже есть"})
    else:
        POOL_DIR.parent.mkdir(parents=True, exist_ok=True)
        clone_pool = git(["clone", "https://github.com/%s.git" % pool, str(POOL_DIR)],
                         POOL_DIR.parent, timeout=600)
        report["steps"].append({"pool": str(POOL_DIR),
                                "how": "взят" if clone_pool.get("ok") else "не взялся"})
        if not clone_pool.get("ok"):
            (POOL_DIR / ".git").mkdir(parents=True, exist_ok=True)
            git(["init", str(POOL_DIR)], POOL_DIR.parent)

    # 5) родословная: от какой версии форк и куда мы пишем
    data["copy_repo"] = repo_name
    data["upstream"] = upstream
    data["fork_of"] = data.get("fork_of") or {"commit": "", "installed_at": 0}
    lineage._write_json(lineage.INSTANCE_FILE, data)
    lineage.record("clone", repo=repo_name, upstream=upstream, pool=pool)
    report["ok"] = True
    report["why"] = "клон копии готов: %s (upstream %s)" % (repo_name, upstream)
    return report


def sync() -> dict[str, Any]:
    """Обновить клон и пул (только fast-forward, локальные правки не трогаем)."""
    out: dict[str, Any] = {"steps": []}
    for name, path in (("clone", CLONE_DIR), ("pool", POOL_DIR)):
        if not (path / ".git").is_dir():
            out["steps"].append({"what": name, "ok": False, "why": "клона нет"})
            continue
        dirty = git(["status", "--porcelain"], path)
        if dirty.get("out"):
            out["steps"].append({"what": name, "ok": False,
                                 "why": "есть локальные правки — не трогаю"})
            continue
        fetch = git(["fetch", "--all", "--prune"], path, timeout=600)
        if not fetch.get("ok"):
            out["steps"].append({"what": name, "ok": False, "why": fetch.get("err")})
            continue
        pull = git(["pull", "--ff-only"], path, timeout=600)
        out["steps"].append({"what": name, "ok": bool(pull.get("ok")),
                             "why": pull.get("out") or pull.get("err")})
    out["ok"] = all(bool(s.get("ok")) for s in out["steps"])
    return out
