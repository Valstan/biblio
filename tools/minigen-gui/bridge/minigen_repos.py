"""Чужие репозитории — **только чтение**.

Заказ владельца (24.11): «чтобы все эти агенты, они имели доступ ко всем
репозиториям, чтоб, ну, они не могли их изменять, но они могли брать оттуда
информацию. И чтобы вот этот агент по обработке моих идей и созданию промтов,
чтобы он мог прочитать соответствующие репозитории, и понять в каком стиле,
в каком ключе, создать этот промт из моей идеи».

Значит два правила, и они противоположны:

- **читать можно всё** — агент обязан видеть чужие проекты, иначе заявка
  получится «из воздуха» и не попадёт в стиль проекта;
- **писать нельзя ничего** — репозиторий чужой, и право записи у него нет
  (у «МиниГена» вообще нет права писать в чужие репозитории).

Поэтому здесь **нет** ни `git commit`, ни `git push`, ни записи файлов: только
`os.walk` и чтение. Единственное, что можно испортить, — прочитать секрет,
поэтому файлы с ключами закрыты так же, как в `minigen_code`.

Путь репозитория задаётся **именем**, а не путём: `sarafan`, а не
`D:\\valstan\\REPO\\sarafan`. Иначе нейросеть напишет `..\\..\\` и уйдёт за
пределы рабочего пространства — это лазейка, которой не должно быть.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from minigen_code import SKIP_DIRS, is_secret_path

#: Рабочее пространство с репозиториями (по соглашению владельца).
DEFAULT_WORKSPACE = Path(r"D:\valstan\REPO")

#: Сколько символов отдаём за один файл.
MAX_READ = 60_000

#: Сколько файлов перечисляем за раз.
MAX_FILES = 400

#: Сколько совпадений отдаём за один поиск.
MAX_HITS = 120


def workspace_home() -> Path:
    """Где лежат репозитории (настройка программы может переопределить)."""
    try:
        import minigen_state as state
        raw = str(state.read_settings().get("agentsWorkspace") or "").strip()
        if raw:
            return Path(raw)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_WORKSPACE


def repo_path(name: str) -> Path:
    """Репозиторий по имени. Вне рабочего пространства — отказ.

    Имя — **одна** папка без разделителей: `sarafan`, `MatricaRMZ`. Всё
    остальное (`..`, `a/b`, абсолютный путь) — отказ, потому что иначе
    «чтение репозиториев» превращается в чтение всего диска.
    """
    raw = (name or "").strip().strip("\\/")
    if not raw or "/" in raw or "\\" in raw or raw in (".", ".."):
        raise PermissionError("имя репозитория: одна папка без пути")
    base = workspace_home().resolve()
    candidate = (base / raw).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise PermissionError("репозиторий вне рабочего пространства: %s" % name)
    return candidate


def _git(args: list[str], cwd: Path, timeout: int = 30) -> dict[str, Any]:
    """Мелкий git-вызов (только чтение: remote, ветка, статус)."""
    import subprocess
    try:
        proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "out": "", "err": str(exc)}
    return {"ok": proc.returncode == 0,
            "out": (proc.stdout or "").strip(),
            "err": (proc.stderr or "").strip()}


def _remote_slug(path: Path) -> str:
    """`owner/name` удалённого репозитория (пусто — репозиторий без origin)."""
    res = _git(["remote", "get-url", "origin"], path)
    if not res["ok"]:
        return ""
    text = res["out"].strip()
    if text.endswith(".git"):
        text = text[:-4]
    if "github.com" in text:
        tail = text.split("github.com")[-1].strip(":/")
        parts = [p for p in tail.split("/") if p]
        if len(parts) >= 2:
            match = "%s/%s" % (parts[0], parts[1])
    return match


def is_repo(path: Path) -> bool:
    """Репозиторий ли это (есть `.git`)."""
    return (path / ".git").exists()


def list_repos(detail: bool = True) -> list[dict[str, Any]]:
    """Все репозитории рабочего пространства — кратко.

    Агенту этого достаточно, чтобы выбрать проект: имя, где лежит, какой
    удалённый репозиторий, есть ли конституция (`AGENTS.md`).

    `detail=False` — только имена: без запуска git. Нужен там, где список
    собирается часто (подбор проекта для заявки): два git-вызова на каждый
    репозиторий — это десятки процессов.
    """
    base = workspace_home()
    out: list[dict[str, Any]] = []
    try:
        children = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return out
    for path in children:
        if not is_repo(path):
            continue
        item: dict[str, Any] = {"name": path.name, "path": str(path)}
        if detail:
            branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
            item["remote"] = _remote_slug(path)
            item["branch"] = branch["out"] if branch["ok"] else ""
            item["constitution"] = (path / "AGENTS.md").is_file()
            item["readme"] = next((n for n in ("README.md", "readme.md", "README.MD")
                                   if (path / n).is_file()), "")
        out.append(item)
    return out


def list_files(name: str, rel: str = "", limit: int = MAX_FILES) -> list[str]:
    """Файлы репозитория (без тяжёлых и служебных каталогов)."""
    root = repo_path(name)
    if not root.is_dir():
        raise FileNotFoundError("нет такого репозитория: %s" % name)
    base = (root / rel) if rel else root
    try:
        base = base.resolve()
        base.relative_to(root)
    except (OSError, ValueError):
        raise PermissionError("путь вне репозитория: %s" % rel)
    out: list[str] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            path = Path(current) / filename
            try:
                rel_path = path.relative_to(root)
            except ValueError:
                continue
            out.append(str(rel_path).replace("\\", "/"))
            if len(out) >= limit:
                return sorted(out)
    return sorted(out)


def read_file(name: str, rel: str, start: int = 0, end: int = 0) -> dict[str, Any]:
    """Прочитать файл репозитория. Секреты — отказ.

    `start`/`end` — строки (1-based, включительно) для чтения куска.
    """
    root = repo_path(name)
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise PermissionError("путь вне репозитория: %s" % rel)
    if not path.is_file():
        return {"error": "нет такого файла: %s/%s" % (name, rel)}
    if is_secret_path(path):
        return {"error": "файл закрыт (ключи/авторизация): %s/%s" % (name, rel)}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": "не читается: %s" % exc}
    lines = raw.splitlines()
    total = len(lines)
    if start or end:
        first = max(int(start or 1) - 1, 0)
        last = int(end or total)
        return {"path": rel, "lines": total, "from": first + 1, "to": last,
                "truncated": False,
                "content": "\n".join(lines[first:last])}
    return {"path": rel, "lines": total, "truncated": len(raw) > MAX_READ,
            "content": raw[:MAX_READ]}


def search(name: str, query: str, limit: int = MAX_HITS) -> list[dict[str, Any]]:
    """Поиск по репозиторию (строки с совпадением)."""
    needle = (query or "").strip().lower()
    if len(needle) < 2:
        return []
    root = repo_path(name)
    hits: list[dict[str, Any]] = []
    for rel in list_files(name):
        path = root / rel
        if is_secret_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                hits.append({"path": rel, "line": number, "text": line.strip()[:220]})
                if len(hits) >= limit:
                    return hits
    return hits


def profile(name: str) -> dict[str, Any]:
    """«Почерк» проекта — что агенту нужно, чтобы попасть в стиль.

    Это ровно то, о чём просил владелец: «прочитать соответствующие
    репозитории и понять, в каком стиле, в каком ключе создать этот промт».
    """
    root = repo_path(name)
    if not root.is_dir():
        return {"error": "нет такого репозитория: %s" % name}
    out: dict[str, Any] = {
        "name": root.name,
        "path": str(root),
        "remote": _remote_slug(root),
        "branch": "",
        "readme": "",
        "constitution": "",
        "languages": [],
        "files": 0,
    }
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch["ok"]:
        out["branch"] = branch["out"]
    readme = next((n for n in ("README.md", "readme.md", "README.MD")
                   if (root / n).is_file()), "")
    if readme:
        out["readme"] = readme
        try:
            out["readme_head"] = "\n".join(
                (root / readme).read_text(encoding="utf-8", errors="replace")
                .splitlines()[:40])
        except OSError:
            out["readme_head"] = ""
    if (root / "AGENTS.md").is_file():
        out["constitution"] = "AGENTS.md"
        try:
            out["constitution_head"] = "\n".join(
                (root / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
                .splitlines()[:40])
        except OSError:
            out["constitution_head"] = ""
    files = list_files(name)
    out["files"] = len(files)
    languages: list[str] = []
    for rel in files:
        suffix = Path(rel).suffix.lower()
        if suffix and suffix not in languages:
            languages.append(suffix)
    out["languages"] = sorted(languages)[:12]
    return out
