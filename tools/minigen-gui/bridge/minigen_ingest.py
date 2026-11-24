"""Заявка: как зачитка владельца становится задачей для нейросети.

Заказ владельца (24.11): «все мои зачитки промптов добавлять в хранилище
промтов на кармане и рассортировывать их по проектам… Если в промте не
указан конкретный проект, то он может создать новый проект… и записать туда
промт **и план реализации**».

Формат — **один файл**, а не две вставки («причёсанная зачитка» + «план»):

- две копии одного и того же расходятся (правка в одной не попадает в
  другую) — а исполнитель всё равно читает **один** текст;
- один файл = один идентификатор = одна запись в хранилище = один PR;
- план — это раздел той же заявки, а не отдельный документ: он живёт ровно
  столько, сколько живёт задача.

Структура заявки (порядок разделов — канон):

    # Название
    **Проект:** <slug>            — куда заявка
    **Суть:** ...                 — одна фраза
    ## Что сделать               — причёсанная зачитка, в императиве
    ## Контекст                   — что уже есть и чего не хватает
    ## План                       — шаги, с проверяемым результатом
    ## Критерии приёмки           — как понять, что готово
    ## Риски и открытые вопросы   — что может помешать
    ## Источник                   — откуда заявка (ссылка, дата)

Модуль **чистый**: ни сети, ни файловой системы, ни моделей — всё
проверяется тестом. Ошибки в оформлении заявки — самая частая беда
такого агента, а проверяется это дёшево и мгновенно.
"""

from __future__ import annotations

import re
from typing import Any

#: Разделы заявки — в этом порядке.
SECTIONS: tuple[str, ...] = (
    "Что сделать",
    "Контекст",
    "План",
    "Критерии приёмки",
    "Риски и открытые вопросы",
    "Источник",
)

#: Служебные строки, которые пишутся перед разделами.
HEAD_KEYS: tuple[str, ...] = ("Проект", "Суть")

#: Порог, ниже которого совпадение считается недостаточным и проект
#: предлагается новый. 0.12 — «если ни один проект не подходит».
PROJECT_THRESHOLD = 0.12

#: Слова, которые ничего не говорят о проекте (шум диктовки).
STOP_WORDS = frozenset(
    """
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
    по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг
    ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
    себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам
    чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому
    этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
    куда зачем всех никогда можно при наконец два об другой хоть после над больше
    тот через эти нас про всего них какая много разве три эту моя впрочем хорошо
    свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно
    всю между
    """.split()
)

CYRILLIC: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}

WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_]+")


def translit(text: str) -> str:
    """Кириллица → латиница (для слагов)."""
    return "".join(CYRILLIC.get(char, char) for char in (text or "").lower())


def slugify(text: str, fallback: str = "idea") -> str:
    """Короткое латинское имя из названия."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", translit(text or "")).strip("-").lower()
    return slug or fallback


def words(text: str) -> list[str]:
    """Слова текста — для сравнения с проектами (без служебных)."""
    out: list[str] = []
    for word in WORD_RE.findall((text or "").lower()):
        if len(word) < 3 or word in STOP_WORDS:
            continue
        out.append(word)
    return out


def _project_terms(project: dict[str, Any]) -> set[str]:
    """Слова, по которым узнаём проект: слаг, название и ключевые слова."""
    terms: set[str] = set()
    for key in ("slug", "title", "name"):
        value = str(project.get(key) or "")
        if value:
            terms.add(value.lower())
    for key in ("keywords", "tags"):
        for item in project.get(key) or []:
            terms.add(str(item).lower())
    return {term for term in terms if term}


def guess_project(text: str, projects: list[dict[str, Any]]) -> dict[str, Any]:
    """К какому проекту относится заявка.

    Возвращает: ``{"slug", "title", "confidence", "known", "evidence"}``.

    ``known=False`` — ни один проект не подошёл ⇒ нужен новый проект
    (владелец как раз просил: «уметь создавать новые проекты, если такого
    проекта не существует»).
    """
    body = (text or "").lower()
    tokens = set(words(text))
    best: dict[str, Any] = {"slug": "", "title": "", "confidence": 0.0,
                            "known": False, "evidence": []}
    for project in projects:
        terms = _project_terms(project)
        if not terms:
            continue
        hits = 0
        evidence: list[str] = []
        for term in terms:
            if term in body:
                hits += 1
                evidence.append(term)
                continue
            # слово из текста, совпадающее с частью названия проекта
            for token in tokens:
                if len(token) > 3 and (token in term or term in token):
                    hits += 1
                    evidence.append(token)
                    break
        if not hits:
            continue
        score = hits / float(len(terms))
        if score > best["confidence"]:
            best = {
                "slug": str(project.get("slug") or ""),
                "title": str(project.get("title") or project.get("name") or ""),
                "confidence": round(score, 3),
                "known": True,
                "evidence": sorted(set(evidence))[:6],
            }
    if best["confidence"] < PROJECT_THRESHOLD:
        return {"slug": "", "title": "", "confidence": round(best["confidence"], 3),
                "known": False, "evidence": best["evidence"]}
    return best


def _section_body(draft: dict[str, Any], name: str) -> str:
    for key, value in (draft or {}).items():
        if str(key).strip().lower() == name.lower():
            return str(value or "").strip()
    return ""


def render_request(draft: dict[str, Any]) -> str:
    """Заявка по канону: один текст, разделы в фиксированном порядке."""
    draft = draft or {}
    title = str(draft.get("title") or "Без названия").strip()
    project = str(draft.get("project") or "").strip()
    summary = str(draft.get("summary") or "").strip()

    lines: list[str] = ["# %s" % title, ""]
    if project:
        lines.append("**Проект:** %s" % project)
    if summary:
        lines.append("**Суть:** %s" % summary)
    if project or summary:
        lines.append("")
    for name in SECTIONS:
        body = _section_body(draft, name)
        if not body:
            continue
        lines.append("## %s" % name)
        lines.append("")
        lines.append(body.rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_request(text: str) -> dict[str, Any]:
    """Разобрать заявку обратно — в словарь (для правок и проверок)."""
    out: dict[str, Any] = {"title": "", "sections": {}}
    body = (text or "").strip()
    if not body:
        return out
    lines = body.splitlines()
    if lines and lines[0].startswith("# "):
        out["title"] = lines[0][2:].strip()
        lines = lines[1:]
    current = ""
    chunks: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            chunks.setdefault(current, [])
            continue
        if line.startswith("**") and ":**" in line:
            key, _, value = line.partition(":**")
            out[key.strip("* ").lower()] = value.strip()
            continue
        if current:
            chunks[current].append(line)
    for name, chunk in chunks.items():
        content = "\n".join(chunk).strip()
        if content:
            out["sections"][name] = content
    for name in SECTIONS:
        if name in out["sections"]:
            out[name.lower()] = out["sections"][name]
    return out


def missing_sections(draft: dict[str, Any]) -> list[str]:
    """Каких разделов не хватает в заявке (для самопроверки агента)."""
    out: list[str] = []
    for name in ("что сделать", "план", "критерии приёмки"):
        if not _section_body(draft, name):
            out.append(name)
    return out


def request_filename(draft: dict[str, Any], when: str = "") -> str:
    """Имя файла заявки: `2026-11-24-sagafan-prompt.md`."""
    date = (when or "").strip() or "без-даты"
    slug = slugify(str((draft or {}).get("title") or "zayavka"))
    return "%s-%s.md" % (date, slug)
