"""Лечение сессий: решение принимает встроенная нейросеть «Управляющего» (промт #172).

Заказ владельца (26.11, промт #172, раздел `MiniGen`): «решение лечения принимает
встроенная нейросеть "Управляющего" — та же нейросеть, что отвечает в чате», и
«отсутствие отчёта о выполнении — не повод требовать завершения».

Что здесь и почему так:

- **словарь решений — ровно три действия**: `продолжать`, `завершать`,
  `новая_сессия` (с продолжением нитки). Больше ничего быть не может: решение
  «что-то ещё сделать» не лечит сессию, а решение «закрыть» без отчёта —
  ровно та беда, которую промт и чинит;
- **на вход — факты, а не простыня логов**: вердикт сторожа, есть ли отчёт о
  выполнении, событие сжатия контекста, заполнение контекста, когда был
  последний ход, репозиторий/ветка/worktree нитки, что осталось, критерии
  приёмки, открытые PR, параллельные сессии того же репозитория;
- **на выход — одно действие + текст инструкции обслуживаемому агенту +
  причина**: инструкция обязана **называть, что делать дальше** (из паспорта
  нитки), а не просто «продолжай» (риск из промта);
- **разбор ответа**: модель может ответить не по схеме ⇒ решение не
  принимается (`ok: false`), и сторож применяет **предсказуемое правило**
  (оно живёт на стороне демона — `tools/minimax-project-areas/minigen_thread.py`:
  правило должно работать и когда программа не запущена вовсе).

⚠️ Этот модуль **не решает** — он только спрашивает и разбирает. Исполняет
решение сторож сессий; там же видно, кто, когда, по каким фактам и какое
решение принял (отчёт/журнал).
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Продолжать: сессию не трогать; если стоит — мягко подтолкнуть.
CONTINUE = "продолжать"
#: Завершать: закрывать можно (есть отчёт о выполнении либо работа не нужна).
FINISH = "завершать"
#: Новая сессия с продолжением нитки (перенос).
NEW_SESSION = "новая_сессия"
#: Все три действия — и только они.
ACTIONS: tuple[str, ...] = (CONTINUE, FINISH, NEW_SESSION)

#: Синонимы, которые модель может написать вместо канонического слова.
#: Ключи — в нижнем регистре; разбор ответа не должен падать из-за слова.
_ALIASES: dict[str, str] = {
    "продолжать": CONTINUE,
    "продолжить": CONTINUE,
    "продолжай": CONTINUE,
    "продолжение": CONTINUE,
    "continue": CONTINUE,
    "work": CONTINUE,
    "завершать": FINISH,
    "завершить": FINISH,
    "заверши": FINISH,
    "закрывать": FINISH,
    "закрыть": FINISH,
    "finish": FINISH,
    "close": FINISH,
    "новая_сессия": NEW_SESSION,
    "новая сессия": NEW_SESSION,
    "новую_сессию": NEW_SESSION,
    "новой_сессии": NEW_SESSION,
    "перенос": NEW_SESSION,
    "перенести": NEW_SESSION,
    "rotate": NEW_SESSION,
    "new_session": NEW_SESSION,
}

#: Системный промпт решения. Правила здесь — **канон промта #172**, а не
#: украшение: именно их проверяет приёмка.
SYSTEM_PROMPT = """\
Ты — «Управляющий» программы «МиниГен». Ты решаешь, что делать с сессией, которая
застряла или которой грозит переполнение контекста.

Отвечай ТОЛЬКО одним JSON-объектом, без пояснений и без обрамления:
{"action": "<одно из трёх>", "instruction": "<что делать обслуживаемому агенту>", "reason": "<почему такое решение>"}

Ровно одно из трёх действий:
- "продолжать" — сессию не закрывать и не архивировать; если она стоит, мягко
  подтолкнуть её продолжить работу по нитке;
- "завершать" — сессию можно закрыть: есть отчёт о выполнении либо работа явно
  не нужна;
- "новая_сессия" — перенести нитку в новую сессию: паспорт нитки уже снят, старую
  сессию корректно закрыть и архивировать, новую открыть и отдать ей паспорт.

Правила (они обязательны):
1. Отсутствие отчёта о выполнении — НЕ повод требовать завершения. Если отчёта
   нет и работа не выглядит ненужной, выбирай "продолжать" или "новая_сессия".
2. "instruction" обязана называть, что именно делать дальше — из паспорта нитки
   (что осталось, в какой ветке/worktree работать, какие критерии приёмки), а не
   просто «продолжай».
3. Работа идёт в ветке/worktree своей нитки. Чужие деревья, ветки и изменения не
   трогать: если границы пересеклись — второй ждёт merge первого и ребейзит
   (ADR-0011: один пишущий агент — одно дерево).
4. Опирайся только на факты из запроса. Не выдумывай то, чего в фактах нет.
5. ⚠️ ПРОМТ КАРМАНА #184: решение обязано опираться на
   «вывод последнего сообщения сессии» — это текст, которым сессия закончила.
   Если там сказано, что работа закончена («готово», «сводка», «закрыто»,
   «итог»), — выбирай "завершать" или "новая_сессия", а НЕ "продолжать".
   Если там сказано, что работа не закончена или что ждут владельца, —
   "продолжать". В "reason" назови словами, на что именно ты посмотрелся.
"""

#: Как показывать факты в тексте запроса (порядок = порядок чтения человеком).
_FACT_ORDER = (
    ("verdict", "вердикт сторожа"),
    ("reason", "почему такой вердикт"),
    ("has_report", "отчёт о выполнении"),
    ("report_kind", "вид отчёта"),
    # ⚠️ Промт #184, п. 4: вывод сессии — не украшение, а **основа решения**:
    # он идёт раньше технических фактов, чтобы модель читала его первой.
    ("last_output", "вывод последнего сообщения сессии"),
    ("last_text", "начало вывода сессии"),
    ("session_closed", "сессия закрыта (работа закончена)"),
    ("compaction", "событие сжатия контекста"),
    ("context", "заполнение контекста"),
    ("last_turn", "последний ход"),
    ("thread", "нить"),
    ("parallel_sessions", "параллельные сессии того же репозитория"),
    ("rules", "правила"),
    ("notes", "замечания"),
)

#: Сколько знаков вывода сессии показываем модели целиком.
OUTPUT_LIMIT = 4000


def _one_line(value: Any, limit: int = 600) -> str:
    """Значение факта — в одну строку (читается и в ответе, и в журнале)."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _block(value: Any, limit: int = OUTPUT_LIMIT) -> str:
    """Вывод сессии — блоком, со строками как есть.

    ⚠️ Промт #184, п. 4: вывод сессии читает **человек** (и модель) — сплющивать
    его в одну строку нельзя: там структура («Итог», «что осталось», пункты).
    """
    text = value if isinstance(value, str) else str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n… (дальше вывод обрезан)"
    return text


def user_prompt(facts: dict[str, Any]) -> str:
    """Запрос к модели: факты о ситуации (без простыни логов)."""
    lines = ["СИТУАЦИЯ (факты):"]
    for key, title in _FACT_ORDER:
        if key not in facts:
            continue
        value = facts.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "last_output":
            # Вывод сессии — отдельным блоком, чтобы его было видно и
            # прочитать глазами.
            lines.append("- %s:" % title)
            lines.append(_block(value))
            lines.append("")
            continue
        lines.append("- %s: %s" % (title, _one_line(value, 1200)))
    # Всё, чего нет в списке, тоже показываем — факты не должны молча теряться.
    for key in sorted(facts):
        if key in dict(_FACT_ORDER):
            continue
        value = facts.get(key)
        if value in (None, "", [], {}):
            continue
        lines.append("- %s: %s" % (key, _one_line(value, 1200)))
    lines.append("")
    lines.append("Дай решение по правилам. Ответ — только JSON-объект.")
    return "\n".join(lines)


def normalize_action(value: Any) -> str:
    """Каноническое действие из ответа модели (пусто — не узнали)."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("«", "").replace("»", "").strip(" .,:;")
    if text in ACTIONS:
        return text
    if text in _ALIASES:
        return _ALIASES[text]
    # «действие: новая сессия» / «action=продолжать» — берём последнее слово.
    for part in re.split(r"[\s:=—-]+", text):
        part = part.strip(" .,:;\"'")
        if part in _ALIASES:
            return _ALIASES[part]
    for name, canon in _ALIASES.items():
        if name in text:
            return canon
    return ""


def _json_candidates(text: str) -> list[str]:
    """Кандидаты на JSON-объект в ответе модели."""
    out: list[str] = []
    stripped = re.sub(r"```(?:json)?", " ", text or "")
    stripped = stripped.replace("```", " ")
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        out.append(stripped[start:end + 1])
    out.append(stripped)
    return out


def parse_decision(text: str) -> dict[str, Any]:
    """Разобрать ответ модели в решение.

    Возвращает `{"ok": True, "action": ..., "instruction": ..., "reason": ...}`
    либо `{"ok": False, "why": ...}` — тогда сторож применяет правило-фолбэк.
    """
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "why": "модель ответила пустотой"}
    data: dict[str, Any] | None = None
    for candidate in _json_candidates(raw):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            data = parsed
            break
    if data is None:
        # Ответ не JSON: попробовать узнать действие по словам. Инструкции в
        # таком ответе нет — её подставит паспорт нитки (сторож).
        # ⚠️ Берём **первое** употребление действия по тексту: в разборе
        # («Действие: новая_сессия, инструкция: продолжай нитку») первым идёт
        # именно действие, а не слово из инструкции.
        action = ""
        best: tuple[int, str] | None = None
        for name, canon in _ALIASES.items():
            match = re.search(r"(?i)\b%s\b" % re.escape(name), raw)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), canon)
        if best:
            action = best[1]
        if not action:
            return {"ok": False, "why": "ответ модели не разобран (не JSON и не действие)"}
        return {"ok": True, "action": action, "instruction": "", "reason": "",
                "raw": raw[:600], "loose": True}
    action = normalize_action(data.get("action") or data.get("решение")
                              or data.get("действие") or "")
    if not action:
        return {"ok": False, "why": "в ответе модели нет одного из трёх действий",
                "raw": raw[:600]}
    instruction = str(data.get("instruction") or data.get("инструкция")
                      or data.get("команда") or "").strip()
    reason = str(data.get("reason") or data.get("причина")
                 or data.get("why") or "").strip()
    return {"ok": True, "action": action, "instruction": instruction,
            "reason": reason, "raw": raw[:600]}
