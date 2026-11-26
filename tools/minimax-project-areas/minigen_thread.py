"""Паспорт нитки и решение о лечении сессии — сторона демона (промт #172).

Заказ владельца (26.11, промт #172, раздел `MiniGen`): «лечение сессий: решение
принимает "Управляющий", нитку переносим, а не теряем».

Три вещи, о которых этот модуль:

1. **Паспорт нитки** — что делалось, что осталось, репозиторий, ветка/worktree,
   открытые PR, критерии приёмки. Лежит **на диске машины, вне репозитория**
   (`%LOCALAPPDATA%\\minigen-gui\\thread-passports.json`, D-038) — потому что
   переживает перезапуск окна, как история чата и ожидание подтверждения.
   В репозиторий паспорт не попадает никогда: там могут быть пути, имена
   хостов и чужие изменения.
2. **Решение лечения** — спрашиваем **встроенную нейросеть программы**
   («Управляющего», тот же агент, что отвечает в чате) через локальный API
   программы (`127.0.0.1:8097`, `/treat-decision`): на вход — **факты**, на
   выход — ровно одно из трёх действий (`продолжать` / `завершать` /
   `новая_сессия`), инструкция обслуживаемому агенту и причина.
3. **Правило-фолбэк** — если модель недоступна (нет очков, вендор лежит,
   программа не запущена) или ответила не по схеме, решение принимает
   **предсказуемое правило**, и оно **не требует завершать сессию без отчёта**:
   продолжить работу либо отложить до следующего прохода.

Границы:
- секретов в паспорте быть не должно (D-038) — сюда кладём только состояние
  работы: ветки, файлы, PR, критерии;
- чужие деревья не трогаем (ADR-0011): паспорт различает нитки по
  репозиторию/ветке/worktree и говорит об этом прямо.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

#: Стор программы на машине (настройки, история чата, паспорта ниток).
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
#: Файл паспортов ниток (вне репозитория — D-038).
PASSPORT_FILE = Path(os.environ.get("MINIGEN_PASSPORTS_FILE")
                     or (LOCAL / "thread-passports.json"))
#: Токен локального API программы (тот же, что у сетевого сторожа).
BRIDGE_TOKEN_FILE = Path(os.environ.get("MINIGEN_BRIDGE_TOKEN_FILE")
                          or (LOCAL / "bridge-token.txt"))
#: Адрес локального API программы.
API_BASE = os.environ.get("MINIGEN_API_BASE", "http://127.0.0.1:8097")

#: Три действия решения — те же слова, что и у модели (см. `minigen_treatment.py`).
CONTINUE = "продолжать"
FINISH = "завершать"
NEW_SESSION = "новая_сессия"
ACTIONS = (CONTINUE, FINISH, NEW_SESSION)

#: Пометка в паспорте: чужие деревья не трогать (ADR-0011).
FOREIGN_TREES_RULE = (
    "ЧУЖИЕ ДЕРЕВЬЯ НЕ ТРОГАТЬ: работа идёт только в своей ветке/worktree. "
    "В том же репозитории работают другие нитки — их файлы, ветки и изменения "
    "не трогать. Если границы пересеклись: дождаться merge первой нитки и "
    "ребейзить свою ветку (ADR-0011: один пишущий агент — одно дерево)."
)

#: Правила, которые видит модель в фактах (коротко, своими словами).
RULES = (
    "ADR-0011: один пишущий агент — одно дерево; чужие ветки не трогать, при "
    "пересечении — ждать merge и ребейзить.",
    "D-249: работу реально законченную продолжать не надо принудительно.",
    "Промт #172: отсутствие отчёта о выполнении — не повод требовать завершения.",
    "D-038: секреты в паспорт нитки не попадают.",
)


# ────────────────────────────────────────────────────────── файл паспортов

def _now() -> int:
    return int(time.time() * 1000)


def passport_path() -> Path:
    """Где лежат паспорта ниток (путь наружу можно: секретов в файле нет)."""
    return PASSPORT_FILE


def load_passports() -> dict[str, Any]:
    """Все паспорта с диска (пусто — файла нет или он битый)."""
    try:
        data = json.loads(PASSPORT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    threads = data.get("threads")
    return threads if isinstance(threads, dict) else {}


def save_passports(threads: dict[str, Any]) -> bool:
    """Записать паспорта (атомарно)."""
    payload = {"version": 1, "updated_at": _now(), "threads": threads or {}}
    tmp = PASSPORT_FILE.with_suffix(".tmp")
    try:
        PASSPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                  default=str), encoding="utf-8")
        os.replace(str(tmp), str(PASSPORT_FILE))
        return True
    except OSError:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        return False


def save_passport(passport: dict[str, Any]) -> bool:
    """Сохранить паспорт нитки (по `thread_id`)."""
    if not isinstance(passport, dict):
        return False
    thread_id = str(passport.get("thread_id") or "")
    if not thread_id:
        return False
    threads = load_passports()
    old = threads.get(thread_id) or {}
    merged = dict(old)
    merged.update(passport)
    merged["updated_at"] = _now()
    threads[thread_id] = merged
    return save_passports(threads)


def load_passport(thread_id: str) -> dict[str, Any]:
    """Паспорт нитки с диска (пусто — нет нити)."""
    return load_passports().get(str(thread_id or "")) or {}


def thread_id_of(repo: str, branch: str) -> str:
    """Идентификатор нитки: репозиторий + ветка (стабилен между сессиями)."""
    name = Path(str(repo or "")).name or str(repo or "")
    return "%s::%s" % (name, str(branch or ""))


# ─────────────────────────────────────────────────────────────── факты git

def _git(folder: str, *args: str, timeout: int = 20) -> str:
    """`git` в рабочей папке (пустая строка — git не ответил)."""
    if not folder:
        return ""
    try:
        proc = subprocess.run(["git", "-C", str(folder)] + list(args),
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def repo_key(folder: str) -> str:
    """Ключ репозитория: общий для всех worktree одного репозитория.

    По нему нитки различаются: в одном репозитории работают несколько ниток
    (в Матрице РМЗ — три полосы в трёх worktree), и каждая продолжает **свою**
    ветку (ADR-0011).
    """
    if not folder or not Path(folder).is_dir():
        return str(folder or "")
    raw = _git(folder, "rev-parse", "--git-common-dir")
    if not raw:
        try:
            return str(Path(folder).resolve()).lower()
        except OSError:
            return str(folder)
    path = Path(raw)
    if not path.is_absolute():
        path = Path(folder) / path
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path)


def worktree_list(folder: str) -> list[dict[str, str]]:
    """Все worktree репозитория (`git worktree list --porcelain`)."""
    out: list[dict[str, str]] = []
    raw = _git(folder, "worktree", "list", "--porcelain")
    current: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.rstrip()
        if not line:
            if current.get("path"):
                out.append(current)
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):].strip()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip().replace("refs/heads/", "")
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
    if current.get("path"):
        out.append(current)
    return out


def git_state(folder: str) -> dict[str, Any]:
    """Состояние нитки по git: репозиторий, ветка, worktree, файлы, слияние."""
    out: dict[str, Any] = {"repo": "", "branch": "", "worktree": "",
                           "files": [], "commits": [], "prs": [],
                           "merged": False, "branch_exists": False,
                           "dirty": [], "ahead": 0, "behind": 0, "worktrees": []}
    if not folder or not Path(folder).is_dir():
        return out
    root = _git(folder, "rev-parse", "--show-toplevel")
    branch = _git(folder, "rev-parse", "--abbrev-ref", "HEAD")
    # ⚠️ Репозиторий и worktree — **разные** вещи: у связанного worktree свой
    # корень, а репозиторий (общий `.git`) — один. Нити различаются по
    # репозиторию + ветке, а работать надо в своём worktree (ADR-0011).
    common = _git(folder, "rev-parse", "--git-common-dir")
    repo_root = root or str(folder)
    if common:
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = Path(folder) / common_path
        try:
            common_path = common_path.resolve()
        except OSError:
            pass
        repo_root = str(common_path.parent if common_path.name == ".git"
                        else common_path)
    out["repo"] = repo_root
    out["branch"] = branch
    out["worktree"] = root or str(folder)
    out["worktrees"] = worktree_list(folder)
    # Файлы нитки: что изменено в ветке относительно origin/master + что не
    # закоммичено. Это и есть «границы» нитки (ADR-0011).
    changed = _git(folder, "diff", "--name-only", "origin/master...HEAD").splitlines()
    dirty = _git(folder, "status", "--porcelain").splitlines()
    out["dirty"] = [line[3:].strip() for line in dirty if len(line) > 3][:80]
    files = [line.strip() for line in changed if line.strip()]
    out["files"] = (files + out["dirty"])[:120]
    out["commits"] = _git(folder, "log", "--oneline", "-20",
                          "origin/master..HEAD").splitlines()[:20]
    # Ветка слита в origin/master? (тогда продолжать её нельзя — паспорт это
    # должен сказать, и агент начнёт новую ветку от свежего origin.)
    merged = _git(folder, "merge-base", "--is-ancestor", "HEAD", "origin/master")
    out["merged"] = bool(merged) and _git(folder, "rev-parse", "HEAD") != ""
    exists = _git(folder, "rev-parse", "--verify", "--quiet",
                  "refs/heads/%s" % branch)
    out["branch_exists"] = bool(exists)
    counts = _git(folder, "rev-list", "--left-right", "--count", "origin/master...HEAD")
    parts = counts.split()
    if len(parts) == 2:
        try:
            out["behind"] = int(parts[0])
            out["ahead"] = int(parts[1])
        except ValueError:
            pass
    return out


def open_prs(folder: str, branch: str) -> list[dict[str, Any]]:
    """Открытые PR нитки (`gh`, если он есть и работает)."""
    if not folder or not branch:
        return []
    env = dict(os.environ)
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--head", str(branch), "--state", "open",
             "--json", "number,title,url,headRefName,isDraft"],
            capture_output=True, text=True, timeout=25, env=env,
            stdin=subprocess.DEVNULL, cwd=str(folder))
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        items = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [{"number": item.get("number"), "title": item.get("title"),
             "url": item.get("url"), "branch": item.get("headRefName"),
             "draft": bool(item.get("isDraft"))}
            for item in items if isinstance(item, dict)][:10]


# ─────────────────────────────────────────────────────── сборка паспорта

def extract_section(text: str, keywords: tuple[str, ...]) -> str:
    """Раздел markdown по заголовку (первое совпадение — до следующего заголовка)."""
    if not text:
        return ""
    lines = (text or "").splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.lstrip().startswith("#"):
            low = line.lower()
            if inside:
                break
            if any(word in low for word in keywords):
                inside = True
                continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def build_passport(area: dict, sess: dict, context: dict[str, Any],
                   report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Паспорт нитки: что делалось, что осталось, где работать, что принять.

    `context` — то, что сторож уже знает из рантайма:
    `task` (первая реплика владельца), `last_final` (последняя сводка),
    `messages` (последние реплики), `handoff_path`, `handoff` (текст handoff),
    `done`, `left`, `criteria`.
    """
    folder = str(sess.get("workspace_dir") or area.get("folder") or "")
    git = git_state(folder)
    branch = git.get("branch") or ""
    repo = git.get("repo") or folder
    handoff = str(context.get("handoff") or "")
    criteria = str(context.get("criteria") or "").strip()
    if not criteria and handoff:
        criteria = extract_section(handoff, ("критерии приёмки", "критерии приемки",
                                             "приёмка", "приемка"))
    done = str(context.get("done") or "").strip()
    if not done and handoff:
        done = extract_section(handoff, ("что сделано", "сделано", "что сделал"))
    if not done:
        done = str(context.get("last_final") or "").strip()
    left = str(context.get("left") or "").strip()
    if not left and handoff:
        left = extract_section(handoff, ("что дальше", "что осталось", "дальше"))
    passport: dict[str, Any] = {
        "version": 1,
        "thread_id": thread_id_of(repo, branch),
        "session_id": str(sess.get("session_id") or ""),
        "area": str(area.get("slug") or ""),
        "created_at": _now(),
        "updated_at": _now(),
        "repo": repo,
        "branch": branch,
        "worktree": git.get("worktree") or folder,
        "worktrees": git.get("worktrees") or [],
        "files": git.get("files") or [],
        "commits": git.get("commits") or [],
        "prs": [],
        "done": done[:4000],
        "left": left[:4000],
        "criteria": criteria[:4000],
        "task": str(context.get("task") or "")[:4000],
        "handoff_path": str(context.get("handoff_path") or ""),
        "merged": bool(git.get("merged")),
        "branch_exists": bool(git.get("branch_exists")),
        "ahead": int(git.get("ahead") or 0),
        "behind": int(git.get("behind") or 0),
        "rule": FOREIGN_TREES_RULE,
    }
    if report:
        passport["report"] = report
    return passport


def enrich_with_prs(passport: dict[str, Any]) -> dict[str, Any]:
    """Дописать открытые PR нитки (после закрытия сессии — `gh` медленный)."""
    folder = str(passport.get("worktree") or "")
    branch = str(passport.get("branch") or "")
    try:
        prs = open_prs(folder, branch)
    except Exception:  # noqa: BLE001 — PR не обязательны
        prs = []
    passport["prs"] = prs
    return passport


def passport_text(passport: dict[str, Any], instruction: str = "") -> str:
    """Паспорт нитки — текстом (его получает новая сессия)."""
    parts = ["ПАСПОРТ НИТКИ (перенос сессии):", ""]
    parts += ["Репозиторий: %s" % (passport.get("repo") or "—"),
              "Ветка: %s" % (passport.get("branch") or "—"),
              "Worktree: %s" % (passport.get("worktree") or "—")]
    if passport.get("merged"):
        parts.append("⚠️ Ветка нитки УЖЕ СЛИТА в origin/master — новую ветку "
                     "начинай от свежего origin/master.")
    if not passport.get("branch_exists"):
        parts.append("⚠️ Ветки нитки больше нет (удалена) — начни новую ветку "
                     "от свежего origin/master.")
    prs = passport.get("prs") or []
    if prs:
        parts.append("Открытые PR: " + ", ".join(
            "#%s (%s)" % (item.get("number"), item.get("title") or "")
            for item in prs if isinstance(item, dict)))
    else:
        parts.append("Открытые PR: нет (или не удалось собрать — `gh` молчит).")
    files = passport.get("files") or []
    if files:
        parts += ["Файлы/модули нитки:", ", ".join(files[:40])]
    if passport.get("done"):
        parts += ["", "Что сделано:", str(passport.get("done"))]
    if passport.get("left"):
        parts += ["", "Что осталось:", str(passport.get("left"))]
    if passport.get("criteria"):
        parts += ["", "Критерии приёмки:", str(passport.get("criteria"))]
    if passport.get("commits"):
        parts += ["", "Коммиты нитки:", ", ".join(passport.get("commits") or [])]
    parts += ["", "ПРАВИЛО: " + FOREIGN_TREES_RULE]
    if instruction:
        parts += ["", "РЕШЕНИЕ «УПРАВЛЯЮЩЕГО»: %s" % instruction]
    return "\n".join(parts)


def continue_text(passport: dict[str, Any], instruction: str = "") -> str:
    """Команда «продолжи работу по нитке» — с ЧЕМ делать, а не «просто продолжи»."""
    parts = ["Продолжи работу по нитке.", ""]
    if passport.get("repo"):
        parts.append("Работай в репозитории %s, ветка %s (worktree %s)."
                     % (passport.get("repo"), passport.get("branch") or "—",
                        passport.get("worktree") or "—"))
    if instruction:
        parts += ["", "Что делать дальше (решение «Управляющего»):", instruction]
    if passport.get("left"):
        parts += ["", "Что осталось по нитке:", str(passport.get("left"))]
    if passport.get("criteria"):
        parts += ["", "Критерии приёмки:", str(passport.get("criteria"))]
    if passport.get("prs"):
        parts.append("Открытые PR: " + ", ".join(
            "#%s" % item.get("number") for item in passport.get("prs") or []
            if isinstance(item, dict)))
    parts += ["", "ПРАВИЛО: " + FOREIGN_TREES_RULE]
    parts += ["", "Если работа уже закончена — напечатай итоговую сводку и "
              "остановись. Если нужен выбор владельца — перечисли варианты и жди."]
    return "\n".join(parts)


# ───────────────────────────────────────────── решение: модель и фолбэк

def _bridge_token() -> str:
    try:
        return BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ask_manager(facts: dict[str, Any], area: str = "",
                timeout: int = 120) -> dict[str, Any]:
    """Спросить решение у встроенной нейросети «Управляющего» (через API программы).

    ⚠️ Сервер живёт в том же процессе, что и встроенная модель, ⇒ решение
    «Управляющего» доступно и на стороне демона-сторожа. Если API не отвечает
    (программа не запущена) — возвращаем `{"ok": False}`: решение принимает
    правило-фолбэк.
    """
    token = _bridge_token()
    if not token:
        return {"ok": False, "why": "нет токена локального API программы"}
    body = json.dumps({"facts": facts, "area": area}, ensure_ascii=False,
                      default=str).encode("utf-8")
    url = "%s/treat-decision?token=%s" % (API_BASE.rstrip("/"), token)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-MiniGen-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "why": "программа ответила %s: %s" % (exc.code, detail)}
    except Exception as exc:  # noqa: BLE001 — программа не поднята / сеть
        return {"ok": False, "why": "программа не отвечает: %s" % exc}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"ok": False, "why": "ответ программы не разобран"}
    if not isinstance(data, dict):
        return {"ok": False, "why": "ответ программы не словарь"}
    return data


def fallback_decision(facts: dict[str, Any]) -> dict[str, Any]:
    """Предсказуемое правило, когда встроенная модель недоступна или ответила
    не по схеме.

    ⚠️ Правило **не требует завершать сессию без отчёта о выполнении** —
    это главное требование промта #172:
    - есть отчёт о выполнении ⇒ работа закончена ⇒ «завершать»;
    - отчёта нет ⇒ «продолжать» (продолжить работу по нитке). Перенос нитки
      без решения «Управляющего» не делаем: это большее действие, чем может
      решить правило.
    """
    has_report = bool((facts or {}).get("has_report"))
    if has_report:
        return {"ok": True, "action": FINISH, "instruction":
                "Работа закончена (есть отчёт о выполнении) — завершай сессию "
                "итоговой сводкой.",
                "reason": "правило-фолбэк: отчёт о выполнении есть",
                "source": "правило"}
    return {"ok": True, "action": CONTINUE, "instruction":
            "Продолжай работу по нитке: доделай то, что осталось, и напиши "
            "итоговую сводку, когда работа будет закончена.",
            "reason": "правило-фолбэк: отчёта о выполнении нет — завершать нельзя",
            "source": "правило"}


def decide(facts: dict[str, Any], area: str = "",
           allow_model: bool = True) -> dict[str, Any]:
    """Решение о лечении: сначала «Управляющий», потом правило-фолбэк."""
    if allow_model:
        answer = ask_manager(facts, area)
        if answer.get("ok") and answer.get("action") in ACTIONS:
            out = dict(answer)
            out["source"] = "Управляющий"
            return out
        reason = str(answer.get("why") or "ответ не по схеме")
        fallback = fallback_decision(facts)
        fallback["model_why"] = reason
        return fallback
    fallback = fallback_decision(facts)
    fallback["model_why"] = "модель не спрашивали"
    return fallback
