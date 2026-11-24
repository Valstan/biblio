"""Агенты «МиниГена»: реестр, создание, восстановление.

Заказ владельца (24.11): «нужно сделать так, чтобы миниген мог создавать вот
таких же агентов, новых, подбирать репозитории, в которых они будут
размещаться, или создавать, может быть, новые репозитории в гитхабе, и чтобы
всё это хранить, конечно, через гитхаб, чтобы мало ли, потом восстановить
можно было на любом другом компьютере. Эти агенты должны быть
специализированными, у них у каждого свой мозг, своя конституция, свои скиллы».

**Что такое агент здесь:**

- **область** (`p-<slug>`) — его рабочая среда в «МиниГене»: изолированный
  профиль рантайма со своим мозгом и сессиями;
- **репозиторий** — его рабочее место (код, наработки, заявки);
- **конституция** (`AGENTS.md` в репозитории) — правила: что он делает и чего
  не делает;
- **скиллы** (`skills/<имя>/SKILL.md`) — умения: как именно он это делает.

**Где это живёт.** Реестр агентов — **репозиторий на GitHub**
(`Valstan/minigen-agents`): `agents/<slug>/agent.yaml` + копия конституции и
скиллов. Реестр — источник правды, поэтому агента можно восстановить на
любом другом компьютере одной командой (`agents.apply`): реестр → манифест →
репозиторий агента, область, скиллы.

**Правила:**

- репозиторий агента и репозиторий-реестр — **разные**: агент работает в
  своём, а список всех агентов лежит в реестре;
- области и репозитории не трогаем без подтверждения владельца — создание
  агента меняет GitHub и конфиг областей;
- если репозиторий уже есть — **не создаём второй**, а берём существующий
  (урок 22.11: две копии одного агента — брак);
- **секретов в репозитории нет** (D-038): ни адресов, ни токенов.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # noqa: BLE001
    # Манифест — YAML; без PyYAML модуль всё равно импортируется (мост не
    # должен падать на старте из-за одной необязательной зависимости), но
    # функции манифеста честно скажут, чего не хватает.
    yaml = None  # type: ignore[assignment]

from minigen_code import gh, git

#: Репозиторий-реестр (источник правды: восстанавливается на любой машине).
DEFAULT_REGISTRY = "Valstan/minigen-agents"

#: Куда кладём локальный клон реестра.
REGISTRY_HOME = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                 / "minigen-gui" / "agents")

#: Куда кладём шаблоны агентов (они едут вместе с программой).
TEMPLATES_HOME = Path(__file__).resolve().parent / "agent-templates"

#: Куда кладём рабочие репозитории агентов (по соглашению владельца).
DEFAULT_WORKSPACE = Path(r"D:\valstan\REPO")

MANIFEST_NAME = "agent.yaml"

#: Имя ветки, куда кладутся заявки агента.
REQUESTS_BRANCH = "requests"

#: Имя каталога с заявками в репозитории агента.
REQUESTS_DIR = "requests"


# ──────────────────────────────────────────────────────────── реестр

def registry_slug() -> str:
    """Репозиторий-реестр (можно переопределить в настройках программы)."""
    try:
        import minigen_state as state
        value = str(state.read_settings().get("agentsRegistry") or "").strip()
        if value:
            return value
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_REGISTRY


def registry_dir() -> Path:
    """Локальный клон реестра."""
    return REGISTRY_HOME / registry_slug().split("/")[-1]


def workspace_home() -> Path:
    """Где живут рабочие репозитории агентов."""
    try:
        import minigen_state as state
        raw = str(state.read_settings().get("agentsWorkspace") or "").strip()
        if raw:
            return Path(raw)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_WORKSPACE


def _git(args: list[str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
    return git(args, cwd, timeout=timeout)


def repo_exists(repo_slug: str) -> bool:
    """Есть ли такой репозиторий на GitHub."""
    res = gh(["repo", "view", repo_slug, "--json", "name"], timeout=120)
    return bool(res.get("ok"))


def ensure_registry(create: bool = True) -> dict[str, Any]:
    """Клон реестра (или его создание на GitHub) — источник правды по агентам.

    Реестр — **приватный** репозиторий по умолчанию: в нём конституции и
    скиллы агентов, а не код проектов.
    """
    slug = registry_slug()
    target = registry_dir()
    if (target / ".git").is_dir():
        return {"ok": True, "path": str(target), "repo": slug, "created": False}
    if not create:
        return {"ok": False, "error": "реестр %s не склонирован" % slug}
    target.parent.mkdir(parents=True, exist_ok=True)
    clone = _git(["clone", "https://github.com/%s.git" % slug, str(target)],
                 target.parent)
    if clone.get("ok"):
        return {"ok": True, "path": str(target), "repo": slug, "created": False}
    if repo_exists(slug):
        return {"ok": False, "error": ("репозиторий %s есть на GitHub, но клон не "
                                      "получился: %s" % (slug, clone.get("err") or ""))}
    made = gh(["repo", "create", slug, "--private",
               "--description", "Агенты «МиниГена»: конституции, скиллы, манифесты"],
              timeout=300)
    if not made.get("ok"):
        return {"ok": False, "error": "реестр не создан: %s" % (made.get("err") or "")}
    # Ветка по умолчанию — `master` (канон репозиториев владельца).
    gh(["repo", "edit", slug, "--default-branch", "master"], timeout=120)
    clone = _git(["clone", "https://github.com/%s.git" % slug, str(target)],
                 target.parent)
    if not clone.get("ok"):
        return {"ok": False, "error": "реестр не склонирован: %s" % (clone.get("err") or "")}
    # Пустой репозиторий без единого коммита — клон есть, а ветки нет:
    # без первого коммита `git push` не знает, что отправлять. И ветка
    # обязана быть `master` (канон владельца): иначе клон придёт пустым.
    head = _git(["rev-parse", "--verify", "HEAD"], target)
    if not head.get("ok"):
        _git(["symbolic-ref", "HEAD", "refs/heads/master"], target)
    readme = target / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Агенты «МиниГена»\n\n"
            "Реестр агентов: манифест (`agents/<slug>/agent.yaml`), копия\n"
            "конституции (`AGENTS.md`) и скиллов (`skills/`).\n\n"
            "Источник правды: агента можно восстановить на любой машине —\n"
            "`agents.apply` в программе «МиниГен».\n",
            encoding="utf-8")
        _git(["add", "-A"], target)
        _git(["commit", "-m", "Реестр агентов «МиниГена»: первый коммит"], target)
        _git(["push", "-u", "origin", "HEAD"], target)
    return {"ok": True, "path": str(target), "repo": slug, "created": True}


# ──────────────────────────────────────────────────────────── манифест

def agent_dir(slug: str) -> Path:
    """Каталог агента в реестре."""
    return registry_dir() / "agents" / slug


def read_manifest(slug: str) -> dict[str, Any]:
    """Манифест агента (пусто — агента нет)."""
    if yaml is None:
        return {}
    path = agent_dir(slug) / MANIFEST_NAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(slug: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Записать манифест агента в реестр (файл — в репозитории реестра)."""
    if yaml is None:
        return {"ok": False, "error": "нужен PyYAML (pip install pyyaml)"}
    path = agent_dir(slug) / MANIFEST_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": "манифест не записан: %s" % exc}
    return {"ok": True, "path": str(path)}


def list_agents() -> list[dict[str, Any]]:
    """Все агенты реестра — с их манифестами."""
    root = registry_dir() / "agents"
    out: list[dict[str, Any]] = []
    try:
        slugs = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for slug in slugs:
        manifest = read_manifest(slug)
        if manifest:
            manifest.setdefault("slug", slug)
            out.append(manifest)
    return out


# ────────────────────────────────────────────────────── рабочий репозиторий

def work_repo_path(repo_slug: str) -> Path:
    """Локальный путь рабочего репозитория агента."""
    name = repo_slug.split("/")[-1]
    return workspace_home() / name


def init_work_repo(path: Path, repo_slug: str) -> dict[str, Any]:
    """Создать локальный репозиторий агента и привязать его к GitHub.

    Ветка — **всегда `master`** (канон владельца). `git init` на части машин
    даёт `main`, а удалённый репозиторий сделан нами с веткой `master`:
    без явного `symbolic-ref` первый же клон окажется пустым, и агент
    «потеряется» молча.
    """
    init = _git(["init", str(path)], path.parent)
    if not init.get("ok"):
        return {"ok": False, "error": "репозиторий не создан: %s" % (init.get("err") or "")}
    _git(["symbolic-ref", "HEAD", "refs/heads/master"], path)
    remote = _git(["remote", "add", "origin", "https://github.com/%s.git" % repo_slug],
                  path)
    if not remote.get("ok"):
        return {"ok": False, "error": "remote не добавлен: %s" % (remote.get("err") or "")}
    return {"ok": True, "branch": "master"}


def ensure_work_repo(repo_slug: str, create: bool = True) -> dict[str, Any]:
    """Репозиторий агента: взять существующий или создать новый на GitHub.

    ⚠️ Второй раз **не создаём**: две копии одного агента — это ровно то,
    из-за чего лечащий брал аккаунты дважды (урок 22.11).
    """
    path = work_repo_path(repo_slug)
    if (path / ".git").is_dir():
        return {"ok": True, "path": str(path), "repo": repo_slug, "created": False}
    if not create:
        return {"ok": False, "error": "репозиторий %s не склонирован" % repo_slug}
    path.parent.mkdir(parents=True, exist_ok=True)
    clone = _git(["clone", "https://github.com/%s.git" % repo_slug, str(path)],
                 path.parent)
    if clone.get("ok"):
        return {"ok": True, "path": str(path), "repo": repo_slug, "created": False}
    if repo_exists(repo_slug):
        return {"ok": False, "error": ("репозиторий %s есть, но клон не получился: %s"
                                      % (repo_slug, clone.get("err") or ""))}
    made = gh(["repo", "create", repo_slug, "--private",
               "--description", "Рабочий репозиторий агента «МиниГена»"],
              timeout=300)
    if not made.get("ok"):
        return {"ok": False, "error": "репозиторий не создан: %s" % (made.get("err") or "")}
    gh(["repo", "edit", repo_slug, "--default-branch", "master"], timeout=120)
    started = init_work_repo(path, repo_slug)
    if not started.get("ok"):
        return {"ok": False, "error": started.get("error") or ""}
    return {"ok": True, "path": str(path), "repo": repo_slug, "created": True}


# ────────────────────────────────────────────────────── конституция и скиллы

def template_dir(slug: str, template: str = "") -> Path:
    """Каталог шаблона агента (если нет — общий `default`)."""
    source = TEMPLATES_HOME / (template or slug)
    if source.is_dir():
        return source
    return TEMPLATES_HOME / "default"


def copy_template(slug: str, repo_path: Path, template: str = "") -> list[str]:
    """Положить конституцию и скиллы агента в его репозиторий.

    Возвращает список скопированных файлов (пути относительно репозитория).
    """
    source = template_dir(slug, template)
    copied: list[str] = []
    if not source.is_dir():
        return copied
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        target = repo_path / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        copied.append(str(target.relative_to(repo_path)).replace("\\", "/"))
    return copied


def skills_of(repo_path: Path) -> list[str]:
    """Имена скиллов агента (каталоги с `SKILL.md` в `skills/`)."""
    root = repo_path / "skills"
    if not root.is_dir():
        return []
    out: list[str] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and (item / "SKILL.md").is_file():
            out.append(item.name)
    return out


def area_skills_dir(area_slug: str) -> Path:
    """Каталог скиллов области — рантайм видит их оттуда."""
    import minigen_state as state
    return state.AREAS_ROOT / ("p-" + area_slug) / "skills"


def install_skills(area_slug: str, repo_path: Path) -> dict[str, Any]:
    """Установить скиллы агента в его область (чтобы рантайм их видел).

    Рантайм берёт общие скиллы области из `<область>/skills/<имя>/SKILL.md`
    (и свои — из `<область>/agents/<имя>/skills/`). Копия в репозитории агента
    остаётся источником правды: восстановление агента переустановит её.
    """
    slug = (area_slug or "").strip()
    if not slug:
        return {"ok": False, "error": "не указана область"}
    names = skills_of(repo_path)
    if not names:
        return {"ok": True, "installed": [], "note": "у агента нет скиллов"}
    target_root = area_skills_dir(slug)
    installed: list[str] = []
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        for name in names:
            source = repo_path / "skills" / name
            target = target_root / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            installed.append(name)
    except OSError as exc:
        return {"ok": False, "error": "скиллы не установлены: %s" % exc,
                "installed": installed}
    return {"ok": True, "installed": installed, "dir": str(target_root)}


def commit_all(repo_path: Path, message: str, branch: str = "") -> dict[str, Any]:
    """Закоммитить и отправить изменения рабочего репозитория агента."""
    if branch:
        checkout = _git(["checkout", "-b", branch], repo_path)
        if not checkout.get("ok"):
            # ветка уже есть — берём её (заявки копятся в одну ветку)
            checkout = _git(["checkout", branch], repo_path)
            if not checkout.get("ok"):
                return {"ok": False, "error": checkout.get("err") or ""}
    _git(["add", "-A"], repo_path)
    res = _git(["commit", "-m", message], repo_path)
    if not res.get("ok"):
        err = res.get("err") or res.get("out") or ""
        if "nothing to commit" in str(err).lower():
            return {"ok": True, "committed": False, "note": "нечего коммитить"}
        return {"ok": False, "error": err}
    push = _git(["push", "-u", "origin", "HEAD"], repo_path)
    if not push.get("ok"):
        return {"ok": False, "error": push.get("err") or "", "committed": True}
    return {"ok": True, "branch": branch or "master"}


# ────────────────────────────────────────────────────── создание и применение

def create_agent(slug: str, title: str, role: str, repo_slug: str = "",
                 template: str = "", area: str = "", model: str = "") -> dict[str, Any]:
    """Создать агента: репозиторий, конституция, скиллы, манифест, область.

    ⚠️ Меняет внешний мир (GitHub, конфиг областей) — поэтому команда в
    каталоге инструментов помечена опасной и спрашивает подтверждения.
    """
    slug = (slug or "").strip().lower()
    if not slug or not all(ch.isalnum() or ch in "-_" for ch in slug):
        return {"error": "slug: только латиница, цифры, дефис и подчёркивание"}
    title = (title or slug).strip()
    role = (role or "").strip()
    repo_slug = (repo_slug or ("Valstan/%s" % slug)).strip()
    area = (area or ("p-" + slug)).strip()

    registry = ensure_registry(create=True)
    if not registry.get("ok"):
        return {"error": registry.get("error") or "реестр недоступен"}
    repo = ensure_work_repo(repo_slug, create=True)
    if not repo.get("ok"):
        return {"error": repo.get("error") or "репозиторий недоступен"}
    repo_path = Path(repo["path"])

    copied = copy_template(slug, repo_path, template)
    skills = skills_of(repo_path)
    committed = commit_all(repo_path, "Агент «%s»: конституция и скиллы" % title)

    # Копия конституции и скиллов — в реестр: он источник правды, и агента
    # можно восстановить даже если рабочий репозиторий потерян.
    registry_agent = agent_dir(slug)
    try:
        registry_agent.mkdir(parents=True, exist_ok=True)
        for rel in copied:
            target = registry_agent / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo_path / rel, target)
    except OSError as exc:
        return {"error": "копия в реестр не записана: %s" % exc}

    manifest = {
        "slug": slug,
        "title": title,
        "role": role,
        "repo": repo_slug,
        "area": area,
        "template": template or slug,
        "model": model or "",
        "constitution": "AGENTS.md" if "AGENTS.md" in copied else "",
        "skills": skills,
    }
    written = write_manifest(slug, manifest)
    if not written.get("ok"):
        return {"error": written.get("error") or "манифест не записан"}
    reg_commit = commit_all(registry_dir(),
                            "Агент «%s»: манифест" % title,
                            branch="agent/%s/manifest" % slug)
    return {
        "ok": True,
        "slug": slug,
        "title": title,
        "repo": repo_slug,
        "repo_path": str(repo_path),
        "area": area,
        "files": copied,
        "skills": skills,
        "manifest": manifest,
        "manifest_path": written.get("path"),
        "registry": registry.get("repo"),
        "committed": committed.get("ok"),
        "registry_committed": reg_commit.get("ok"),
        "error": committed.get("error") or reg_commit.get("error") or "",
    }


def apply_agent(slug: str, area: str = "") -> dict[str, Any]:
    """Восстановить агента на этой машине (по манифесту из реестра).

    Это и есть «восстановить на любом другом компьютере»: реестр на GitHub →
    манифест → репозиторий агента, конституция, скиллы и область.
    """
    registry = ensure_registry(create=False)
    if not registry.get("ok"):
        return {"error": registry.get("error") or "реестр недоступен"}
    manifest = read_manifest(slug)
    if not manifest:
        return {"error": "агент %s не заведён (нет манифеста в реестре)" % slug}
    repo_slug = str(manifest.get("repo") or ("Valstan/%s" % slug))
    repo = ensure_work_repo(repo_slug, create=False)
    if not repo.get("ok"):
        return {"error": repo.get("error") or "репозиторий агента недоступен"}
    repo_path = Path(repo["path"])

    # Конституция и скиллы берём из реестра — он источник правды (рабочий
    # репозиторий мог устареть или потеряться).
    source = agent_dir(slug)
    copied: list[str] = []
    if source.is_dir():
        for item in sorted(source.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(source)
            target = repo_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
            copied.append(str(rel).replace("\\", "/"))

    area_slug = (area or str(manifest.get("area") or ("p-" + slug))).strip()
    installed = install_skills(area_slug, repo_path)
    return {"ok": True, "slug": slug, "repo": repo_slug,
            "repo_path": str(repo_path), "area": area_slug,
            "files": copied, "skills": skills_of(repo_path),
            "skills_installed": installed.get("installed") or [],
            "manifest": manifest}


# ────────────────────────────────────────────────────────────── заявки

def request_branch(project: str) -> str:
    """Ветка заявок агента (по проекту — чтобы не мешать друг другу)."""
    from minigen_ingest import slugify
    return "%s/%s" % (REQUESTS_BRANCH, slugify(project or "общее", "общее"))


def save_request(slug: str, project: str, filename: str, text: str) -> dict[str, Any]:
    """Сохранить заявку в репозиторий агента (ветка `requests/<проект>`).

    Заявки — это то, ради чего агент и нужен: они лежат в его репозитории
    (значит, переживают переустановку и видны с другого компьютера).
    """
    manifest = read_manifest(slug)
    repo_slug = str(manifest.get("repo") or ("Valstan/%s" % slug))
    repo = ensure_work_repo(repo_slug, create=False)
    if not repo.get("ok"):
        return {"error": repo.get("error") or "репозиторий агента недоступен"}
    repo_path = Path(repo["path"])
    folder = repo_path / REQUESTS_DIR / (project or "без-проекта")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(text, encoding="utf-8")
    except OSError as exc:
        return {"error": "заявка не записана: %s" % exc}
    committed = commit_all(repo_path, "Заявка: %s" % filename,
                           branch=request_branch(project))
    return {"ok": True, "path": str(folder / filename),
            "repo": repo_slug, "committed": committed.get("ok"),
            "error": committed.get("error") or ""}


def list_requests(slug: str) -> list[dict[str, Any]]:
    """Заявки агента, уже сохранённые в его репозитории."""
    manifest = read_manifest(slug)
    repo_slug = str(manifest.get("repo") or ("Valstan/%s" % slug))
    repo = ensure_work_repo(repo_slug, create=False)
    if not repo.get("ok"):
        return []
    root = Path(repo["path"]) / REQUESTS_DIR
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.md")):
        try:
            rel = str(path.relative_to(Path(repo["path"]))).replace("\\", "/")
        except ValueError:
            continue
        out.append({"path": rel, "project": path.parent.name,
                    "size": path.stat().st_size})
    return out


# ──────────────────────────────────────────────────────────── сводка

def agents_digest() -> str:
    """Короткая сводка по агентам — для встроенной нейросети."""
    agents = list_agents()
    if not agents:
        return ("Агентов в реестре нет. Реестр: %s (локальный клон: %s)."
                % (registry_slug(), registry_dir()))
    lines = ["Агенты «МиниГена» (реестр %s):" % registry_slug()]
    for agent in agents:
        lines.append("- %s (%s) — %s · репозиторий %s · область %s"
                     % (agent.get("slug"), agent.get("title"), agent.get("role") or "",
                        agent.get("repo") or "—", agent.get("area") or "—"))
    return "\n".join(lines)


def agent_state() -> dict[str, Any]:
    """Состояние реестра — для окна и для отчётов."""
    templates = []
    if TEMPLATES_HOME.is_dir():
        templates = sorted(p.name for p in TEMPLATES_HOME.iterdir() if p.is_dir())
    return {
        "registry": registry_slug(),
        "registry_path": str(registry_dir()),
        "registry_cloned": (registry_dir() / ".git").is_dir(),
        "workspace": str(workspace_home()),
        "agents": list_agents(),
        "templates": templates,
    }


def dumps(data: Any) -> str:
    """JSON для ответа команды (как у остальных команд моста)."""
    return json.dumps(data, ensure_ascii=False, default=str)
