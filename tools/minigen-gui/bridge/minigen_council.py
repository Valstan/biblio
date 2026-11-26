"""Совет специалистов «МиниГена»: обсуждение задачи и решение судьи.

Заказ владельца (25.11, промт #132 в разделе `MiniGen` библиотеки КАРМАНА):
«в программе появляется совет из 5–6 специалистов: новое задание сначала
обсуждается советом, затем судья выносит решение и назначает исполнителей, а
совет может завести нового специалиста».

Конвейер:

    задача → отбор ролей (секретарь) → обсуждение (специалисты) →
    решение (судья) → исполнители (каждый со своими инструментами) → работа

Что здесь зашито (и это не украшение, а правила):

- **совет смотрит в программу**: каждый специалист со своими инструментами
  чтения, а не «по памяти модели» — иначе обсуждение пустое;
- **решение совета** — структура с полями: что делать, что не делать, кто
  исполнитель, что уточнить у владельца. Термин именно такой: «вердикт» в
  программе — это состояние сессии (`SessionVerdict`), путать нельзя;
- **ролей немного**: по умолчанию отвечают не больше трёх — иначе цикл станет
  долгим и дорогим (главный риск назвал владелец). Предел — настройка;
- **новый специалист — только предложение**: роль попадает в совет после
  решения владельца (опасный инструмент `council.add_role`), а не с молча;
- **выход за рамки ролей** совет фиксирует явно, а не молча игнорирует.

Роли лежат в репозитории программы (`bridge/council/`) — совет переживает
обновление и виден и нейросети, и человеку. Роли, одобренные владельцем
сверх реестра, добавляются **в состояние программы**
(`%LOCALAPPDATA%\\minigen-gui\\council`): в репозиторий их вносит человек,
через ветку и PR (правки кода — только так).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
COUNCIL_DIR = HERE / "council"
ROLES_DIR = COUNCIL_DIR / "roles"
REGISTRY_FILE = COUNCIL_DIR / "registry.json"

#: Роли, одобренные владельцем, — в состоянии программы, не в репозитории.
LOCAL_DIR = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
             / "minigen-gui" / "council")


def local_roles_dir() -> Path:
    """Папка брифов одобренных ролей (путь берётся каждый раз — тесты
    подменяют `LOCAL_DIR`)."""
    return LOCAL_DIR / "roles"


def local_registry_file() -> Path:
    """Файл реестра одобренных ролей."""
    return LOCAL_DIR / "registry.json"

#: Пределы конвейера. Числа предлагает исполнитель, решение — за владельцем
#: (в настройках окна: `councilEnabled`, `councilMaxRoles`).
#:
#: ⚠️ По умолчанию ролей **три**: совет дорог (каждый голос — вызов модели),
#: а бюджет задачи общий (`MAX_ROUNDS`): совет и работа считаются вместе, и
#: работа никогда не остаётся без шагов (`WORK_FLOOR` в `minigen_agent.py`).
#: Владелец может поднять число до шести — но задача станет дольше и дороже.
#: ⚠️ Настоящий предел — **бюджет**: секретарь зовёт не больше голосов, чем
#: совет потом оплатит (голос нужен минимум в два раунда — прочитать и
#: ответить). Настройка — потолок, бюджет — фактическое число.
DEFAULT_MAX_ROLES = 3
MAX_ROLES_LIMIT = 6
#: Раундов на голос специалиста: он читает файлы и отвечает. Нужен запас:
#: чтение — это отдельные шаги, ответ — следующий.
#:
#: ⚠️ Живая проба 26.11: при 5 шагов голос «Супер-программист» прочитал четыре
#: места и **не успел ответить** — его мнение в обсуждении оказалось пустым
#: («не уложился в 5 шагов»). Шесть шагов — это четыре чтения и ответ.
ROLE_ROUNDS = 6
JUDGE_ROUNDS = 1
EXECUTOR_ROUNDS = 2
#: Резерв совета под судью и исполнителей (в раундах). Голоса не должны съесть
#: весь бюджет совета: без решения судьи обсуждение бессмысленно, а без
#: исполнителей решение остаётся без работы.
#:
#: Шесть раундов — это три исполнителя по два: каждый должен успеть прочитать
#: и ответить (приёмка промта #132: работа делится между исполнителями, а не
#: делается одним).
EXECUTOR_POOL = 6
#: Минимум раундов на голос: голос должен успеть **прочитать и ответить** (два
#: шага: вызов инструмента и ответ). Голос в один шаг не отвечает — значит,
#: его мнения в обсуждении нет.
MIN_VOICE_ROUNDS = 2
#: Больше трёх исполнителей решение совета не должно выводить: работа
#: делится, а не размножается.
MAX_EXECUTORS = 3
#: Сколько символов ответа специалиста уходит в решение.
ROLE_TEXT_LIMIT = 4000

#: Поля решения совета — их программа проверяет приёмкой.
DECISION_FIELDS = ("to_do", "not_to_do", "executors", "ask_owner")

#: Продолжения нитки: совету они не нужны (он по этой задаче уже отработал).
CONTINUATION_WORDS = (
    "продолжай", "продолжить", "продолжаем", "дальше", "давай", "далее",
    "иди", "поехали", "приступай", "готов", "готово", "да", "нет", "ок",
    "окей", "хорошо", "спасибо", "понял", "поняла",
)


# ─────────────────────────────────────────────────────────── реестр ролей

def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def registry() -> dict[str, Any]:
    """Реестр совета: роли из репозитория + одобренные владельцем.

    Репозиторий — источник правды; одобренные роли добавляются поверх
    (в состояние программы), чтобы новая роль появилась у владельца сразу,
    но в репозиторий попала только через ветку и PR.
    """
    base = _read_json(REGISTRY_FILE)
    roles = [role for role in (base.get("roles") or []) if isinstance(role, dict)]
    known = {str(role.get("slug") or "") for role in roles}
    local = _read_json(local_registry_file())
    for role in local.get("roles") or []:
        if not isinstance(role, dict):
            continue
        slug = str(role.get("slug") or "")
        if slug and slug not in known:
            known.add(slug)
            roles.append(role)
    return {**base, "roles": roles}


def roles() -> list[dict[str, Any]]:
    return list(registry().get("roles") or [])


def role(slug: str) -> dict[str, Any] | None:
    """Роль по слагу (с slug'ом владельца)."""
    want = (slug or "").strip()
    for item in roles():
        if str(item.get("slug") or "") == want:
            return item
    return None


def executors() -> list[dict[str, Any]]:
    """Роли-исполнители (их назначает судья)."""
    return [item for item in roles() if str(item.get("kind")) == "executor"]


def brief_of(item: dict[str, Any]) -> str:
    """Текст брифа роли (из репозитория или из одобренного локального)."""
    slug = str(item.get("slug") or "").strip()
    name = str(item.get("brief") or ("roles/%s.md" % slug)).strip()
    for base in (ROLES_DIR.parent, LOCAL_DIR):
        path = (base / name).resolve()
        try:
            if path.is_file() and str(path).startswith(str(base.resolve())):
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ("# Роль\n\n%s (%s)\n" % (item.get("title") or slug, slug))


# ────────────────────────────────────────────────────────── решения и тексты

def parse_json(text: str) -> dict[str, Any]:
    """Объект из ответа модели — терпимо к обёрткам и болтовне вокруг."""
    clean = (text or "").strip()
    if not clean:
        return {}
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-zA-Z]*\n?", "", clean)
        clean = re.sub(r"\n?```\s*$", "", clean).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(clean[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _items(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        value = [value]
    for item in value or []:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("title") or item.get("what")
                        or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def normalize_decision(raw: dict[str, Any], executor_slugs: list[str]) -> dict[str, Any]:
    """Решение судьи → единый вид (поля проверяет приёмка)."""
    executors_chosen: list[str] = []
    for item in raw.get("executors") or []:
        slug = (str(item.get("slug") or "").strip()
                if isinstance(item, dict) else str(item or "").strip())
        if not slug:
            continue
        match = next((known for known in executor_slugs
                      if known.lower() == slug.lower()), "")
        if match and match not in executors_chosen:
            executors_chosen.append(match)
        if len(executors_chosen) >= MAX_EXECUTORS:
            break
    new_role = raw.get("new_role") or raw.get("new_specialist")
    proposal: dict[str, Any] = {}
    if isinstance(new_role, dict) and (new_role.get("title") or new_role.get("role")):
        proposal = {
            "title": str(new_role.get("title") or "").strip(),
            "role": str(new_role.get("role") or "").strip(),
            "why": str(new_role.get("why") or new_role.get("reason") or "").strip(),
        }
    return {
        "to_do": _items(raw.get("to_do") or raw.get("todo")),
        "not_to_do": _items(raw.get("not_to_do") or raw.get("not_do")),
        "executors": executors_chosen,
        "ask_owner": _items(raw.get("ask_owner") or raw.get("ask")),
        "new_role": proposal or None,
        "summary": str(raw.get("summary") or "").strip(),
    }


def format_discussion(opinions: list[dict[str, Any]],
                      decision: dict[str, Any]) -> str:
    """Как совет виден владельцу в чате."""
    lines = ["Совет специалистов"]
    if not opinions:
        lines.append("• по этой задаче никого не позвали — обсуждения не было")
    for item in opinions:
        text = (item.get("text") or "").strip() or "мнения нет"
        lines.append("• %s: %s" % (item.get("title") or item.get("slug"), text))
    lines.append("")
    lines.append("Решение совета")
    lines.append("Что делать: %s" % ("; ".join(decision.get("to_do") or []) or "—"))
    lines.append("Что не делать: %s"
                 % ("; ".join(decision.get("not_to_do") or []) or "—"))
    executors = decision.get("executors") or []
    titles = []
    for slug in executors:
        item = role(slug)
        titles.append(str((item or {}).get("title") or slug))
    lines.append("Исполнители: %s" % ("; ".join(titles) or "—"))
    lines.append("Уточнить у владельца: %s"
                 % ("; ".join(decision.get("ask_owner") or []) or "—"))
    proposal = decision.get("new_role")
    if proposal:
        lines.append("Вне рамок ролей: нужен новый специалист — %s (%s). %s"
                     % (proposal.get("title") or "—", proposal.get("role") or "—",
                        proposal.get("why") or ""))
    return "\n".join(lines)


def context_block(decision: dict[str, Any], opinions: list[dict[str, Any]],
                  outputs: list[dict[str, Any]]) -> str:
    """Что совет решил — это входит в контекст основного хода агента."""
    parts = ["Совет специалистов обсудил задачу. Решение совета:"]
    parts.append("Что делать: %s" % ("; ".join(decision.get("to_do") or []) or "—"))
    parts.append("Что не делать: %s"
                 % ("; ".join(decision.get("not_to_do") or []) or "—"))
    parts.append("Кто исполнитель: %s"
                 % (", ".join(decision.get("executors") or []) or "—"))
    if decision.get("ask_owner"):
        parts.append("Что уточнить у владельца: %s"
                     % "; ".join(decision["ask_owner"]))
    proposal = decision.get("new_role")
    if proposal:
        parts.append("Совет зафиксировал выход за рамки ролей и предлагает нового "
                     "специалиста: %s — %s. %s" % (proposal.get("title") or "—",
                                                   proposal.get("role") or "—",
                                                   proposal.get("why") or ""))
        parts.append("Предложи это владельцу. Если он согласится — вызови "
                     "council.add_role (действие опасное, спроси подтверждение).")
    if outputs:
        parts.append("")
        parts.append("Что подготовили исполнители:")
        for item in outputs:
            parts.append("— %s: %s" % (item.get("title") or item.get("slug"),
                                       (item.get("text") or "").strip() or "—"))
    parts.append("")
    parts.append("Работай по решению совета. Если оно противоречит прямой "
                 "просьбе владельца — спрашивай владельца (приоритет: владелец → "
                 "судья → совет).")
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────── промпты

def triage_prompt(task: str, cohort: list[dict[str, Any]], limit: int) -> str:
    """Секретарь отбирает, кто нужен на задачу (не больше `limit`)."""
    lines = [
        "Ты — секретарь совета специалистов программы «МиниГен».",
        "Твоя одна задача: решить, КТО нужен на это задание.",
        "",
        "Совет:",
    ]
    for item in cohort:
        lines.append("- %s (%s, %s): %s" % (item.get("title"), item.get("slug"),
                                            item.get("kind"),
                                            item.get("mandatory_when") or "—"))
    lines += [
        "",
        "Ответь ТОЛЬКО объектом JSON без пояснений:",
        '{"summary": "одно предложение о задаче", "roles": ["slug-1"], '
        '"why": "почему эти роли"}',
        "",
        "Правила:",
        "- в roles только slug'и из списка совета;",
        "- не больше %d ролей (совет дорог: каждый ответ — это вызов модели);" % limit,
        "- бери минимум: без роли задача разбирается хуже, но лишний голос — "
        "это лишние очки и время;",
        "- исполнителей (coder, reviewer, presenter) не зови — их назначает судья.",
    ]
    return "\n".join(lines)


def role_prompt(item: dict[str, Any], task: str) -> str:
    """Системный промпт специалиста: его бриф + рамка ответа."""
    return (
        "%s\n\n"
        "Ты — специалист совета программы «МиниГен» и отвечаешь ТОЛЬКО по своей "
        "роли. Другие роли отвечают за своё — не лезь в чужую.\n\n"
        "Задание владельца:\n%s\n\n"
        "Отвечай коротко (3–6 пунктов), по делу, с опорой на реальные данные "
        "программы: читай код, состояние и проверки своими инструментами. Читай "
        "точечно — то, что нужно для твоей роли, не перебирай репозиторий целиком. "
        "Если чего-то не хватает — так и скажи, чего именно, и не выдумывай."
        % (brief_of(item).strip(), task.strip())
    )


def judge_prompt(cohort: list[dict[str, Any]]) -> str:
    """Системный промпт судьи: сводит обсуждение в решение."""
    executors_list = ", ".join("%s (%s)" % (item.get("title"), item.get("slug"))
                               for item in cohort
                               if str(item.get("kind")) == "executor")
    return (
        "Ты — судья совета специалистов программы «МиниГен».\n\n"
        "Ты сводишь обсуждение специалистов в решение. Приоритет при конфликте: "
        "владелец → судья → совет. Если в обсуждении есть противоречие прямой "
        "просьбе владельца — это уходит в «уточнить у владельца».\n\n"
        "Исполнители (их назначаешь ты): %s\n\n"
        "Ответь ТОЛЬКО объектом JSON без пояснений:\n"
        '{"to_do": ["..."], "not_to_do": ["..."], "executors": ["slug"], '
        '"ask_owner": ["..."], "summary": "одно предложение", '
        '"new_role": null}\n\n'
        "Правила:\n"
        "- to_do — что делать (проверяемые пункты);\n"
        "- not_to_do — чего НЕ делать;\n"
        "- executors — slug'и исполнителей; если работа делится (код + проверка + "
        "презентация) — перечисли их всех, а не одного; каждый делает свою часть;\n"
        "- ask_owner — что нельзя решить без владельца (пустой список, если всё "
        "ясно);\n"
        "- new_role — null. Если задача выходит за рамки ролей совета, заполни "
        '{"title": "...", "role": "...", "why": "..."} — это ПРЕДЛОЖЕНИЕ, решение '
        "за владельцем." % executors_list
    )


def judge_input(task: str, opinions: list[dict[str, Any]]) -> str:
    """Что видит судья: задача и мнения специалистов."""
    parts = ["Задание владельца:\n%s" % task.strip(), "", "Мнения специалистов:"]
    for item in opinions:
        parts.append("— %s: %s" % (item.get("title") or item.get("slug"),
                                   (item.get("text") or "").strip() or "—"))
    if not opinions:
        parts.append("— никого не позвали: обсуждения не было.")
    return "\n".join(parts)


def executor_prompt(item: dict[str, Any], decision: dict[str, Any]) -> str:
    """Системный промпт исполнителя: его часть решения и его инструменты."""
    return (
        "%s\n\n"
        "Ты — исполнитель в совете программы «МиниГен». Ты делаешь **свою часть** "
        "работы по решению совета, а не всю работу целиком.\n\n"
        "Решение совета:\n"
        "Что делать: %s\n"
        "Что не делать: %s\n"
        "Что уточнить у владельца: %s\n\n"
        "Отвечай по своей части: что именно делать, где (файлы, места) и как "
        "проверить. Коротко и конкретно. Изменения в коде применяет основной ход "
        "агента — ты готовишь решение, а не правишь файлы."
        % (brief_of(item).strip(),
           "; ".join(decision.get("to_do") or []) or "—",
           "; ".join(decision.get("not_to_do") or []) or "—",
           "; ".join(decision.get("ask_owner") or []) or "—")
    )


def executor_input(task: str, decision: dict[str, Any],
                   outputs: list[dict[str, Any]]) -> str:
    """Задание и решения для исполнителя + уже сделанное другими."""
    parts = ["Задание владельца:\n%s" % task.strip(), "",
             "Решение совета:", "Что делать: %s"
             % ("; ".join(decision.get("to_do") or []) or "—"),
             "Что не делать: %s"
             % ("; ".join(decision.get("not_to_do") or []) or "—"),
             "Кто ещё исполнитель: %s"
             % (", ".join(decision.get("executors") or []) or "—")]
    if outputs:
        parts.append("")
        parts.append("Что уже подготовили другие исполнители:")
        for item in outputs:
            parts.append("— %s: %s" % (item.get("title") or item.get("slug"),
                                       (item.get("text") or "").strip() or "—"))
    return "\n".join(parts)


# ───────────────────────────────────────────────────────── настройки и слова

def enabled(settings: dict[str, Any]) -> bool:
    """Включён ли совет (владелец может выключить его в настройках)."""
    return bool((settings or {}).get("councilEnabled", True))


def max_roles(settings: dict[str, Any]) -> int:
    """Сколько ролей реально отвечают на задачу."""
    try:
        value = int((settings or {}).get("councilMaxRoles") or DEFAULT_MAX_ROLES)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_ROLES
    return max(1, min(MAX_ROLES_LIMIT, value))


def is_continuation(text: str) -> bool:
    """Продолжение нитки — совет по этой задаче уже отработал."""
    clean = (text or "").strip().lower().rstrip("!.").strip()
    if not clean or len(clean) > 40:
        return False
    return any(clean == word or clean.startswith(word + " ")
               for word in CONTINUATION_WORDS)


# ─────────────────────────────────────────────── роли, одобренные владельцем

def add_role(slug: str, title: str, role_text: str, why: str = "") -> dict[str, Any]:
    """Добавить роль в совет (после решения владельца).

    Пишем **в состояние программы**, а не в репозиторий: правки кода — только
    веткой и PR, а владельцу роль нужна сразу.
    """
    name = (slug or "").strip().lower().replace(" ", "-")
    if not name or not re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
        return {"ok": False, "error": "slug роли: латиница, цифры, дефис"}
    if not (title or "").strip():
        return {"ok": False, "error": "нужно название роли"}
    existing = role(name)
    if existing is not None:
        return {"ok": False, "error": "такая роль в совете уже есть"}
    local_roles_dir().mkdir(parents=True, exist_ok=True)
    brief = ("# Роль\n\n%s\n\n%s\n\n## Зачем эта роль\n\n%s\n"
             % (title.strip(), (role_text or "").strip(), (why or "").strip()))
    (local_roles_dir() / ("%s.md" % name)).write_text(brief, encoding="utf-8")
    data = _read_json(local_registry_file())
    items = [item for item in (data.get("roles") or []) if isinstance(item, dict)]
    items.append({
        "slug": name,
        "title": title.strip(),
        "kind": "advisor",
        "brief": "roles/%s.md" % name,
        "tools": ["code.read", "code.search", "journal.read"],
        "mandatory_when": why.strip() or "добавлена владельцем",
    })
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    local_registry_file().write_text(
        json.dumps({"version": 1, "roles": items}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {"ok": True, "slug": name, "title": title.strip()}
