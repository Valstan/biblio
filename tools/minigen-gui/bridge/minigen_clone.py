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
import time
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


def _safe_ident(value: str) -> str:
    """Идентификатор копии, пригодный для пути (ветка и папка)."""
    raw = "".join(ch for ch in str(value or "")
                  if ch.isalnum() or ch in "-_.").strip()
    if not raw or raw in (".", "..") or ".." in raw:
        return "copy"
    return raw


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
    # ⚠️ Режим копии (заказ владельца 25.11): смена режима должна быть видна
    # в `clone.status` — иначе «режим» был бы только строчкой в настройках.
    try:
        import minigen_copy
        info["mode"] = minigen_copy.mode()
        info["mode_label"] = minigen_copy.mode_label()
        info["exchange"] = minigen_copy.exchange_state()
        info["local_repo"] = minigen_copy.local_repo_state()
        info["first_run"] = minigen_copy.first_run_state()
    except Exception as exc:  # noqa: BLE001
        info["mode"] = ""
        info["mode_label"] = "неизвестно"
        info["exchange"] = {"available": False, "transport": "нет", "pool": "",
                            "pool_exists": False, "why": str(exc)}
        info["local_repo"] = {"path": "", "exists": False}
        info["first_run"] = {"asked": False, "agreed": False, "mode": "",
                             "answered_at": 0, "instance": ""}
    if info["clone_exists"]:
        res = git(["rev-parse", "--abbrev-ref", "HEAD"], CLONE_DIR)
        info["branch"] = res.get("out") or ""
        info["remotes"] = _remotes(CLONE_DIR)
        dirty = git(["status", "--porcelain"], CLONE_DIR)
        info["dirty"] = bool(dirty.get("out"))
    if info["pool_exists"]:
        info["pool_remotes"] = _remotes(POOL_DIR)
    return info


def register_folder(instance_id: str = "", upstream: str = DEFAULT_UPSTREAM
                    ) -> dict[str, Any]:
    """Завести **папку копии** в общем репозитории программы.

    Папка — `copies/<идентификатор копии>/`: в ней файл регистрации (кто
    копия, от какой версии форк, когда зарегистрирована) — без путей и имён
    машин (D-038). Ветка — `copies/<идентификатор копии>`: правки копии не
    мешают другим копиям и не требуют аккаунта на GitHub.

    Опасное действие: пишет в репозиторий программы ⇒ только после
    подтверждения владельца.
    """
    data = lineage.instance()
    ident = _safe_ident(instance_id or str(data.get("instance_id") or "copy"))
    branch = "copies/%s" % ident
    folder = "copies/%s" % ident
    report: dict[str, Any] = {"instance_id": ident, "branch": branch,
                              "folder": folder, "upstream": upstream,
                              "steps": []}
    if not (CLONE_DIR / ".git").is_dir():
        report["ok"] = False
        report["why"] = "клона нет — сначала clone.setup"
        return report
    # куда пушить: remote, чей адрес — репозиторий программы
    # ⚠️ Сравнение строгое: «github.com/Valstan/biblio» — это подстрока и
    # «github.com/Valstan/biblio-minigen», а такой пушить нельзя.
    target = ""
    want = "github.com/%s" % upstream
    remotes = _remotes(CLONE_DIR)
    for name, url in remotes.items():
        clean = url.strip()
        if clean.endswith(".git"):
            clean = clean[:-4]
        if clean == want or clean.endswith("/" + want):
            target = name
            break
    if not target:
        report["ok"] = False
        report["why"] = ("не найден remote на репозиторий программы (%s) — "
                         "ветку некуда отправить" % upstream)
        return report
    checkout = git(["checkout", "-B", branch, "master"], CLONE_DIR)
    if not checkout.get("ok"):
        report["ok"] = False
        report["why"] = "ветку не создать: %s" % (checkout.get("err") or checkout.get("out"))
        return report
    marker = {
        "instance_id": ident,
        "family": str(data.get("family") or "minigen"),
        "generation": int(data.get("generation") or 1),
        "fork_commit": str((data.get("fork_of") or {}).get("commit") or ""),
        "registered_at": int(time.time() * 1000),
    }
    path = CLONE_DIR / folder / "copy.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marker, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        report["ok"] = False
        report["why"] = "не записать регистрацию папки: %s" % exc
        return report
    add = git(["add", "-A", "copies"], CLONE_DIR)
    if not add.get("ok"):
        report["ok"] = False
        report["why"] = "не добавить в индекс: %s" % (add.get("err") or add.get("out"))
        return report
    commit = git(["commit", "-m", "copy: register %s" % ident], CLONE_DIR, timeout=180)
    if not commit.get("ok"):
        report["ok"] = False
        report["why"] = "коммит не прошёл: %s" % (commit.get("err") or commit.get("out"))
        return report
    push = git(["push", target, "HEAD:refs/heads/%s" % branch], CLONE_DIR, timeout=600)
    if not push.get("ok"):
        report["ok"] = False
        report["why"] = "папка не ушла в репозиторий программы: %s" % (
            push.get("err") or push.get("out"))
        return report
    report["ok"] = True
    report["why"] = "папка копии заведена в общем репозитории: %s (ветка %s)" % (
        folder, branch)
    report["steps"].append({"folder": folder, "branch": branch, "how": "заведена"})
    return report


def setup(upstream: str = DEFAULT_UPSTREAM, pool: str = DEFAULT_POOL,
          private: bool = True, mode: str = "shared") -> dict[str, Any]:
    """Завести копию: репозиторий копии и клон (+ пул генов).

    Опасное действие: создаёт репозитории в аккаунте владельца ⇒ только после
    подтверждения.

    Режимы (заказ владельца 25.11, промт #131 и #130):

    - `shared` — **дефолт**: копия работает **папкой в общем репозитории
      программы** (`copies/<идентификатор копии>/`); свой репозиторий на GitHub
      не заводится и аккаунт не нужен;
    - `own` — прежнее поведение: копия заводит **свой** репозиторий на
      GitHub (`minigen-<id>`), ветки живут в нём, PR идут в репозиторий
      программы;
    - `local` — сети нет: клона не берётся, правки идут в локальный
      git-репозиторий на машине (`minigen_copy.ensure_local_repo`).
    """
    if mode == "local":
        import minigen_copy
        local = minigen_copy.ensure_local_repo()
        return {"ok": bool(local.get("ok")), "mode": "local",
                "repo": local.get("repo") or "",
                "steps": [{"what": "локальный репозиторий",
                           "ok": bool(local.get("ok")),
                           "why": local.get("why")}],
                "why": local.get("why")}
    shared = mode == "shared"
    data = lineage.instance()
    instance_id = str(data.get("instance_id") or "")
    repo_name = copy_repo_name(instance_id)
    report: dict[str, Any] = {"instance_id": instance_id,
                              "repo": (upstream if shared else repo_name),
                              "upstream": upstream, "pool": pool, "mode": mode,
                              "steps": []}

    # 1) свой репозиторий копии (или форк, если платформа его разрешит)
    owner = _owner_of(upstream) or "Valstan"
    full_name = "%s/%s" % (owner, repo_name)
    if shared:
        # Режим «папка в общем репозитории»: своего репозитория не заводим —
        # копия правит программу там же, где она живёт.
        report["steps"].append({"repo": full_name,
                                "how": "свой репозиторий не нужен — общий"})
    elif _repo_exists(full_name):
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
        clone_url = ("https://github.com/%s.git" % upstream) if shared else url
        clone = git(["clone", clone_url, str(CLONE_DIR)], CLONE_DIR.parent, timeout=900)
        if not clone.get("ok"):
            report["ok"] = False
            report["why"] = "клон не взялся: %s" % (clone.get("err") or clone.get("out"))
            return report
        report["steps"].append({"clone": str(CLONE_DIR), "how": "взят"})

    # 3) upstream — репозиторий программы
    remotes = _remotes(CLONE_DIR)
    if shared:
        # ⚠️ Живой случай 25.11: первая версия режима «общий репозиторий»
        # переставляла `origin` у уже взятого клона. У живого копии свой
        # origin — её репозиторий, и молчаливая перестановка сломала бы
        # настроенную машину. Теперь: клона нет — берём общий репозиторий
        # сразу; клон есть — репозитории не трогаем, а режим меняет только
        # то, **куда уходит ветка** (см. `code.open_pr`).
        if not remotes:
            git(["remote", "set-url", "origin",
                 "https://github.com/%s.git" % upstream], CLONE_DIR)
            report["steps"].append({"origin": upstream,
                                    "how": "общий репозиторий (клон свежий)"})
        else:
            report["steps"].append({"origin": remotes.get("origin", ""),
                                    "how": "репозитории клона не переставляем — "
                                           "меняется только то, куда уходит ветка"})
    else:
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
    fetch_from = "origin" if (shared and "upstream" not in remotes) else "upstream"
    fetch = git(["fetch", fetch_from, "master"], CLONE_DIR, timeout=600)
    if fetch.get("ok"):
        git(["checkout", "-B", "master", "FETCH_HEAD"], CLONE_DIR)
        if shared:
            # В режиме «общий репозиторий» ветка правок уходит прямо в
            # репозиторий программы: пушить в master нельзя (правило машины),
            # но PR открывается оттуда.
            git(["branch", "--set-upstream-to=%s/master" % fetch_from, "master"], CLONE_DIR)
            report["steps"].append({"master": "от %s (общий репозиторий)" % fetch_from,
                                    "how": "правки и PR — в общий репозиторий"})
        else:
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
    # ветка пула — `pool`: глобальный хук машины (ADR-0002) запрещает прямой
    # пуш в `main` и `master` любого репозитория, а пул — не код программы,
    # а обмен наработками, поэтому он живёт на своей ветке.
    git(["branch", "-M", "pool"], POOL_DIR)
    gh(["repo", "edit", pool, "--default-branch", "pool"])

    # 5) родословная: от какой версии форк и куда мы пишем
    if not shared:
        data["copy_repo"] = repo_name
    data["upstream"] = upstream
    data["copy_mode"] = mode
    data["fork_of"] = data.get("fork_of") or {"commit": "", "installed_at": 0}
    lineage._write_json(lineage.INSTANCE_FILE, data)
    lineage.record("clone", repo=repo_name, upstream=upstream, pool=pool, mode=mode)

    # 6) копия появилась на карте: «в какой точке родилась» — это коммит форка.
    #    Пишем в пул, чтобы карта была общей: каждая копия дописывает в неё
    #    свои связи, а окно собирает карту из пула (заказ владельца 24.11).
    try:
        import minigen_genes as genes
        born = genes.publish_birth(str((data.get("fork_of") or {}).get("commit") or ""),
                                   repo=repo_name, upstream=upstream)
        report["steps"].append({"born": "в пуле" if born.get("ok") else "не записано",
                                "how": born.get("why") or born.get("error")})
    except Exception as exc:  # noqa: BLE001
        report["steps"].append({"born": "не записано", "how": str(exc)})

    # 7) папка копии в общем репозитории (режим «папка в общем репозитории»):
    #    `copies/<идентификатор копии>/` — свои правки копии никому не мешают,
    #    и аккаунт на GitHub не нужен.
    if shared:
        folder = register_folder(instance_id, upstream)
        report["steps"].append({"folder": folder.get("folder", ""),
                                "ok": bool(folder.get("ok")),
                                "why": folder.get("why")})
        if not folder.get("ok"):
            report["ok"] = False
            report["why"] = folder.get("why")
            return report

    report["ok"] = True
    report["why"] = "клон копии готов: %s (upstream %s)" % (repo_name, upstream)
    return report


def sync() -> dict[str, Any]:
    """Обновить клон и пул (только fast-forward, локальные правки не трогаем)."""
    out: dict[str, Any] = {"steps": []}
    # ⚠️ Локальный режим (заказ владельца 25.11): сети нет — обновлять
    # нечего. Говорим прямо, а не делаем вид, что «обновление прошло».
    try:
        import minigen_copy
        if minigen_copy.is_local():
            return {"ok": False, "steps": [
                {"what": "клон", "ok": False,
                 "why": "локальный режим: сети нет — обновлять нечего"}],
                "why": "локальный режим: обмен и обновление с другими копиями "
                       "недоступны"}
    except Exception:  # noqa: BLE001
        pass
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
