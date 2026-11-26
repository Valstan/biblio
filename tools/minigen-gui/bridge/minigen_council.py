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
- **бюджет совета — тоже настройка**: сколько раундов задачи занимает
  обсуждение (`councilRounds`) и сколько гарантированно достаётся работе
  (`councilWorkFloor`). Числа из живых проб — это дефолт, а не догма;
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
#: (в настройках окна: `councilEnabled`, `councilMaxRoles`, `councilRounds`,
#: `councilWorkFloor`).
#:
#: ⚠️ По умолчанию ролей **три**: совет дорог (каждый голос — вызов модели),
#: а бюджет задачи общий (`MAX_ROUNDS` в `minigen_agent.py`): совет и работа
#: считаются вместе, и работа никогда не остаётся без шагов.
#: Владелец может поднять число до шести — но задача станет дольше и дороже.
#: ⚠️ Настоящий предел — **бюджет**: секретарь зовёт не больше голосов, чем
#: совет потом оплатит (голосу нужно минимум `MIN_VOICE_ROUNDS` — прочитать и
#: ответить). Настройка — потолок, бюджет — фактическое число.
DEFAULT_MAX_ROLES = 3
MAX_ROLES_LIMIT = 6
#: Сколько раундов задачи может занять совет (настройка `councilRounds`).
#:
#: ⚠️ Число — из живых проб 26.11: работа по задаче «объясни, как устроен
#: совет» без совета занимает 12 раундов, а полный совет — не меньше 14. Но
#: это **дефолт**, а не догма: решение за владельцем, и он может поднять
#: число (совет станет дольше) или опустить (совет станет короче).
DEFAULT_COUNCIL_ROUNDS = 12
#: Гарантированный запас шагов для работы после обсуждения (настройка
#: `councilWorkFloor`). Живой случай 25.11 оставил работе 4 шага — этого
#: не хватало: задача «найди дефект и почини» столько не занимает.
DEFAULT_WORK_FLOOR = 12
#: Меньше двух раундов у совета не остаётся даже на секретаря и судью.
MIN_COUNCIL_ROUNDS = 2
#: Меньше четырёх шагов работе не хватает даже на «прочитал — сказал».
MIN_WORK_FLOOR = 4
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
#: Минимум раундов на голос: голос должен успеть **прочитать и ответить**.
#:
#: ⚠️ Живая проба 26.11: при **двух** шагах голос успевал только читать — ответ
#: не прозвучал («не уложился в 2 шагов»), и мнение в обсуждении было пустым.
#: Четыре шага — два чтения и ответ. Значит, **сколько голосов реально
#: прозвучит, решает бюджет**: при 12 раундах совета — один голос, при 20 —
#: больше. Читать и молчать — хуже, чем не звать вовсе.
MIN_VOICE_ROUNDS = 4
#: Минимум раундов на исполнителя — **меньше**: исполнитель готовит свою часть
#: (одно действие и ответ), а не читает и рассуждает.
#:
#: ⚠️ Эти две величины — разные (дыра, найденная тестом 26.11): когда минимум
#: голоса подняли до четырёх, тот же минимум забрал и исполнителям — работа
#: перестала делиться между тремя, а это прямое требование приёмки промта #132.
MIN_EXECUTOR_ROUNDS = 2
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
                      decision: dict[str, Any],
                      skipped: list[str] | None = None) -> str:
    """Как совет виден владельцу в чате.

    ⚠️ `skipped` — исполнители, которым **не досталось шагов**: решение их
    назвало, а бюджет совета не оплатил. Молчать об этом нельзя: владелец
    видит в решении «Исполнители: …» и считает, что работа сделана (живая
    проба 26.11: судья назвал двоих, шагов хватило на одного).
    """
    lines = ["Совет специалистов"]
    if not opinions:
        lines.append("• по этой задаче никого не позвали — обсуждения не было")
    for item in opinions:
        text = (item.get("text") or "").strip()
        if not text:
            # ⚠️ Молчание голоса — это не мнение: владелец должен видеть, что
            # обсуждение осталось пустым (живая проба 26.11: голос прочитал
            # четыре места и не ответил, а решение вынесено).
            text = ("не ответил — мнение в обсуждении пустое"
                    if not item.get("answered", True) else "мнения нет")
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
    if skipped:
        lines.append("Без шагов остались: %s — бюджет совета кончился, их часть "
                     "работы не сделана"
                     % ", ".join(role_title(slug) for slug in skipped))
    return "\n".join(lines)


def role_title(slug: str) -> str:
    """Название роли по слагу (для владельца — слово, а не идентификатор)."""
    item = role(slug)
    return str((item or {}).get("title") or slug)


def context_block(decision: dict[str, Any], opinions: list[dict[str, Any]],
                  outputs: list[dict[str, Any]],
                  skipped: list[str] | None = None) -> str:
    """Что совет решил — это входит в контекст основного хода агента.

    ⚠️ `skipped` — исполнители без шагов: основной ход должен знать, что их
    часть работы **не сделана**, иначе он отчитается о готовом, чего нет.
    """
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
    if skipped:
        parts.append("")
        parts.append("⚠️ Исполнители без шагов: %s — совет их не оплатил, их "
                     "часть работы НЕ сделана. Сделай её сам (своими "
                     "инструментами) или скажи владельцу прямо."
                     % ", ".join(role_title(slug) for slug in skipped))
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


def role_prompt(item: dict[str, Any], task: str, rounds: int = 0) -> str:
    """Системный промпт специалиста: его бриф + рамка ответа.

    ⚠️ **Сколько у него шагов — говорим прямо** (живая проба 26.11): голос с
    двумя раундами пытался прочитать четыре места и не успел ответить — его
    мнение в обсуждении оказалось пустым («не уложился в 2 шагов»). Поэтому в
    промпте: «прочитай **одно** главное место и ответь», а не «перебери
    репозиторий». Число шагов — то, что реально досталось голосу.
    """
    steps = int(rounds or 0)
    limit = ""
    if steps > 0:
        limit = ("⚠️ У тебя %d шагов — и это **весь** бюджет твоего голоса. "
                 "Сделай не больше двух команд чтения (лучше всего одна "
                 "`code.search` по ключевому слову — вернёт места сразу) и "
                 "**сразу ответь** по своей роли, даже если картина неполная. "
                 "Не хватает данных — скажи, чего именно не хватает, и закончи "
                 "ответом. Молчание хуже неполного мнения: пустое мнение в "
                 "обсуждении — это как его нет.\n\n" % steps)
    return (
        "%s"
        "%s\n\n"
        "Ты — специалист совета программы «МиниГен» и отвечаешь ТОЛЬКО по своей "
        "роли. Другие роли отвечают за своё — не лезь в чужую.\n\n"
        "Задание владельца:\n%s\n\n"
        "Отвечай коротко (3–6 пунктов), по делу, с опорой на реальные данные "
        "программы: читай код, состояние и проверки своими инструментами. Читай "
        "точечно — то, что нужно для твоей роли, не перебирай репозиторий целиком. "
        "Если чего-то не хватает — так и скажи, чего именно, и не выдумывай."
        % (limit, brief_of(item).strip(), task.strip())
    )


#: Подсказка ходу ответа голоса: инструментов в нём нет.
#:
#: ⚠️ Живая проба 26.11: без подсказки модель в шаге ответа **продолжала**
#: звать инструменты (в переписке шла цепочка чтения) — и ответ не
#: прозвучал снова. Слова «инструментов больше нет» рвут цепочку.
VOICE_ANSWER_NUDGE = ("Инструментов в этом ходе нет. Ты уже прочитал всё, что "
                      "нужно, — **ответь сейчас**, по своей роли.")


def voice_answer_prompt(item: dict[str, Any], task: str) -> str:
    """Промпт последнего шага голоса: **ответ**, а не чтение.

    ⚠️ Живая проба 26.11 (промт #132): голос, которому досталось четыре шага,
    прочитал четыре места и **не ответил** — мнение в обсуждении оказалось
    пустым, а решение вынесено по одному пустому мнению. Поэтому последний
    шаг голоса отдан ответу: в этом ходе инструментов **нет** — читать уже
    нечего, осталось сказать.
    """
    return (
        "%s\n\n"
        "Ты — специалист совета программы «МиниГен» и отвечаешь ТОЛЬКО по своей "
        "роли.\n\n"
        "Ты уже прочитал то, что тебе нужно. **Сейчас только ответь** — коротко "
        "(3–6 пунктов), по своей роли, с опорой на прочитанное. Больше ничего не "
        "читай и ничего не делай: в этом ходе инструментов нет. Если "
        "чего-то не хватает — так и скажи, чего именно, и закончи ответом. "
        "Молчание хуже неполного мнения: пустое мнение в обсуждении — это как "
        "его нет.\n\n"
        "Задание владельца:\n%s"
        % (brief_of(item).strip(), task.strip())
    )


def judge_prompt(cohort: list[dict[str, Any]],
                 max_executors: int = -1) -> str:
    """Системный промпт судьи: сводит обсуждение в решение.

    ⚠️ `max_executors` — сколько исполнителей совет **может оплатить**
    (каждому — минимум `MIN_EXECUTOR_ROUNDS` шагов). Судья должен знать предел:
    иначе решение обещает то, что совет не оплатит, и владелец видит в
    переписке трёх исполнителей, а отработал один (живая проба 26.11).
    `-1` — предел неизвестен (строка про бюджет не добавляется).
    """
    executors_list = ", ".join("%s (%s)" % (item.get("title"), item.get("slug"))
                               for item in cohort
                               if str(item.get("kind")) == "executor")
    budget_line = ""
    if max_executors >= 0:
        budget_line = ("\n\n⚠️ Бюджет совета: исполнителей, которым хватит "
                       "шагов, — **не больше %d**. Назови не больше %d: решение "
                       "не должно обещать то, что совет не оплатит. Если работы "
                       "больше — выбери, кто важнее, а остальное скажи в "
                       "«что не делать»." % (max_executors, max_executors))
    return (
        "Ты — судья совета специалистов программы «МиниГен».\n\n"
        "Ты сводишь обсуждение специалистов в решение. Приоритет при конфликте: "
        "владелец → судья → совет. Если в обсуждении есть противоречие прямой "
        "просьбе владельца — это уходит в «уточнить у владельца»."
        "%s\n\n"
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
        "за владельцем." % (budget_line, executors_list)
    )


def judge_input(task: str, opinions: list[dict[str, Any]]) -> str:
    """Что видит судья: задача и мнения специалистов."""
    parts = ["Задание владельца:\n%s" % task.strip(), "", "Мнения специалистов:"]
    for item in opinions:
        text = (item.get("text") or "").strip()
        if not text:
            # ⚠️ Голос, который не ответил, — это **пустое** мнение: судья
            # должен знать, что обсуждения было меньше, чем кажется.
            text = "не ответил" if not item.get("answered", True) else "—"
        parts.append("— %s: %s" % (item.get("title") or item.get("slug"), text))
    if not opinions:
        parts.append("— никого не позвали: обсуждения не было.")
    return "\n".join(parts)


def executor_prompt(item: dict[str, Any], decision: dict[str, Any],
                     rounds: int = 0) -> str:
    """Системный промпт исполнителя: его часть решения и его инструменты.

    ⚠️ Число шагов — тоже прямо (живая проба 26.11): исполнитель с двумя
    раундами должен **одним** действием показать, что делать, а не начать
    перебор.
    """
    steps = int(rounds or 0)
    limit = ""
    if steps > 0:
        limit = ("\n\nУ тебя %d шагов — сделай **одно** главное действие по своей "
                 "части (прочитай нужное место или подготовь правку) и ответь "
                 "коротко." % steps)
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
        "агента — ты готовишь решение, а не правишь файлы.%s"
        % (brief_of(item).strip(),
           "; ".join(decision.get("to_do") or []) or "—",
           "; ".join(decision.get("not_to_do") or []) or "—",
           "; ".join(decision.get("ask_owner") or []) or "—",
           limit)
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


def _number(settings: dict[str, Any], key: str, default: int,
            low: int, high: int) -> int:
    """Число из настроек владельца — с границами (мусор ⇒ дефолт)."""
    try:
        value = int((settings or {}).get(key))
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return max(low, min(high, value))


def budget(settings: dict[str, Any], task_cap: int) -> dict[str, int]:
    """Бюджет задачи, делённый между советом и работой.

    Числа — **настройки владельца** (`councilRounds`, `councilWorkFloor`),
    дефолты — из живых проб 26.11. Правило одно: совет и работа — вместе не
    выходят за предел задачи (`task_cap`), и работа никогда не остаётся без
    шагов.

    ⚠️ Границы важны: если владелец попросит и совету, и работе по 24, то
    совет + работа = 48 раундов — и приёмка промта #132 («число раундов на
    задачу не выходит за `MAX_ROUNDS`») сломалась бы. Поэтому запас работы
    ограничен сверху так, чтобы совету осталось хотя бы `MIN_COUNCIL_ROUNDS`,
    а совет — не больше `task_cap - MIN_WORK_FLOOR`.

    ⚠️ Если числа не влезают вместе — уступает **совет**: запас работы
    гарантирован, а не выбирается. Поэтому `council` здесь — уже
    **эффективная** доля совета, а не просьба владельца.
    """
    cap = max(MIN_COUNCIL_ROUNDS, int(task_cap or 0))
    # Запас работы ограничен так, чтобы совету осталось хотя бы
    # `MIN_COUNCIL_ROUNDS`: иначе совет выйдет за свою долю, а раундов на
    # задачу станет больше предела. При очень маленьком пределе гарантия
    # работы уступает жёсткому лимиту (иначе не помещается ни то, ни другое).
    work_high = max(0, cap - MIN_COUNCIL_ROUNDS)
    work_floor = _number(settings, "councilWorkFloor", DEFAULT_WORK_FLOOR,
                         min(MIN_WORK_FLOOR, work_high), work_high)
    council_rounds = _number(settings, "councilRounds",
                             DEFAULT_COUNCIL_ROUNDS, MIN_COUNCIL_ROUNDS,
                             max(MIN_COUNCIL_ROUNDS, cap - work_floor))
    return {"council": council_rounds, "work": work_floor, "cap": cap}


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
