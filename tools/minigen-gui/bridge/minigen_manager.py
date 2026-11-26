"""Решение о лечении сессии принимает встроенная нейросеть «Управляющий» (промт #172).

Задача (зачитка владельца 26.11.2026): сторож сессий больше **не решает сам**,
что делать с застрявшей сессией. Решение принимает встроенная в программу
модель — та же, что отвечает в чате. На вход идут **факты** о ситуации (а не
простыня логов), на выход — **ровно одно из трёх действий**:

- `continue` («продолжать») — сессию не трогать; если она стоит — мягко
  подтолкнуть («продолжи работу по нитке»);
- `finish` («завершать») — закрывать можно (есть отчёт о выполнении либо
  работа явно не нужна);
- `rotate` («новая сессия с продолжением нитки») — перенос нитки с паспортом.

Плюс к действию: **текст инструкции обслуживаемому агенту** (что именно
делать) и **причина решения**. Решение и причина обязаны быть видны в
отчёте/журнале — это делает сторож (`minimax-sessionwatch.py`), который
вызывает программу через локальный API `:8097`.

⚠️ Почему это отдельный модуль, а не `Agent.ask()`: решение лечения должно
быть **дешёвым и предсказуемым** — один запрос к модели, без совета
специалистов и без инструментов (инструменты тут опасны: решение не должно
ничего менять в системе). Но модель — **та же встроенная** (тот же
`POST {baseURL}/messages` в Anthropic-совместимом формате, тот же конфиг
провайдера, тот же токен области).

Границы:
- модуль **не решает** ничего сам: он только спрашивает модель и разбирает её
  ответ; если модель недоступна или ответила не по схеме — возвращает
  `ok: False`, и решение принимает **правило-фолбэк** на стороне сторожа
  (см. `minimax-sessionwatch.py`);
- секретов здесь нет и быть не должно (D-038): в запрос уходят только факты
  о сессии.
"""

from __future__ import annotations

import json
import re
from typing import Any

import minigen_agent as agent

#: Три действия — и только три (промт #172).
ACTION_CONTINUE = "continue"
ACTION_FINISH = "finish"
ACTION_ROTATE = "rotate"
ACTIONS = (ACTION_CONTINUE, ACTION_FINISH, ACTION_ROTATE)

#: Как действие выглядит по-русски (для отчётов и журнала).
ACTION_LABELS = {
    ACTION_CONTINUE: "продолжать",
    ACTION_FINISH: "завершать",
    ACTION_ROTATE: "новая сессия с продолжением нитки",
}

#: Слова, которыми модель может назвать действие (разбор ответа не по схеме).
#:
#: ⚠️ Порядок важен: более **специфичные** слова идут первыми. Иначе
#: «новая сессия с продолжением нитки» попала бы в «продолжать» (слово
#: «продолжением» содержит «продолж»).
ACTION_WORDS: list[tuple[str, str]] = [
    (r"новая сессия", ACTION_ROTATE),
    (r"новую сессию", ACTION_ROTATE),
    (r"новой сесси", ACTION_ROTATE),
    (r"перенос", ACTION_ROTATE),
    (r"ротац", ACTION_ROTATE),
    (r"rotate", ACTION_ROTATE),
    (r"заверш", ACTION_FINISH),
    (r"закры", ACTION_FINISH),
    (r"finish", ACTION_FINISH),
    (r"продолж", ACTION_CONTINUE),
    (r"continue", ACTION_CONTINUE),
]

#: Сколько ждём ответа модели (сек). Решение — не чат: дольше не нужно.
DECIDE_TIMEOUT = 90

#: Сколько фактов отдаём модели (символов). Факты — не простыня логов.
FACTS_LIMIT = 6000


SYSTEM = """\
Ты — «Управляющий»: встроенная нейросеть программы «МиниГен». Ты решаешь, \
что делать с сессией агента, который застрял.

Ты получаешь ФАКТЫ о ситуации (вердикт сторожа, есть ли отчёт о выполнении, \
событие сжатия контекста, заполнение контекста, когда был последний ход, \
репозиторий/ветка/worktree нитки, параллельные сессии того же репозитория, \
что делалось и что осталось). Ты НЕ получаешь сырые логи.

Верни РОВНО ОДНО из трёх действий:

1. "continue" («продолжать») — сессию не трогать. Если она стоит — мягко \
подтолкнуть её инструкцией «продолжи работу по нитке». Это действие по \
умолчанию: отчёт о выполнении НЕ нужен, чтобы продолжить.
2. "finish" («завершать») — сессию можно закрыть. Выбирай его, когда есть \
отчёт о выполнении (работа закончена) либо работа явно не нужна.
3. "rotate" («новая сессия с продолжением нитки») — перенести нитку: новая \
сессия продолжает СВОЮ ветку/worktree и получает паспорт нитки. Выбирай его, \
когда сессия не отвечает, контекст сжат или нить надо продолжить в новой \
сессии.

ЖЁСТКИЕ ПРАВИЛА:
- Отсутствие отчёта о выполнении — НЕ повод требовать завершения. Никогда не \
выбирай "finish" только потому, что отчёта нет.
- Никогда не требуй от сессии «заверши работу правильно / напечатай сводку», \
если отчёта о выполнении нет и никто не просил закрыть нитку.
- Если в одном репозитории работают другие сессии (параллельные нитки) — \
никогда не предлагай трогать чужие ветки/деревья. Продолжение идёт в своей \
ветке/worktree; при пересечении — ждать merge и ребейзить.
- Инструкция обслуживаемому агенту должна называть, ЧТО делать дальше \
(что осталось, в какой ветке), а не просто «продолжай».
- "finish_old": нужно ли сначала корректно завершить старую сессию (только \
для действия "rotate"; по умолчанию — false, если отчёта о выполнении нет).

ОТВЕТ — строго JSON, без пояснений вне JSON:
{"action": "continue|finish|rotate",
 "instruction": "текст инструкции обслуживаемому агенту",
 "reason": "почему именно это решение (1-2 фразы)",
 "finish_old": false}
"""


# ────────────────────────────────────────────────────────── разбор ответа

def _json_candidates(text: str) -> list[str]:
    """Кандидаты на JSON-объект из текста ответа модели."""
    out: list[str] = []
    raw = (text or "").strip()
    if not raw:
        return out
    # 1) весь ответ, если это объект
    out.append(raw)
    # 2) куски из ```- fences
    for block in re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.S):
        block = block.strip()
        if block:
            out.append(block)
    # 3) первый {...} блок (скобки считаем по глубине)
    depth = 0
    start = -1
    for idx, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(raw[start:idx + 1])
                start = -1
    return out


def normalize_action(value: Any) -> str:
    """Имя действия по ответу модели (русский, английский, синонимы)."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in ACTIONS:
        return text
    for word, action in ACTION_WORDS:
        if word in text:
            return action
    return ""


def parse_decision(text: str) -> dict[str, Any]:
    """Разобрать ответ модели в решение.

    Возвращает `{"action": ..., "instruction": ..., "reason": ..., "finish_old":
    bool}`. Если действие определить не удалось — `action` пустой: значит,
    ответ не по схеме и решает **правило-фолбэк** (а не мы).
    """
    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        action = normalize_action(data.get("action") or data.get("действие"))
        if not action:
            continue
        instruction = str(data.get("instruction") or data.get("инструкция") or "").strip()
        reason = str(data.get("reason") or data.get("причина") or "").strip()
        finish_old = bool(data.get("finish_old") or data.get("finishOld"))
        return {"action": action, "instruction": instruction,
                "reason": reason, "finish_old": finish_old}

    # Ответ без JSON: пробуем вытащить действие из текста. Инструкция и причина
    # — из помеченных строк («Инструкция: …», «Причина: …»), если они есть.
    raw = (text or "").strip()
    if raw:
        action = ""
        for word, act in ACTION_WORDS:
            if re.search(word, raw, flags=re.I):
                action = act
                break
        if action:
            instruction = _labeled(raw, ("инструкция", "instruction"))
            reason = _labeled(raw, ("причина", "reason", "потому что"))
            return {"action": action, "instruction": instruction,
                    "reason": reason, "finish_old": False}
    return {"action": "", "instruction": "", "reason": "", "finish_old": False}


def _labeled(text: str, labels: tuple[str, ...]) -> str:
    """Значение после «Метка: …» (до следующей метки или конца)."""
    for label in labels:
        m = re.search(r"(?im)^\s*(?:\*\*)?%s(?:\*\*)?\s*[:—-]\s*(.+)$" % re.escape(label),
                      text)
        if m:
            return m.group(1).strip()
    return ""


# ────────────────────────────────────────────────────────── факты для модели

def facts_text(facts: dict[str, Any]) -> str:
    """Факты о ситуации — коротким текстом (не простыня логов)."""
    facts = facts or {}
    lines: list[str] = []

    def put(label: str, value: Any) -> None:
        if value is None or value == "" or value == []:
            return
        lines.append("%s: %s" % (label, value))

    put("Область", facts.get("area"))
    put("Сессия", facts.get("session_id"))
    put("Заголовок", facts.get("title"))
    put("Вердикт сторожа", facts.get("verdict"))
    put("Причина вердикта", facts.get("reason"))
    put("Статус хода", facts.get("turn_status"))
    report = facts.get("has_report")
    put("Отчёт о выполнении", "есть" if report else "НЕТ")
    if facts.get("report_excerpt"):
        put("Выдержка из отчёта", facts.get("report_excerpt"))
    put("Событие сжатия контекста",
        "было %s" % facts.get("compacted_at") if facts.get("compacted") else "не было")
    if facts.get("context_fill") is not None:
        put("Заполнение контекста, %%", facts.get("context_fill"))
    if facts.get("peak_fill") is not None:
        put("Пик заполнения контекста, %%", facts.get("peak_fill"))
    if facts.get("last_turn_at"):
        put("Последний ход", facts.get("last_turn_at"))
    if facts.get("idle_minutes") is not None:
        put("Простой, мин", facts.get("idle_minutes"))
    put("Репозиторий", facts.get("repo"))
    put("Ветка", facts.get("branch"))
    put("Worktree", facts.get("worktree"))
    put("Открытые PR", facts.get("prs"))
    put("Что делалось", facts.get("done"))
    put("Что осталось", facts.get("todo"))
    put("Критерии приёмки", facts.get("acceptance"))
    put("Файлы нитки", facts.get("files"))
    parallel = facts.get("parallel_sessions") or []
    if parallel:
        lines.append("Параллельные сессии того же репозитория (их не трогать):")
        for item in parallel:
            if isinstance(item, dict):
                lines.append("  - %s | ветка %s | %s | обновлена %s"
                             % (item.get("session_id") or "?",
                                item.get("branch") or "?",
                                item.get("worktree") or "?",
                                item.get("updated_at") or "?"))
            else:
                lines.append("  - %s" % item)
    if facts.get("note"):
        put("Примечание", facts.get("note"))
    text = "\n".join(lines)
    if len(text) > FACTS_LIMIT:
        text = text[:FACTS_LIMIT] + "\n… (факты обрезаны)"
    return text


# ────────────────────────────────────────────────────────── запрос к модели

def provider_config(area_slug: str, token: str = "") -> dict[str, Any]:
    """Конфиг запроса к встроенной модели области (с её токеном, если он есть).

    Ключ из `config.yaml` — заглушка; настоящая авторизация — токен области
    (`Bearer <accessToken>`), его и кладём в `token`.
    """
    cfg = agent.provider_config(area_slug)
    if token:
        cfg["token"] = token
    return cfg


def decide(facts: dict[str, Any], cfg: dict[str, Any] | None = None,
           area_slug: str = "minigen", timeout: int = DECIDE_TIMEOUT) -> dict[str, Any]:
    """Спросить встроенную модель и разобрать её решение.

    Возвращает:
    - `{"ok": True, "action": ..., "instruction": ..., "reason": ...,
      "finish_old": bool, "source": "model", "raw": "<ответ модели>"}`;
    - `{"ok": False, "error": "...", "raw": ...}` — модель недоступна или
      ответила не по схеме ⇒ решение принимает правило-фолбэк сторожа.
    """
    text = facts_text(facts)
    user = ("Факты о сессии, которую надо вылечить. Реши и ответь строго JSON.\n\n"
            "%s" % text)
    messages = [{"role": "user", "content": user}]
    try:
        conf = dict(cfg) if cfg else agent.provider_config(area_slug)
    except Exception as exc:  # noqa: BLE001 — конфига нет ⇒ модель недоступна
        return {"ok": False, "error": "нет конфига провайдера: %s" % exc,
                "source": "model", "raw": ""}
    conf.setdefault("max_output", 4096)
    try:
        data = agent.chat_completion(messages, SYSTEM, [], conf, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — вендор недоступен/ошибка
        return {"ok": False, "error": str(exc), "source": "model", "raw": ""}
    try:
        _, raw, _ = agent.parse_message(data)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "ответ не разобран: %s" % exc,
                "source": "model", "raw": ""}
    parsed = parse_decision(raw)
    if not parsed["action"]:
        return {"ok": False, "error": "ответ не по схеме (действие не распознано)",
                "source": "model", "raw": raw}
    return {"ok": True, "action": parsed["action"],
            "instruction": parsed["instruction"], "reason": parsed["reason"],
            "finish_old": parsed["finish_old"], "source": "model", "raw": raw}
