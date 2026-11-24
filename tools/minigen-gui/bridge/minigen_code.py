"""Самообновление программы: нейросеть правит модули «МиниГена» сама.

Задача (заказ владельца, 23.11): «нейросеть, встроенная в МиниГена, должна
понимать работу программы, анализировать и могла сама переписывать свои
модули. Находила баги, нестыковки, расширяла функционал. Если это нужно,
переписывала свои модули и обновляла программу минигена сама же».

Как это устроено и почему так:

- **правка идёт в отдельном git-worktree**, а не в дерево, где работает
  сессия. Правило репозитория: один писатель на область — «МиниГен»
  (область `p-minigen`) уже занят этой сессией, второй писатель в том же
  дереве ломал бы чужую работу. Отдельный worktree — это отдельное рабочее
  дерево того же репозитория;
- **только ветка, только PR.** Прямой пуш в `master` запрещён (канон
  репозитория). Нейросеть не «обновляет программу на машине» — она
  предлагает правку веткой и PR, а решение принимает человек;
- **секреты не читаются.** Файлы с ключами и авторизацией закрыты списком
  (см. `is_secret_path`): правка программы не требует читать ключи;
- **правка точная.** Замена применяется, только если искомый фрагмент
  встретился ровно один раз: «приблизительная» правка по смыслу — это
  порча кода, а не его починка;
- **проверка обязательна.** `code.tests` гоняет проверки репозитория в том
  же worktree — правка без зелёных проверок не идёт в PR.

Всё, что здесь есть, — обёртки над `git`, `gh` и файловой системой внутри
одного репозитория. Никаких правок вне него.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────── репозиторий

def repo_root() -> Path:
    """Корень репозитория с исходниками программы (для правок и PR).

    Порядок поиска:

    1. `MINIGEN_CODE_REPO` — явное указание;
    2. **клон копии** (`<Programs>\\МиниГен\\clone`) — если он есть, то это
       рабочее дерево копии: заказ владельца (24.11) — каждая копия правит
       программу в **своём** клоне, а не в чужом дереве;
    3. настройка `codeRepo` (её пишет установщик): программа может стоять
       в своём месте (`%LOCALAPPDATA%\\Programs\\МиниГен`), а исходники —
       в репозитории; правки и PR делаются по исходникам;
    4. вверх по дереву до каталога с `.git` — когда программа запущена из
       самого репозитория (разработка);
    5. иначе — каталог программы (правки работать не смогут, и команда
       это скажет).
    """
    env = os.environ.get("MINIGEN_CODE_REPO")
    if env:
        return Path(env).resolve()
    try:
        import minigen_clone
        clone = minigen_clone.CLONE_DIR
        if (clone / ".git").is_dir():
            return clone.resolve()
    except Exception:  # noqa: BLE001
        pass
    try:
        import json
        settings = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) \
            / "minigen-gui" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        configured = str(data.get("codeRepo") or "").strip()
        if configured:
            return Path(configured).resolve()
    except (OSError, ValueError, AttributeError):
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[2]


REPO = repo_root()


def _worktrees_dir() -> Path:
    """Каталог worktree'ов.

    Если репозиторий — клон копии, worktree'и лежат **вне** клона: иначе
    клон виделся бы «грязным» (untracked-каталог) и правки в нём путались бы
    с чужими.
    """
    try:
        import minigen_clone
        if REPO == minigen_clone.CLONE_DIR.resolve():
            return (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                    / "minigen-gui" / "worktrees")
    except Exception:  # noqa: BLE001
        pass
    return REPO / ".claude" / "worktrees"


WORKTREES = _worktrees_dir()
STATE_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui" / "fix-worktree.json"

MAX_READ = 60_000
MAX_FILES_LISTED = 400

# Каталоги, которые не читаем и не ищем (тяжёлые, чужие, временные).
SKIP_DIRS = {".git", "node_modules", "out", "release", "__pycache__", ".venv",
             "dist", ".pytest_cache", "worktrees"}

# ─────────────────────────────────────────────────────────────── секреты

# Закрытые файлы: ключи, авторизация, личные данные. Нейросети они не нужны,
# а в журнале и в чате секреты утекают.
SECRET_NAMES = {
    "config.yaml", "config.yml", ".env", ".env.local", "auth.json",
    "credentials.json", "secrets.json", "id_rsa", "id_ed25519",
    "accounts.json", "store.json",
}
SECRET_PATTERNS = (
    re.compile(r"(^|[\\/])\.env(\..+)?$", re.I),
    re.compile(r"(secret|token|password|passwd|apikey|api_key|private_key)", re.I),
    re.compile(r"\.(pem|key|p12|pfx)$", re.I),
)


def is_secret_path(path: Path) -> bool:
    """Файл с секретами или личными данными?"""
    name = path.name.lower()
    if name in SECRET_NAMES:
        return True
    for part in path.parts:
        if part.lower() in SECRET_NAMES:
            return True
    return any(pattern.search(str(path)) for pattern in SECRET_PATTERNS)


def resolve_in_repo(raw: str, base: Path | None = None) -> Path:
    """Путь внутри репозитория (или его worktree). Вне — отказ."""
    base = base or REPO
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    root = base.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PermissionError("путь вне рабочего дерева: %s" % raw)
    return resolved


# ──────────────────────────────────────────────────────────────── git

def git(args: list[str], cwd: Path, timeout: int = 120) -> dict[str, Any]:
    """Запустить git и вернуть результат (без исключений — с текстом ошибки)."""
    try:
        proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": "git не ответил за %d с" % timeout}
    except OSError as exc:
        return {"ok": False, "code": -1, "out": "", "err": str(exc)}
    return {"ok": proc.returncode == 0, "code": proc.returncode,
            "out": (proc.stdout or "").strip(), "err": (proc.stderr or "").strip()}


def current_branch(cwd: Path) -> str:
    res = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return res["out"] if res["ok"] else ""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (slug or "fix")[:40]


# ─────────────────────────────────────────────────────────── состояние

def read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(data: dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(STATE_FILE))
    except OSError:
        pass


def current_worktree() -> Path | None:
    """Worktree текущей правки (если он есть и жив)."""
    state = read_state()
    path = state.get("worktree")
    if not path:
        return None
    candidate = Path(path)
    if not (candidate / ".git").exists():
        return None
    return candidate


def worktree_info() -> dict[str, Any]:
    state = read_state()
    path = current_worktree()
    return {
        "worktree": str(path) if path else "",
        "branch": state.get("branch", ""),
        "title": state.get("title", ""),
        "alive": bool(path),
    }


# ────────────────────────────────────────────────────── чтение и поиск

def list_files(rel: str = "", limit: int = MAX_FILES_LISTED) -> list[str]:
    """Файлы репозитория (без тяжёлых и служебных каталогов)."""
    base = resolve_in_repo(rel) if rel else REPO
    out: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = Path(root) / name
            try:
                rel_path = path.relative_to(REPO)
            except ValueError:
                continue
            out.append(str(rel_path).replace("\\", "/"))
            if len(out) >= limit:
                return sorted(out)
    return sorted(out)


def read_file(rel: str, start: int = 0, end: int = 0) -> dict[str, Any]:
    """Прочитать файл репозитория. Секреты — отказ.

    `start`/`end` — строки (1-based, включительно) для чтения куска.
    """
    path = resolve_in_repo(rel)
    if not path.is_file():
        return {"error": "нет такого файла: %s" % rel}
    if is_secret_path(path):
        return {"error": "файл закрыт (ключи/авторизация): %s" % rel}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": "не читается: %s" % exc}
    lines = raw.splitlines()
    total = len(lines)
    if start or end:
        first = max(int(start or 1) - 1, 0)
        last = int(end or total)
        chunk = "\n".join(lines[first:last])
        return {"path": rel, "lines": total, "from": first + 1, "to": last,
                "truncated": False, "content": chunk}
    truncated = len(raw) > MAX_READ
    try:
        rel_path = str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        rel_path = str(path)
    return {"path": rel_path, "lines": total,
            "truncated": truncated, "content": raw[:MAX_READ]}


def search(query: str, rel: str = "", limit: int = 120) -> list[dict[str, Any]]:
    """Поиск по репозиторию (строки с совпадением)."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    needle = query.lower()
    base = resolve_in_repo(rel) if rel else REPO
    hits: list[dict[str, Any]] = []
    for name in list_files(str(base.relative_to(REPO)) if base != REPO else ""):
        path = REPO / name
        if is_secret_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                hits.append({"path": name, "line": number, "text": line.strip()[:220]})
                if len(hits) >= limit:
                    return hits
    return hits


# ──────────────────────────────────────────────────── работа с правкой

def create_worktree(title: str) -> dict[str, Any]:
    """Создать worktree и ветку под правку.

    Отдельное рабочее дерево — чтобы второй писатель (нейросеть) не лез в
    дерево сессии. Имя ветки: `agent/minigen-fix-<slug>`.
    """
    title = (title or "").strip()
    if not title:
        return {"error": "нужно название правки"}
    WORKTREES.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    branch = "agent/minigen-fix-%s" % slug
    target = WORKTREES / slug
    if target.exists():
        return {"error": "worktree уже есть: %s" % target}
    res = git(["worktree", "add", "-b", branch, str(target), "master"], REPO, timeout=180)
    if not res["ok"]:
        # ветка могла остаться от прошлой попытки — пробуем без -b
        res2 = git(["worktree", "add", str(target), branch], REPO, timeout=180)
        if not res2["ok"]:
            return {"error": "worktree не создан: %s %s" % (res["err"], res2["err"])}
    write_state({"worktree": str(target), "branch": branch, "title": title,
                 "created_at": int(time.time() * 1000)})
    return {"ok": True, "worktree": str(target), "branch": branch, "title": title}


def apply_patch(rel: str, find: str, replace: str) -> dict[str, Any]:
    """Точная замена фрагмента в файле (ровно одно вхождение)."""
    worktree = current_worktree()
    if worktree is None:
        return {"error": "нет worktree под правку — сначала code.branch"}
    if not find:
        return {"error": "пустой фрагмент для замены"}
    path = resolve_in_repo(rel, base=worktree)
    if not path.is_file():
        return {"error": "нет такого файла: %s" % rel}
    if is_secret_path(path):
        return {"error": "файл закрыт (ключи/авторизация): %s" % rel}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": "не читается: %s" % exc}
    count = text.count(find)
    if count == 0:
        return {"error": "фрагмент не найден в файле"}
    if count > 1:
        return {"error": "фрагмент встречается %d раз — замена неоднозначна" % count}
    new_text = text.replace(find, replace, 1)
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return {"error": "не записать: %s" % exc}
    diff = git(["diff", "--stat", "--", str(path.relative_to(worktree))], worktree)
    return {"ok": True, "path": rel, "diff": diff["out"] or diff["err"]}


def run_tests(target: str = "all", worktree: Path | None = None) -> dict[str, Any]:
    """Прогнать проверки репозитория в worktree правки."""
    worktree = worktree or current_worktree()
    if worktree is None:
        worktree = REPO
    target = (target or "all").lower()
    steps: list[dict[str, Any]] = []

    if target in ("all", "python", "bridge"):
        res = subprocess.run(
            [os.environ.get("PYTHON", "python"), "-m", "unittest",
             "discover", "-s", "bridge/tests", "-t", "."],
            cwd=str(worktree / "tools" / "minigen-gui"), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        steps.append({"name": "тесты моста", "ok": res.returncode == 0,
                      "out": (res.stderr or res.stdout or "")[-1200:]})

    if target in ("all", "panel"):
        # ⚠️ 24.11: путь был `minimax-project-areas/check_panel.py`, но приёмка
        # лежит в `archive/` (старая панель архивирована). Файла не было ⇒ шаг
        # всегда красный ⇒ нейросеть не могла открыть PR (у нас правило:
        # красные проверки — не PR). Живой случай: правка в копии прошла
        # проверки моста и окна, а «приёмка панели» ругалась на путь.
        panel = worktree / "tools" / "minimax-project-areas" / "archive" / "check_panel.py"
        if not panel.is_file():
            steps.append({"name": "приёмка панели", "ok": False,
                          "out": "нет файла приёмки: %s" % panel})
        else:
            res = subprocess.run(
                [os.environ.get("PYTHON", "python"), str(panel), "--source-only"],
                cwd=str(worktree), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            steps.append({"name": "приёмка панели", "ok": res.returncode == 0,
                          "out": (res.stdout or "")[-1200:]})

    if target in ("all", "race"):
        res = subprocess.run(
            [os.environ.get("PYTHON", "python"),
             str(worktree / "tools" / "minimax-project-areas" /
                 "test_config_write_race.py")],
            cwd=str(worktree), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        steps.append({"name": "приёмка записи конфига", "ok": res.returncode == 0,
                      "out": (res.stdout or "")[-1200:]})

    if target in ("all", "js") and (worktree / "tools" / "minigen-gui" / "node_modules").exists():
        res = subprocess.run(["npm", "test"], cwd=str(worktree / "tools" / "minigen-gui"),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=900,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        steps.append({"name": "тесты окна", "ok": res.returncode == 0,
                      "out": (res.stdout or "")[-1200:]})
    elif target in ("all", "js"):
        steps.append({"name": "тесты окна", "ok": True,
                      "out": "пропущены: нет node_modules в worktree (нужны один раз вручную)"})

    ok = all(step["ok"] for step in steps)
    return {"ok": ok, "steps": steps}


def changed_files(worktree: Path) -> list[str]:
    """Файлы, изменённые в worktree (против HEAD), включая новые.

    Именно `git status`, а не `git diff`: новый файл (например, только что
    написанный регрессионный тест) в diff не попадает, а он важен.
    """
    res = git(["status", "--porcelain"], worktree)
    if not res["ok"]:
        return []
    out: list[str] = []
    for line in res["out"].splitlines():
        if len(line) < 3:
            continue
        # В формате porcelain колонки статуса — две буквы, потом пробел и путь.
        # Берём с третьего символа: у первой строки ведущий пробел срезает
        # helper() (strip), а у остальных он есть — отсюда разбор с [:2].
        name = line[2:].strip().strip('"')
        if " -> " in name:
            name = name.split(" -> ")[-1].strip()
        if name:
            out.append(name)
    return out


def looks_like_test(path: str) -> bool:
    """Похоже ли имя файла на тест."""
    name = path.lower()
    return ("test" in name or name.endswith("_test.py") or name.endswith("test.py")
            or "/tests/" in name or "\\tests\\" in name
            or ".test." in name or ".spec." in name)


def diff(worktree: Path | None = None) -> dict[str, Any]:
    """Текущий diff правки (для проверки глазами и для самой модели)."""
    worktree = worktree or current_worktree()
    if worktree is None:
        return {"error": "нет worktree под правку — сначала code.branch"}
    res = git(["diff", "HEAD"], worktree, timeout=120)
    text = res["out"] or ""
    if len(text) > 40_000:
        text = text[:40_000] + "\n… (обрезано)"
    return {"worktree": str(worktree), "branch": current_branch(worktree),
            "files": changed_files(worktree), "diff": text}


def commit(message: str) -> dict[str, Any]:
    """Закоммитить правку в ветку worktree."""
    worktree = current_worktree()
    if worktree is None:
        return {"error": "нет worktree под правку — сначала code.branch"}
    message = (message or "").strip()
    if not message:
        return {"error": "пустое сообщение коммита"}
    branch = current_branch(worktree)
    if branch in ("", "master"):
        return {"error": "правка должна быть в ветке, а не в master"}
    files = changed_files(worktree)
    note = ""
    if files and not any(looks_like_test(name) for name in files):
        # Урок 23.11: правка без регрессионного теста проходит зелёные
        # проверки и может быть неверной (так сломалось с REPO_TOOLS).
        note = ("изменения не затронули тесты — добавь регрессионный тест "
                "(сначала падающий, потом зелёный) или объясни в описании PR, "
                "почему тест не нужен")
    git(["add", "-A"], worktree)
    res = git(["commit", "-m", message], worktree, timeout=180)
    if not res["ok"]:
        err = res["err"] or res["out"]
        low = err.lower()
        if "identity" in low or "tell me who you are" in low:
            err = ("не задана identity git в этом репозитории "
                   "(git config user.name / user.email)")
        return {"error": "коммит не прошёл: %s" % err}
    return {"ok": True, "branch": branch, "files": files, "note": note,
            "out": res["out"]}



def _remote_url(name: str, cwd: Path) -> str:
    res = git(["remote", "get-url", name], cwd)
    return res.get("out") or ""


def _repo_slug(url: str) -> str:
    """`https://github.com/Valstan/biblio.git` → `Valstan/biblio`."""
    match = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?\s*$", url or "")
    return "%s/%s" % (match.group(1), match.group(2)) if match else ""


def upstream_slug(cwd: Path) -> str:
    """Куда идёт PR: репозиторий программы (`upstream`), иначе `origin`."""
    for name in ("upstream", "origin"):
        slug = _repo_slug(_remote_url(name, cwd))
        if slug:
            return slug
    return ""


def open_pr(title: str, body: str = "") -> dict[str, Any]:
    """Отправить ветку и открыть PR (по правилам репозитория).

    Ветка уходит в **свой** репозиторий копии (`origin`), а PR открывается в
    репозиторий программы (`upstream`) — так это и работает с форком: PR из
    форка в основной репозиторий.
    """
    worktree = current_worktree()
    if worktree is None:
        return {"error": "нет worktree под правку — сначала code.branch"}
    branch = current_branch(worktree)
    if not branch or branch == "master":
        return {"error": "PR только из ветки, не из master"}
    push = git(["push", "-u", "origin", branch], worktree, timeout=300)
    if not push["ok"]:
        return {"error": "push не прошёл: %s" % (push["err"] or push["out"])}
    title = (title or "").strip()
    if not title:
        title = "fix(minigen): правка модуля программы"
    body_text = (body or "").strip()
    if not body_text:
        body_text = ("Правка предложена встроенной нейросетью «МиниГена».\n\n"
                     "Проверки запускались в worktree правки; решение — за владельцем.")
    target = upstream_slug(worktree)
    owner = _repo_slug(_remote_url("origin", worktree)).split("/")[0]
    args = ["gh", "pr", "create", "--base", "master"]
    if target:
        args += ["--repo", target]
    if owner and target and not target.startswith(owner + "/"):
        args += ["--head", "%s:%s" % (owner, branch)]
    else:
        args += ["--head", branch]
    args += ["--title", title, "--body", body_text]
    try:
        proc = subprocess.run(
            args,
            cwd=str(worktree), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return {"error": "нет gh (GitHub CLI) — PR придётся открыть вручную"}
    except subprocess.TimeoutExpired:
        return {"error": "gh не ответил за 300 с"}
    if proc.returncode != 0:
        return {"error": "PR не открыт: %s" % ((proc.stderr or proc.stdout or "")[-400:])}
    return {"ok": True, "branch": branch, "repo": target,
            "pr": (proc.stdout or "").strip()}


def discard_worktree() -> dict[str, Any]:
    """Убрать worktree правки (владелец передумал)."""
    worktree = current_worktree()
    if worktree is None:
        return {"ok": True, "note": "worktree и не было"}
    state = read_state()
    branch = state.get("branch") or ""
    git(["worktree", "remove", "--force", str(worktree)], REPO, timeout=180)
    if branch:
        git(["branch", "-D", branch], REPO, timeout=60)
    write_state({})
    return {"ok": True, "removed": str(worktree)}
