"""Паспорт нитки: что делалось, что осталось, где работать (промт #172).

Задача (зачитка владельца 26.11.2026): нитка не должна теряться при переносе
сессии. Новой сессии нужен не «последний финальный ответ», а **паспорт**:
что делалось, что осталось, репозиторий, ветка/worktree, открытые PR и
критерии приёмки. Иначе агент в новой сессии не знает, что осталось и в какой
ветке работать.

Где лежит паспорт:
- **на диске машины, вне репозитория** — `%LOCALAPPDATA%\\minigen-gui\\passports`
  (рядом с историей чата и ожиданием подтверждения: то же «переживает
  перезапуск окна», D-038);
- **без секретов**: текст проходит через `redact()` — токены, ключи и
  авторизация вырезаются. В репозиторий паспорт не попадает никогда.

Нити одного репозитория не путаются: в паспорте обязательны репозиторий,
ветка, worktree и явная пометка «чужие деревья не трогать» (ADR-0011 — один
пишущий агент — одно дерево; границы пересеклись — второй ждёт merge первого
и ребейзит).

Модуль **не зависит** от рантайма и работает без сети: всё, что он знает,
он читает с диска (handoff-файл, git) и получает от вызывающего.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

#: Версия паспорта — на случай будущей смены формата.
PASSPORT_VERSION = 1

#: Куда класть паспорта: диск машины, вне репозитория (D-038).
PASSPORT_DIR = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "minigen-gui" / "passports")

#: Где в репозитории искать состояние нитки (по порядку).
HANDOFF_NAMES = ("docs/SESSION_HANDOFF.md", "docs/HANDOFF.md", "HANDOFF.md")

#: Секреты, которых в паспорте быть не должно (D-038).
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|authorization|api[_-]?key|access[_-]?token|"
                r"refresh[_-]?token|password|passwd|secret)\b\s*[:=]\s*\S+"),
     r"\1=***"),
    (re.compile(r"(?i)\b(minimax-[a-z0-9]{6,}|[A-Za-z0-9_\-]{40,})\b"), "***"),
]

#: Разделы, которые мы вытаскиваем из текста отчёта и handoff-файла.
SECTION_DONE = ("что сделано", "сделано", "выполнено", "итог", "что сделал")
SECTION_TODO = ("что не сделано", "не сделано", "что осталось", "осталось",
                "что дальше", "дальше", "продолжение", "что не доделано")
SECTION_ACCEPT = ("критерии приёмки", "критерии приемки", "приёмка", "приемка",
                  "acceptance")
SECTION_FILES = ("файлы", "изменённые файлы", "измененные файлы")

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def redact(text: str) -> str:
    """Вырезать секреты (D-038): в паспорт они попадать не должны."""
    out = text or ""
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


# ────────────────────────────────────────────────────────── хранение

def passport_dir(area_slug: str = "") -> Path:
    """Папка паспортов (общая или по области)."""
    if not area_slug:
        return PASSPORT_DIR
    return PASSPORT_DIR / _safe_name(area_slug)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "area"))[:64]


def passport_path(area_slug: str, thread_key: str) -> Path:
    """Путь файла паспорта нитки."""
    return passport_dir(area_slug) / ("%s.json" % _safe_name(thread_key))


def save(passport: dict[str, Any]) -> str:
    """Записать паспорт на диск. Возвращает путь (пустая строка — не записали)."""
    data = dict(passport or {})
    data["version"] = PASSPORT_VERSION
    data["updated_at_ms"] = int(time.time() * 1000)
    data.setdefault("created_at_ms", data["updated_at_ms"])
    area = str(data.get("area") or "")
    thread = str(data.get("thread_id") or data.get("session_id") or "")
    if not thread:
        return ""
    path = passport_path(area, thread)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except OSError:
        return ""
    return str(path)


def load(area_slug: str, thread_key: str) -> dict[str, Any]:
    """Прочитать паспорт нитки с диска (пусто — паспорта нет)."""
    path = passport_path(area_slug, thread_key)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_passports(area_slug: str = "") -> list[dict[str, Any]]:
    """Все паспорта (по области или все)."""
    base = passport_dir(area_slug) if area_slug else PASSPORT_DIR
    out: list[dict[str, Any]] = []
    try:
        files = sorted(base.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["path"] = str(f)
            out.append(data)
    return out


# ────────────────────────────────────────────────────────── разбор текста

def sections(text: str) -> dict[str, list[str]]:
    """Разделы «сделано / осталось / критерии / файлы» из текста отчёта."""
    out: dict[str, list[str]] = {"done": [], "todo": [], "acceptance": [], "files": []}
    if not text:
        return out
    bucket = ""
    for line in (text or "").splitlines():
        low = line.strip().lower()
        if not low:
            continue
        head = low.lstrip("#* ").strip()
        matched = ""
        for name, keys in (("done", SECTION_DONE), ("todo", SECTION_TODO),
                           ("acceptance", SECTION_ACCEPT), ("files", SECTION_FILES)):
            for key in keys:
                if head.startswith(key) or (" " + key) in head[:60]:
                    matched = name
                    break
            if matched:
                break
        if matched:
            bucket = matched
            continue
        if not bucket:
            continue
        if _BULLET.match(line) or line.strip().startswith("["):
            item = line.strip().lstrip("-*• ").strip()
            item = re.sub(r"^\[[ xX]\]\s*", "", item)
            if item and item not in out[bucket]:
                out[bucket].append(item)
    return out


def read_handoff(folder: str | Path) -> str:
    """Состояние нитки из handoff-файла репозитория (если он есть)."""
    base = Path(folder or "")
    for name in HANDOFF_NAMES:
        p = base / name
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")[-8000:]
        except OSError:
            continue
    return ""


# ────────────────────────────────────────────────────────── git и PR

def _git(folder: str | Path, args: list[str], timeout: int = 10) -> str:
    """`git` в папке — молча и быстро (никогда не роняет сторож)."""
    base = str(folder or "")
    if not base or not Path(base).is_dir():
        return ""
    try:
        proc = subprocess.run(["git", "-C", base] + args, capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8",
                              errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def git_info(folder: str | Path) -> dict[str, Any]:
    """Репозиторий, ветка, worktree и изменённые файлы нитки."""
    out: dict[str, Any] = {"repo": "", "branch": "", "worktree": "",
                           "files": [], "upstream": "", "merged": None}
    base = str(folder or "")
    if not base or not Path(base).is_dir():
        return out
    root = _git(base, ["rev-parse", "--show-toplevel"])
    out["worktree"] = root or base
    out["repo"] = _git(base, ["rev-parse", "--path-format=absolute",
                              "--git-common-dir"]) or ""
    if out["repo"]:
        out["repo"] = str(Path(out["repo"]).parent)
    out["branch"] = _git(base, ["rev-parse", "--abbrev-ref", "HEAD"])
    out["upstream"] = _git(base, ["rev-parse", "--abbrev-ref",
                                  "--symbolic-full-name", "@{upstream}"])
    status = _git(base, ["status", "--porcelain"])
    files: list[str] = []
    for line in status.splitlines():
        name = line[3:].strip() if len(line) > 3 else ""
        if " -> " in name:
            name = name.split(" -> ")[-1].strip()
        if name and name not in files:
            files.append(name)
    out["files"] = files[:60]
    if out["branch"] and out["branch"] != "HEAD":
        merged = _git(base, ["branch", "--merged", "origin/master"])
        out["merged"] = out["branch"] in [b.strip() for b in merged.splitlines()]
    return out


def open_prs(folder: str | Path, branch: str) -> list[str]:
    """Открытые PR по ветке (если `gh` есть и вход есть)."""
    if not branch or branch == "HEAD":
        return []
    base = str(folder or "")
    if not base or not Path(base).is_dir():
        return []
    try:
        proc = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open",
                               "--json", "number,title,url", "--limit", "10"],
                              capture_output=True, text=True, timeout=20,
                              encoding="utf-8", errors="replace", cwd=base)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        title = str(item.get("title") or "")
        if url:
            out.append("%s — %s" % (url, title) if title else url)
    return out


# ────────────────────────────────────────────────────────── сборка паспорта

def build(area: dict[str, Any], sess: dict[str, Any],
          facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Собрать паспорт нитки по сессии.

    Источники: отчёт о выполнении (если есть) → handoff-файл репозитория →
    git (репозиторий/ветка/worktree/файлы) → открытые PR.
    """
    facts = facts or {}
    area = area or {}
    sess = sess or {}
    folder = str(sess.get("workspace_dir") or area.get("folder") or "")
    git = git_info(folder)
    report = str(facts.get("report_excerpt") or "")
    handoff = read_handoff(folder)
    from_report = sections(report)
    from_handoff = sections(handoff)

    def pick(first: list[str], second: list[str]) -> list[str]:
        out = list(first)
        for item in second:
            if item not in out:
                out.append(item)
        return out[:12]

    done = pick(from_report["done"], from_handoff["done"])
    todo = pick(from_report["todo"], from_handoff["todo"])
    acceptance = pick(from_handoff["acceptance"], from_report["acceptance"])
    files = list(git.get("files") or [])
    for item in from_handoff["files"]:
        if item not in files and ("/" in item or "." in item):
            files.append(item)
    files = files[:40]

    branch = git.get("branch") or ""
    merged = git.get("merged")
    if merged is True:
        branch_state = "ветка слита в origin/master — новую ветку бери от свежего origin/master"
    elif merged is False:
        branch_state = "ветка рабочая — продолжай в ней"
    else:
        branch_state = "состояние ветки неизвестно — проверь сам"

    passport = {
        "version": PASSPORT_VERSION,
        "area": str(area.get("slug") or facts.get("area") or ""),
        "thread_id": str(sess.get("session_id") or facts.get("session_id") or ""),
        "session_id": str(sess.get("session_id") or facts.get("session_id") or ""),
        "title": str(sess.get("title") or facts.get("title") or "")[:120],
        "repo": git.get("repo") or folder,
        "branch": branch,
        "worktree": git.get("worktree") or folder,
        "branch_state": branch_state,
        "done": done,
        "todo": todo,
        "acceptance": acceptance,
        "files": files,
        "prs": open_prs(folder, branch),
        "report": redact(report)[:4000],
        "parallel": facts.get("parallel_sessions") or [],
        "foreign_note": ("Чужие деревья и ветки НЕ трогать: в одном репозитории "
                          "работают параллельные нитки (ADR-0011 — один пишущий "
                          "агент — одно дерево; границы пересеклись — жди merge "
                          "первого и ребейзи свою ветку)."),
        "created_at_ms": int(time.time() * 1000),
        "updated_at_ms": int(time.time() * 1000),
    }
    return passport


def next_step(passport: dict[str, Any]) -> str:
    """Что делать дальше — одной строкой (для инструкции агенту)."""
    todo = [str(i) for i in (passport.get("todo") or []) if str(i).strip()]
    if todo:
        return todo[0]
    accept = [str(i) for i in (passport.get("acceptance") or []) if str(i).strip()]
    if accept:
        return "доведи работу до критериев приёмки: %s" % accept[0]
    return "продолжи нитку с места, где она остановилась"


def as_brief(passport: dict[str, Any]) -> str:
    """Паспорт — текстом начальной команды для новой сессии."""
    passport = passport or {}
    lines = ["Продолжи нитку разработки в новой сессии.", ""]
    repo = passport.get("repo") or ""
    branch = passport.get("branch") or ""
    worktree = passport.get("worktree") or ""
    if repo or branch or worktree:
        lines += ["Где работать (своя нитка — чужие деревья не трогать):"]
        if repo:
            lines.append("- репозиторий: %s" % repo)
        if branch:
            lines.append("- ветка: %s" % branch)
        if worktree:
            lines.append("- worktree: %s" % worktree)
        lines.append("")
    if passport.get("branch_state"):
        lines += [str(passport["branch_state"]), ""]
    done = [str(i) for i in (passport.get("done") or [])]
    todo = [str(i) for i in (passport.get("todo") or [])]
    if done:
        lines += ["Что уже сделано:"]
        lines += ["- %s" % i for i in done]
        lines.append("")
    if todo:
        lines += ["Что осталось:"]
        lines += ["- %s" % i for i in todo]
        lines.append("")
    accept = [str(i) for i in (passport.get("acceptance") or [])]
    if accept:
        lines += ["Критерии приёмки:"] + ["- %s" % i for i in accept] + [""]
    files = [str(i) for i in (passport.get("files") or [])]
    if files:
        lines += ["Файлы нитки: %s" % ", ".join(files[:20]), ""]
    prs = [str(i) for i in (passport.get("prs") or [])]
    if prs:
        lines += ["Открытые PR: %s" % "; ".join(prs), ""]
    if passport.get("report"):
        lines += ["Отчёт о выполнении прежней сессии:", str(passport["report"]), ""]
    if passport.get("foreign_note"):
        lines += [str(passport["foreign_note"]), ""]
    lines += ["Что делать дальше: %s" % next_step(passport), ""]
    lines += ["Сначала прочитай состояние и скажи, что делаешь дальше. "
              "Не начинай новую тему — продолжай ту же нитку."]
    return redact("\n".join(lines))
