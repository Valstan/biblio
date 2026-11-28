"""МиниГен — сторож сессий: понимает, что с сессиями происходит, и оживляет те,
которые встали. Плюс — ротация сессии, когда её контекст забит.

Задача (заказ владельца 21.11.2026), дословно:
  * «определять, что работа в сессии реально остановлена, например выполнена
    работа по закрытию сессии и напечатана окончательная сводка работы сессии,
    то тогда продолжать ее не надо принудительно»;
  * «если интернет нормальный позволяет им работать а они все равно не
    запустились сами то помогать им оживиться, продолжить работу»;
  * «отслеживать переполнение контекста сессии, ну пример достигла уровня 95%
    наполнения, то давать сессии команду закрытия сессии, чтобы она ее корректно
    завершила, а потом дождавшись закрытия сессии, архивировать сессию и открыть
    новую сессию и продолжить работу нитки разработки».

Что делает демон (раз в `interval_sec`, по умолчанию 3 минуты):

1. **Читает базу рантайма каждой ЗАПУЩЕННОЙ области** (`v2/sqlite/runtime-state.sqlite`)
   и разбирает сессии на виды:
   - `active` — ход идёт, сообщения идут; не трогаем;
   - `stuck` — ход идёт, но сообщений нет давно ⇒ зависший ход (диагноз);
   - `balance` — отказ провайдера `50110` (кончились очки) ⇒ это лечит
     `minimax-autheal.py`, мы не лезем (иначе два лекателя жгут пул);
   - `network` — упала из-за сети ⇒ ждём восстановления сети (сторож сети
     пишет метку времени) и шлём «Продолжай»;
   - `finished` — работа реально закончена (последний ход завершён, напечатана
     итоговая сводка, оборванных субагентов нет) ⇒ **не продолжаем принудительно**;
   - `stalled` — ход завершён, но итоговой сводки нет ⇒ нитка брошена ⇒ оживляем;
   - `waits_owner` — сессия ждёт решения владельца ⇒ не будим (это не наша работа);
   - `context_full` — контекст заполнен ≥ порога ⇒ ротация сессии.
2. **Оживляет** (`stalled`, `network` после восстановления сети): отправляет
   в сессию осторожное «Продолжай» — с явным правилом «если работа уже
   закончена, подтверди и жди; если нужен выбор владельца — перечисли
   варианты и жди». Агент видит свой контекст и сам знает, что он сделал:
   поэтому принудительного «второго дна» не происходит.
3. **Лечит застрявшие нитки — по решению «Управляющего»** (промт #172, 26.11):
   решение принимает **встроенная нейросеть программы** (тот же агент, что
   отвечает в чате): на вход — **факты** о ситуации, на выход — ровно одно из
   трёх действий (`продолжать` / `завершать` / `новая_сессия` с продолжением
   нитки), текст инструкции обслуживаемому агенту и причина.
   - **продолжать** — сессию не закрывать и не архивировать; если стоит —
     мягко подтолкнуть («продолжи работу по нитке» — с тем, что делать);
   - **завершать** — закрывать можно (есть отчёт о выполнении либо решил
     «Управляющий»);
   - **новая_сессия** — перенос нитки: **паспорт нитки** (что делалось, что
     осталось, репозиторий, ветка/worktree, PR, критерии приёмки) → корректно
     завершить старую сессию → архивировать → открыть новую и отдать ей
     паспорт + «продолжи нитку».
   ⚠️ **Отсутствие отчёта о выполнении — не повод требовать завершения**
   (раньше здесь стоял сброс на месте с командой «заверши работу правильно…»,
   и нитка обрывалась на полпути).
   ⚠️ Модель недоступна ⇒ **предсказуемое правило-фолбэк**: продолжить либо
   отложить до следующего прохода (завершать без отчёта оно не требует).
4. **Различает параллельные нитки одного репозитория** (ADR-0011): в паспорте
   нитки — репозиторий, ветка, worktree, файлы нитки и пометка «чужие деревья
   не трогать»; если ветка нитки слита или удалена — паспорт это говорит.
5. **Даёт CLI для «Управляющего»** — сессии, которая сама читает чужие чаты,
   отвечает в них, решает по движению разработки и открывает новые нитки:
       python minimax-sessionwatch.py --list
       python minimax-sessionwatch.py --read <область> <session_id>
       python minimax-sessionwatch.py --send <область> <session_id> "текст"
       python minimax-sessionwatch.py --close <область> <session_id>
       python minimax-sessionwatch.py --new <область> "задача"
       python minimax-sessionwatch.py --rotate <область> <session_id>

Границы:
- **незапущенные области не трогаем** (то же правило, что у лечащего);
- **готовую работу не продолжаем** — это главный заказ владельца;
- **отказ по очкам не лечим здесь** — это лечащий, иначе пул Кармана выжигается;
- **архивирование — точечный UPDATE одной строки**, не переписывание базы.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minigen_core as core  # noqa: E402
import minigen_thread as thread  # noqa: E402

BALANCE_CODE = 50110
# Код провайдера «соединение с моделью оборвалось» (21.11: `50113`,
# error_message «Connection error.»). Это сеть, а не аккаунт.
NET_CODE = 50113
# Признаки сетевого сбоя в тексте ошибки рантайма.
NET_MARKERS = ("fetch failed", "econnrefused", "econnreset", "etimedout",
                "enotfound", "ehostunreach", "enetunreach", "socket hang up",
                "network error", "network request failed", "connection error",
                "connection closed", "not available in your country",
                "unauthorized", "forbidden", "request timed out", "dns")
FINAL_MARKERS = ("готово", "закрыто", "завершено", "сделано", "итог", "сводка",
                 "работа завершена", "закрытие сессии", "финальная сводка",
                 "готово.", "закрыто.")
# Признаки сбоя ВЕНДОРА (не сети и не аккаунта) — 23.11: в десктопе это
# «The model service is unstable. Please try again.» с кнопкой Retry.
# В базе рантайма такой ход остаётся `aborted`/`failed` без кода ошибки.
VENDOR_MARKERS = ("the model service is unstable", "model service is unstable",
                  "service unavailable", "service_unavailable", "upstream error",
                  "overloaded", "too many requests", "internal server error",
                  "bad gateway", "gateway timeout", "model unavailable",
                  "the model is unstable", "please try again")
# Служебные сессии-пробы лечащего (`mcode exec --cwd … ping`): это не работа
# владельца, их не показываем и не «оживляем» — иначе будем будить пробы.
PROBE_TITLES = ("ping", "пинг", "probe")
PROBE_TEXTS = ("ping", "пинг", "probe")
# ⚠️ ПРОМТ КАРМАНА #174 (26.11): «MANAGER_AREAS = ("brain2",)» больше не
# существует. Раньше правило «один Управляющий на область» было зашито на одну
# область и не выключалось. Теперь это **переключатель проекта** (`driver` в
# конфиге областей) + правило по умолчанию: если в области работает Управляющий
# (живая сессия с маркером «ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — …»), водит он, а автомат
# управления сессиями только наблюдает. Механика — в `minigen_core`
# (`driver_of`, `live_manager_session`, `live_sessions`).
RECOVERY_WAIT = 900      # после восстановления сети ждём 15 минут перед «Продолжай»
REVIVE_COOLDOWN = 900    # не чаще одного «Продолжай» на сессию за 15 минут
#: ⚠️ ПРОМТ КАРМАНА #177 (п. 7): зависшая сессия (ход идёт, а сообщений
#: нет) — это «задание не выполнено, но нитка встала». Встроенная нейросеть
#: решает, что с ней делать, а «продолжать» — это **попытка продолжения**.
#: Попытки не должны повторяться бесконечно ⇒ предел:
STUCK_ATTEMPT_LIMIT = 3      # столько попыток продолжения на одну зависшую сессию
STUCK_REACTION_WAIT = 600    # сколько ждём реакцию агента на попытку (10 минут)
CONTEXT_LIMIT = 95       # верхняя граница: процент заполнения контекста, после которого ротация
CONTEXT_ROTATE = 60      # backstop: НИЖЕ минимальной наблюдённой точки сжатия.
                          # ⚠️ Точка сжатия не выражается стабильным процентом (замер 22.11
                          #  по всем областям: 345k–835k токенов ⇒ 68–84 % при окне 1 000 000),
                          #  поэтому процентный порог между сессиями не переносится, а 70 %
                          #  был бы ХУЖЕ минимальной точки сжатия и не сработал бы как
                          #  страховка. Основной триггер — СОБЫТИЕ компактизации
                          #  (context_compaction_checkpoint_generated), процент — только
                          #  страховка снизу [G680].
COMPACT_MARK = "context_compaction_checkpoint_generated"   # событие рантайма о сжатии
COMPACT_WINDOW_MS = 6 * 3600 * 1000   # окно, в котором сжатие считается свежим
CLOSE_TIMEOUT = 900       # сколько ждём корректного закрытия сессии, сек
# ⚠️ Сброс сессии НА МЕСТЕ (заказ владельца 24.11): не открываем новую
# сессию, а в этой же — правильно завершаем, чистим контекст и продолжаем.
RESET_COOLDOWN = 7200     # 2 ч: после сброса ту же сессию не трогаем
                          # (пик заполнения остаётся высоким — иначе цикл)
DECISION_COOLDOWN = 7200  # 2 ч: решение лечения нельзя спрашивать на каждом
                          # проходе сторожа (это тратит очки аккаунта) —
                          # спрашиваем только когда вердикт требует действия
                          # (промт #172, «Риски и открытые вопросы»).
COMPACTION_TRIGGER = 0.9  # порог сжатия контекста рантайма (доля окна)
                          # ⚠️ Постоянный (решение владельца 24.11, промт
                          # #96 в разделе MiniGen): держать 0.9 ВСЕГДА, а не
                          # только на время сброса. Рантайм сам сожмёт
                          # контекст на 90 % окна, поэтому «чистка» сброса на
                          # месте от порога не зависит: сброс лишь приводит
                          # конфиг области к этому значению (обычно он уже
                          # такой ⇒ changed: false, «уже так»).
COMPACTION_AREA = "minigen"  # решение владельца касается ТОЛЬКО этой области
                          # ⚠️ Писать порог во все области нельзя: сброс на
                          # месте идёт по любой области, и запись уехала бы в
                          # чужие конфиги (у brain2 свой порог 0.5, у остальных
                          # — дефолт рантайма ~95 %). В чужих областях сброс
                          # конфиг НЕ трогает: «очистку» делает сам рантайм —
                          # сессия уже перешла порог, сжатие случится само.

# ───────────────────────────────────────────── кто есть кто (промт #184)
# ⚠️ ПРОМТ КАРМАНА #184, п. 2: «надзиратель» — слово двусмысленное, и его
# путают. В программе **три** разных механизма, и у каждого своё имя — эти
# имена и стоят в коде, в журнале и в отчёте:
#   * автомат аккаунтов — берёт новый аккаунт из Кармана, перелогинивает
#     агента; в сессию НЕ пишет ни одного сообщения;
#   * сторож сессий — обходит сессии, ставит диагноз, оживляет и **исполняет**
#     решение;
#   * «Управляющий» — встроенная нейросеть программы: **принимает** решение.
# Отдельная роль — «смотритель демонов» (он поднимает и следит за демонами);
# он к сессиям отношения не имеет (слово «надзиратель» для него больше не
# употребляется — путаница снимается).
ROLE_ACCOUNTS = "автомат аккаунтов"
ROLE_WATCHDOG = "сторож сессий"
ROLE_MANAGER = "Управляющий"
ROLE_RULE = "правило-фолбэк"
#: Строка в журнале: команду в закрытую сессию не отправляли (промт #184, п. 3).
CLOSED_SESSION_NOTE = "сессия уже закрыта — команда не отправлялась"
#: Строка в журнале: та же команда уже уходила в эту сессию (промт #184, п. 3).
REPEAT_NOTE = "та же команда уже отправлялась в эту сессию — не отправлялась"
#: Сколько сессий держим в лекарке команд (файл на машине не должен расти вечно).
LEDGER_MAX_SESSIONS = 2000


# ────────────────────────────────────────────────────────── чтение рантайма

def sessions_of(area_dir: Path) -> list:
    """Сессии области с их состоянием."""
    con = core.connect(core.area_db(area_dir))
    if not con:
        return []
    out = []
    try:
        for r in core.rows(con, "select session_id, status, archived, session_kind, title, "
                                  "workspace_dir, created_at_ms, updated_at_ms, error_code, "
                                  "error_message, extra_data_json from local_runtime_sessions "
                                  "order by updated_at_ms desc"):
            out.append({
                "session_id": r[0], "status": r[1], "archived": bool(r[2]),
                "kind": r[3], "title": r[4] or "", "workspace_dir": r[5] or "",
                "created_at_ms": r[6] or 0, "updated_at_ms": r[7] or 0,
                "error_code": r[8], "error_message": r[9] or "",
                "extra": json.loads(r[10] or "{}"),
            })
    finally:
        con.close()
    return out


def last_turn(con, session_id: str) -> dict:
    r = core.rows(con, "select turn_id, status, accepted_at_ms, completed_at_ms, input_json "
                       "from local_runtime_turn_ingress where session_id=? "
                       "order by accepted_at_ms desc limit 1", (session_id,))
    if not r:
        return {}
    return {"turn_id": r[0][0], "status": r[0][1], "accepted_at_ms": r[0][2] or 0,
            "completed_at_ms": r[0][3] or 0, "input_json": r[0][4] or "{}"}


def terminal_outcome(con, session_id: str) -> str:
    r = core.rows(con, "select terminal_outcome from local_runtime_session_agent_state "
                       "where session_id=? order by updated_at_ms desc limit 1", (session_id,))
    return (r[0][0] or "") if r else ""


def last_message(con, session_id: str, final_only: bool = False) -> dict:
    """Последнее сообщение ассистента. `msg_type=1` — финальный ответ хода."""
    sql = ("select id, role, created_at_ms, data_json from local_runtime_message_rows "
           "where session_id=? and role='assistant'")
    args = [session_id]
    if final_only:
        sql += " and data_json like '%\"msg_type\":1%'"
    sql += " order by id desc limit 1"
    r = core.rows(con, sql, tuple(args))
    if not r:
        return {}
    try:
        data = json.loads(r[0][3] or "{}")
    except ValueError:
        return {}
    return {"id": r[0][0], "at": r[0][2] or 0, "msg_type": data.get("msg_type"),
            "text": data.get("msg_content") or "", "turn_id": data.get("turnId") or ""}


def context_fill(con, session_id: str, extra: dict) -> dict:
    """Заполнение контекста сессии в процентах.

    Считаем по последнему ходу: `input + output + cache_read` — это ровно то,
    что рантайм показывает как `totalTokens` (проверено на живых данных).
    Окно берём из `effectiveModelContextWindow` (у MiniMax-M3.1 — 1 000 000).
    """
    win = int(extra.get("effectiveModelContextWindow") or 0)
    r = core.rows(con, "select input_tokens, output_tokens, cache_read_tokens, reasoning_tokens "
                       "from local_runtime_token_usage where session_id=? order by ts desc limit 1",
                  (session_id,))
    if not r:
        return {"fill": 0.0, "tokens": 0, "window": win}
    inp = int(r[0][0] or 0)
    out = int(r[0][1] or 0)
    cache = int(r[0][2] or 0)
    total = inp + out + cache
    fill = (100.0 * total / win) if win else 0.0
    return {"fill": round(fill, 1), "tokens": total, "window": win,
            "input": inp, "output": out, "cache_read": cache}


def broken_subagents(con, session_id: str = "", max_age_ms: int = 24 * 3600 * 1000) -> list:
    """Оборванные субагенты **этой** сессии: работа пропала, её надо поднять.

    ⚠️ 22.11: счёт шёл по всем задачам области — включая старые и чужие ⇒ живая
    сессия объявлялась «незаконченной» из-за субагента, который оборвался вчера
    в другой нитке (на Мозга пришло лишнее «Продолжай»). Теперь — только задачи
    этой сессии и только за последние сутки.
    """
    out = []
    sql = ("select record_json, created_at_ms from local_runtime_background_tasks "
           "where kind='subagent' and status in ('failed','canceled','aborted','lost')")
    args = []
    if session_id:
        sql += " and owner_session_id = ?"
        args.append(session_id)
    for rec in core.rows(con, sql, tuple(args)):
        if max_age_ms and rec[1] and (core.now_ms() - int(rec[1])) > max_age_ms:
            continue
        try:
            task = json.loads(rec[0] or "{}")
        except ValueError:
            continue
        child = (task.get("metadata") or {}).get("childSessionId")
        if not child:
            continue
        row = core.rows(con, "select status from local_runtime_turn_ingress where session_id=? "
                              "order by accepted_at_ms desc limit 1", (child,))
        if row and row[0][0] == "completed":
            continue
        out.append({"task_id": task.get("taskId"),
                    "description": (task.get("description") or "")[:100]})
    return out


def looks_finished(text: str) -> bool:
    """Итоговая сводка? Ключевые слова закрытия в начале финального сообщения."""
    low = (text or "").strip().lower()
    if not low:
        return False
    head = low[:400]
    return any(m in head for m in FINAL_MARKERS)


def asks_owner(text: str) -> bool:
    """Ждёт ли сессия решения владельца (короткий вопрос без ответа)."""
    low = (text or "").strip()
    if not low:
        return False
    if len(low) > 1200:
        return False
    return bool(re.search(r"(?i)(нужен ваш выбор|нужно ваше решение|жду ответа|вопрос к вам|"
                           r"как вам|выберите|нужно подтверждение|подтвердите)", low))


# ──────────────────────────────────────── состояние сессии «прямо сейчас»

def session_state(area: dict, session_id: str) -> dict:
    """Состояние сессии **прямо сейчас** — читаем базу рантайма заново.

    ⚠️ ПРОМТ КАРМАНА #184, п. 3: «в закрытую (завершённую) сессию команда не
    отправляется». Значит, перед отправкой состояние надо **перечитать**: вердикт,
    посчитанный минуту назад на проходе сторожа, мог устареть — агент как раз
    мог закончить работу между вердиктом и отправкой.

    Сессия считается **закрытой**, если:
      - она архивирована (работа закрыта по существу), либо
      - её последний ход завершён, и на этот ход у агента есть **финальный
        ответ** (`msg_type=1`) — агент высказался последним словом.

    Не закрыта: ход ещё идёт (`accepted`), либо ход завершён **без** финального
    ответа (обрыв — нить брошена, её можно оживлять).
    """
    out = {"session_id": session_id, "found": False, "archived": False,
           "status": "", "turn_status": "", "turn_id": "", "running": False,
           "has_final": False, "closed": False, "why": ""}
    if not session_id:
        out["why"] = "нет id сессии"
        return out
    con = core.connect(core.area_db(area["dir"]))
    if not con:
        out["why"] = "база рантайма недоступна"
        return out
    try:
        row = core.rows(con, "select status, archived from local_runtime_sessions "
                              "where session_id=?", (session_id,))
        if not row:
            out["why"] = "сессии нет в базе рантайма"
            return out
        out["found"] = True
        out["status"] = row[0][0] or ""
        out["archived"] = bool(row[0][1])
        turn = last_turn(con, session_id)
        out["turn_status"] = turn.get("status") or ""
        out["turn_id"] = turn.get("turn_id") or ""
        out["running"] = bool(turn.get("status") == "accepted"
                              and not turn.get("completed_at_ms"))
        # Финальный ответ — **текущего** хода (как в `classify`): ответ из
        # прошлого хода сводкой этого хода не считается.
        legacy_final = last_message(con, session_id, final_only=True)
        turn_id = turn.get("turn_id") or ""
        final_msg = legacy_final if (not legacy_final.get("turn_id")
                                     or legacy_final.get("turn_id") == turn_id) else {}
        out["has_final"] = bool(final_msg)
        if out["archived"]:
            out["closed"] = True
            out["why"] = "сессия архивирована"
        elif out["running"]:
            out["why"] = "ход идёт"
        elif out["has_final"]:
            out["closed"] = True
            out["why"] = "ход завершён, на него есть финальный ответ"
        else:
            out["why"] = "ход завершён, финального ответа нет (нить брошена)"
    finally:
        con.close()
    return out


def situation_token(verdict: dict) -> str:
    """Отпечаток ситуации сессии — «изменилось ли что-то».

    ⚠️ ПРОМТ КАРМАНА #184, п. 7: решение лечения не переспрашивается на
    каждом проходе (оно тратит очки аккаунта), но если ситуация изменилась —
    принимается новое. «Изменилась» значит: другой вердикт, другой ход,
    другая закрытость сессии.
    """
    lt = verdict.get("last_turn") or {}
    return "|".join(str(x) for x in (
        verdict.get("verdict"), lt.get("status"), lt.get("completed_at_ms"),
        verdict.get("turn_id"), bool(verdict.get("has_report")),
        bool(verdict.get("closed")),
    ))


# ────────────────────────────────────────────────────────── вердикт

def compacted_sessions(area_dir: Path, window_ms: int = COMPACT_WINDOW_MS) -> dict:
    """Сессии, которые рантайм недавно СЖАЛ (компактизация контекста).

    Источник — журнал рантайма области (`v2/observability/logs/runtime-*.log`):
    `context_compaction_checkpoint_generated` и `beforeLlmCall replaced context
    messages`.

    Почему это важно: после сжатия метрика заполнения падает до ~10 %, и сторож
    по «последнему ходу» никогда не увидит переполнения ⇒ сессия остаётся
    затушенной (а в замере 22.11 сводки не осталось вовсе: `summary_length: 0`).
    Сжатие надо ловить **событием**, а не процентом.
    """
    out = {}
    log_dir = area_dir / "v2" / "observability" / "logs"
    if not log_dir.is_dir():
        return out
    try:
        files = sorted(log_dir.glob("runtime-*.log"))[-3:]
    except OSError:
        return out
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in raw.splitlines():
            if COMPACT_MARK not in line:
                continue
            m = re.search(r'"session_id":\s*"([^"]+)"', line)
            ts = re.search(r"\[(\d\d):(\d\d):(\d\d)", line)
            if not m:
                continue
            at = 0
            if ts:
                try:
                    import datetime as _dt
                    hh, mm, ss = (int(x) for x in ts.groups())
                    now = core.now()
                    at = int(now.replace(hour=hh, minute=mm, second=ss,
                                         microsecond=0).timestamp() * 1000)
                except Exception:  # noqa: BLE001
                    at = 0
            sid = m.group(1)
            if at and (core.now_ms() - at) > window_ms:
                continue
            out[sid] = max(out.get(sid, 0), at)
    return out


def context_peak(con, session_id: str, window_ms: int = 24 * 3600 * 1000) -> dict:
    """Пик заполнения контекста сессии (максимум по ходам за сутки).

    ⚠️ Нужен именно **пик**, а не последний ход: после компактизации рантайма
    последний ход показывает ~10 %, и заполнение кажется нормальным — а сессия
    уже была большой (замер 22.11: было 68 %, стало 10 %).
    """
    since = core.now_ms() - window_ms
    r = core.rows(con, "select max(input_tokens + output_tokens + cache_read_tokens) "
                       "from local_runtime_token_usage where session_id=? and ts>=?",
                  (session_id, since))
    return {"peak_tokens": int(r[0][0] or 0) if r else 0}


def classify(area: dict, sess: dict, con, compacted: dict = None) -> dict:
    """Вердикт по сессии: что с ней и надо ли что-то делать."""
    sid = sess["session_id"]
    compacted = compacted or {}
    turn = last_turn(con, sid)
    outcome = terminal_outcome(con, sid)
    last_msg = last_message(con, sid)
    # Финальный ответ хода — по метке САМОГО рантайма: assistant с msg_type=1 —
    # это финальный ответ хода, и он привязан к turnId. Ответ из ПРОШЛОГО хода
    # сводкой этого хода не считаем (22.11: ложное «Продолжай» в живую сессию —
    # отчёт без слов «готово/закрыто» считался брошенной ниткой).
    legacy_final = last_message(con, sid, final_only=True)
    turn_id = turn.get("turn_id") or ""
    final_msg = legacy_final if (not legacy_final.get("turn_id")
                                  or legacy_final.get("turn_id") == turn_id) else {}
    fill = context_fill(con, sid, sess.get("extra") or {})
    kids = broken_subagents(con, sid)

    # ⚠️ ПРОМТ #172 (26.11): «отчёт о выполнении» и «отчёта нет» — разные
    # вещи, и различие обязано быть видно в вердикте. Отчёт — это ЛИБО
    # итоговая сводка (слова «готово/закрыто/итог…» в финальном ответе
    # рантайма), ЛИБО сам финальный ответ рантайма (`msg_type=1`) — оба
    # случая владельца зафиксированы в коде (решение владельца из промта
    # #172: «зафиксируй оба случая»).
    report_kind = ""
    if final_msg:
        report_kind = ("итоговая сводка" if looks_finished(final_msg.get("text"))
                       else "финальный ответ рантайма")

    v = {
        "session_id": sid, "title": sess["title"][:70], "status": sess["status"],
        "archived": sess["archived"], "kind": sess["kind"], "verdict": "непонятно",
        "reason": "", "fill": fill.get("fill"), "turn_status": turn.get("status"),
        "updated_at_ms": sess["updated_at_ms"], "broken_kids": len(kids),
        "last_text": (last_msg.get("text") or "")[:400],
        # ⚠️ ПРОМТ КАРМАНА #184, п. 4: управляющий должен видеть **текст
        # последнего сообщения сессии** целиком, а не первые 400 знаков:
        # решение принимается по выводу агента («работа закончена» и т. п.).
        "last_output": (last_msg.get("text") or "")[:4000],
        "last_msg_id": last_msg.get("id") or 0,
        "turn_id": turn.get("turn_id") or "",
        # закрыта ли сессия (работа закончена) — по состоянию на момент
        # вердикта; перед отправкой состояние перечитывается заново.
        # Правило то же, что в `session_state()`: ход не идёт, и на него есть
        # финальный ответ ⇒ агент высказался последним словом.
        "closed": bool(sess["archived"]
                       or (turn.get("status") != "accepted"
                           and bool(final_msg))),
        # рабочая папка **этой** сессии: у параллельных ниток одного
        # репозитория она своя (свой worktree) — её и надо продолжать
        "workspace_dir": sess.get("workspace_dir") or "",
        # отчёт о выполнении (промт #172): без него завершать сессию нельзя
        "has_report": bool(report_kind), "report_kind": report_kind,
        "last_turn": {"status": turn.get("status"),
                      "accepted_at_ms": turn.get("accepted_at_ms"),
                      "completed_at_ms": turn.get("completed_at_ms"),
                      "idle_min": round(max(0, core.now_ms()
                                             - (last_msg.get("at") or 0)) / 60000.0, 1)},
    }
    if sess["archived"]:
        v["verdict"] = "готова"
        v["reason"] = "сессия архивирована (работа закрыта)"
        return v
    if sess["kind"] != "conversation":
        return v                      # задачи и субагенты — не наши разговоры
    if is_probe(con, sess):
        v["verdict"] = "проба"
        v["reason"] = "служебная сессия-проба лечащего"
        return v
    # ⚠️ ПРОМТ #174 (п. 4): сессия «Управляющего» (заголовок вида
    # «ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — …») — автомат её не оживляет, не сбрасывает
    # и не ротирует. Отдельный вердикт — видно и в окне, и в отчёте.
    if core.is_manager_title(sess.get("title") or ""):
        v["verdict"] = "управляющий"
        v["reason"] = "сессия Управляющего — автомат её не трогает"
        v["is_manager"] = True
        return v

    # 1) Отказ провайдера по очкам — лечит лечащий, мы не лезем.
    if sess["error_code"] == BALANCE_CODE or "insufficient balance" in (sess["error_message"] or "").lower():
        v["verdict"] = "кончились очки"
        v["reason"] = "отказ провайдера 50110 — лечит лечащий"
        return v

    # 2) Ход идёт — работа жива (или зависла, если сообщений нет).
    if turn.get("status") == "accepted" and not turn.get("completed_at_ms"):
        idle_ms = core.now_ms() - (last_msg.get("at") or sess["updated_at_ms"] or core.now_ms())
        if idle_ms > 120000:
            v["verdict"] = "ход завис"
            v["reason"] = "ход идёт, но сообщений нет %d с" % (idle_ms // 1000)
        else:
            v["verdict"] = "работает"
            v["reason"] = "ход идёт"
        return v

    # 3) Упавший ход: сеть, вендор или нет?
    if (turn.get("status") in ("failed", "aborted")
            or sess["status"] in ("error", "aborted")
            or outcome in ("failed", "aborted")):
        blob = ("%s %s" % (sess["error_message"] or "", turn.get("input_json") or "")).lower()
        net = any(m in blob for m in NET_MARKERS) or sess["error_code"] == NET_CODE
        v["reason"] = "ход упал: %s" % ((sess["error_message"] or "без текста")[:120])
        if net:
            v["verdict"] = "упала по сети"
            return v
        # ⚠️ «Вендор недоступен» (23.11). В десктопе это выглядит как
        # «The model service is unstable. Please try again.» с кнопкой Retry —
        # вендор отвечает 5xx/перегрузом. В базе рантайма такой ход остаётся
        # `aborted`/`failed` БЕЗ кода ошибки, и раньше он попадал в «ошибка»/
        # «непонятно» ⇒ никто его не продолжал, а область стояла мёртвой.
        #
        # Лечение здесь НЕ «новый аккаунт» и НЕ «перезапуск десктопа»:
        # вендор чинит себя (сетевой сторож), а сессию надо просто продолжить.
        if any(m in blob for m in VENDOR_MARKERS) or not sess["error_code"]:
            v["verdict"] = "вендор недоступен"
            v["reason"] = ("ход упал без кода ошибки (%s) — похоже на «модель нестабильна»: "
                           "ждём восстановления вендора и продолжаем сессию"
                           % ((sess["error_message"] or "без текста")[:100]))
            return v
        v["verdict"] = "ошибка"
        return v

    # 4) Ход завершён — смотрим, закончена ли работа.
    if turn.get("status") == "completed" or outcome == "completed":
        if compacted.get(sid):
            v["verdict"] = "скомпактилась"
            v["reason"] = ("рантайм сжал контекст (%s) — сессия дальше «тупеет», "
                           "нужно корректно закрыть и открыть новую" % COMPACT_MARK)
            v["compaction"] = {"event_at": compacted.get(sid), "marker": COMPACT_MARK}
            return v
        peak = context_peak(con, sid)
        # Окно контекста — из context_fill() (там оно и считается);
        # раньше здесь был NameError: демон падал на каждом проходе.
        win = int(fill.get("window") or 0)
        peak_fill = (100.0 * peak.get("peak_tokens", 0) / win) if win else 0.0
        if peak_fill >= CONTEXT_ROTATE or fill.get("fill", 0) >= CONTEXT_LIMIT:
            v["verdict"] = "контекст заполнен"
            v["reason"] = "пик %s%% контекста (сейчас %s%%)" % (
                round(peak_fill, 1), fill.get("fill"))
            v["peak_fill"] = round(peak_fill, 1)
            v["context"] = {"fill": fill.get("fill"), "peak_fill": round(peak_fill, 1),
                             "window": win, "tokens": peak.get("peak_tokens", 0)}
            return v
        # ⚠️ ПОРЯДОК ПРАВИЛ (22.11, находка Управляющего): «итоговая сводка»
        # проверяется ПЕРВОЙ. D-249 ⑤ (владелец: «работу реально законченную
        # продолжать не надо принудительно») сильнее D-230 (будить сессии с
        # оборванными субагентами): сессия со сводкой НЕ оживает, а её сироты —
        # забота Управляющего (task_append), а не «Продолжай» в закрытую сессию.
        if final_msg and looks_finished(final_msg.get("text")):
            v["verdict"] = "готова"
            v["reason"] = ("последний ход завершён, напечатана итоговая сводка"
                           + (" · есть %d оборванных субагентов — их поднимает "
                              "Управляющий (task_append)" % len(kids) if kids else ""))
            return v
        if asks_owner(last_msg.get("text")):
            v["verdict"] = "ждёт владельца"
            v["reason"] = "последнее сообщение — вопрос владельцу"
            return v
        # 22.11 (находка Управляющего): ход завершён, рантайм пометил его
        # финальным ответом (msg_type=1), сирот нет ⇒ работа закончена. Раньше
        # тут был только путь «сводка по ключевым словам» ⇒ обычный отчёт без
        # слов «готово/закрыто» считался брошенной ниткой, и сторож слал
        # «Продолжай» в живую сессию (ложное пробуждение 13:51).
        if final_msg and not kids:
            v["verdict"] = "готова"
            v["reason"] = "ход завершён, у него есть финальный ответ — работа закончена"
            return v
        if kids:
            v["verdict"] = "есть оборванные субагенты"
            v["reason"] = "%d оборванных субагентов — работа не закончена" % len(kids)
            return v
        v["verdict"] = "брошена"
        v["reason"] = "ход завершён, но финального ответа нет (агент оборвался)"
        return v

    v["reason"] = "нет ходов"
    return v


def is_probe(con, sess: dict) -> bool:
    """Проба лечащего — служебная сессия, а не работа владельца.

    Два признака: заголовок («ping») и первая реплика пользователя («ping»).
    Одного заголовка мало: пробу переименовали — и по заголовку она проходит
    как обычная сессия (и ротация её закрывает). У пробы первая реплика всегда
    ровно «ping» (`mcode exec --cwd … ping`).
    """
    if (sess.get("title") or "").strip().lower() in PROBE_TITLES:
        return True
    if con is None:
        return False
    r = core.rows(con, "select data_json from local_runtime_message_rows "
                       "where session_id=? and role='user' order by id asc limit 1",
                  (sess.get("session_id") or "",))
    if not r:
        return False
    try:
        data = json.loads(r[0][0] or "{}")
    except ValueError:
        return False
    return (data.get("msg_content") or "").strip().lower() in PROBE_TEXTS


def probe_of(area: dict, sess: dict) -> bool:
    """Проба ли это — без уже открытого соединения с базой."""
    con = core.connect(core.area_db(area["dir"]))
    try:
        return is_probe(con, sess)
    finally:
        if con:
            con.close()


# ────────────────────────────────────────────────────────── действия

def live_manager_session(area: dict) -> str:
    """Живая сессия Управляющего в области (id или пусто).

    ⚠️ ПРОМТ #174: раньше здесь было «любая живая сессия в зашитой области
    MANAGER_AREAS». Теперь это **живая сессия с маркером Управляющего** в
    заголовке («ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — …») — по любой области. Смысл тот
    же (G679: один Управляющий на область), но правило стало переключателем,
    а не зашитой константой.
    """
    return core.live_manager_session(area)


def command_signature(text: str, tag: str = "") -> str:
    """Отпечаток команды: текст + «ситуация» (тег вызывающего).

    ⚠️ Промт #184, п. 3: «повтор одной и той же команды в одной сессии
    невозможен». Отпечаток включает тег ситуации, поэтому честная повторная
    отправка (другая попытка, другая смена аккаунта, другая причина) —
    это **другой** отпечаток и проходит, а слепой повтор — нет.
    """
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]
    return "%s|%s" % (tag or "", digest)


def sends_ledger() -> dict:
    """Лекарка команд: сессия → отпечаток команды → когда уходила."""
    return core.read_state(core.SESSION_SENDS_STATE)


def remember_send(session_id: str, signature: str, text: str) -> None:
    """Записать в лекарку: эта команда в эту сессию уже уходила."""
    ledger = sends_ledger()
    rec = ledger.setdefault(session_id, {})
    rec[signature] = {"at": core.now_ms(), "text": (text or "")[:200]}
    # Файл не должен расти вечно: держим последние сессии (по времени последней
    # команды). Старые сессии всё равно закрыты ⇒ их прикрывает стража
    # «сессия уже закрыта».
    if len(ledger) > LEDGER_MAX_SESSIONS:
        def last_at(item) -> int:
            stamps = [int(v.get("at") or 0) for v in (item or {}).values()
                      if isinstance(v, dict)]
            return max(stamps) if stamps else 0
        for old in sorted(ledger, key=last_at)[:len(ledger) - LEDGER_MAX_SESSIONS]:
            ledger.pop(old, None)
    core.write_state(core.SESSION_SENDS_STATE, ledger)


def send_to_session(area: dict, sess: dict, text: str, background: bool = True,
                     force: bool = False, tag: str = "", why: str = "") -> dict:
    """Отправить сообщение в СУЩЕСТВУЮЩУЮ сессию области.

    `mcode exec --session <id>` работает в контексте области (через
    MINIMAX_DATA_DIR) — иначе CLI не найдёт ни сессию, ни авторизацию.

    Перед отправкой перечитываем прокси-переменные из реестра: если сетевой
    сторож починил мёртвый прокси, демон держит старые значения, и ход снова
    упал бы с «Connection error» ([G]: `fetch failed` = мёртвый прокси).

    ⚠️ ПРОМТ КАРМАНА #184, п. 3 — **две стражи перед отправкой**:
      1. **сессия уже закрыта** (работа закончена / архивирована) ⇒ команда
         не отправляется: в закрытую сессию команда бессмысленна, и именно
         этим «МиниГен слал „Продолжай“ и „заверши сессию“ десятки раз подряд»
         (живой случай 27–28.11: 27 команд закрытия подряд в одну сессию);
      2. **та же команда уже уходила** в эту сессию при той же ситуации ⇒
         повтор невозможен (лекарка команд — общая для всех, кто пишет в
         сессию: и сторож, и карусель полос, и панель).
    Обе стражи пишут в журнал словами — видно, что команда **не** ушла.
    """
    core.refresh_env()
    if not force and probe_of(area, sess):
        # Пробу не будим: сообщение в пробу = лишний ход и лишние очки.
        return {"ok": False, "why": "это проба лечащего — сообщение не отправлено"}
    if not force and core.is_manager_title(sess.get("title") or ""):
        # ⚠️ ПРОМТ #174 (п. 4): сессию Управляющего автомат не трогает — не
        # оживляет, не сбрасывает, не ротирует (как служебные пробы).
        # `force=True` — это ручная команда владельца/«Управляющего», она
        # право имеет.
        return {"ok": False, "why": "сессия Управляющего — автомат её не трогает"}
    signature = command_signature(text, tag)
    if not force:
        # 1) Сессия уже закрыта? — состояние перечитываем **прямо сейчас**.
        st = session_state(area, sess.get("session_id") or "")
        if st.get("closed"):
            note = "%s (%s)" % (CLOSED_SESSION_NOTE, st.get("why") or "")
            core.log("СЕССИИ: %s — %s" % (str(sess.get("session_id") or "")[:12], note))
            core.report("info", "session", "%s: %s — %s"
                        % (area.get("slug") or "", str(sess.get("session_id") or "")[:12],
                           note), area=str(area.get("slug") or ""))
            return {"ok": False, "why": note, "closed": True}
        # 2) Та же команда уже была в этой сессии при той же ситуации?
        prev = (sends_ledger().get(sess.get("session_id") or "") or {}).get(signature)
        if prev:
            note = "%s (%s)" % (REPEAT_NOTE, why or "ситуация не изменилась")
            core.log("СЕССИИ: %s — %s" % (str(sess.get("session_id") or "")[:12], note))
            core.report("info", "session", "%s: %s — %s"
                        % (area.get("slug") or "", str(sess.get("session_id") or "")[:12],
                           note), area=str(area.get("slug") or ""))
            return {"ok": False, "why": note, "repeat": True}
    folder = sess.get("workspace_dir") or area.get("folder") or ""
    if not folder or not Path(folder).is_dir():
        return {"ok": False, "why": "папка проекта недоступна: %s" % folder}
    logfile = None
    if background:
        logs = core.LOCAL / "sessionwatch-sends.log"
        logs.parent.mkdir(parents=True, exist_ok=True)
        logfile = open(logs, "a", encoding="utf-8")
    try:
        proc = core.mcode_run(["exec", "--session", sess["session_id"], "--cwd", folder, text],
                               area["dir"], background=background, logfile=logfile)
    finally:
        if logfile is not None:
            logfile.close()
    if background:
        time.sleep(3)
        if proc.poll() is None:
            remember_send(sess["session_id"], signature, text)
            return {"ok": True, "why": "ход пошёл (pid %s)" % proc.pid}
        return {"ok": False, "why": "процесс умер сразу (rc=%s)" % proc.returncode}
    out = ""
    if proc is not None:
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    ok = bool(proc and proc.returncode == 0)
    if ok:
        remember_send(sess["session_id"], signature, text)
    return {"ok": ok, "why": out[-400:]}


def new_session(area: dict, prompt: str, force: bool = False,
                folder: str = "", by_decision: bool = False) -> dict:
    """Открыть НОВУЮ сессию в области с начальной командой.

    ⚠️ `folder` — рабочая папка **нитки**: у параллельных ниток одного
    репозитория она своя (свой worktree), и новая сессия обязана продолжить
    именно её ветку (ADR-0011: один пишущий агент — одно дерево).

    ⚠️ ПРОМТ #174 (п. 3): **вторая сессия не поднимается, пока в проекте живая
    сессия** (своя или чужая). Живая = рантайм занят: идёт ход или сессия в
    состоянии «started» (`core.live_sessions`). Порядок при переносе нитки
    обязателен: сначала архив старой, потом новая (это делает
    `apply_decision`, ветка «новая_сессия»).

    Исключение — **только решение «Управляющего»** «новая сессия с
    продолжением нитки»: к этому моменту старая сессия уже закрыта и
    **архивирована** (порядок соблюдён), иначе перенос нитки был бы невозможен
    вовсе. Поэтому флаг `by_decision` — только для этого пути.

    `force` оставлен для совместимости вызовов, но запрет второй сессии он НЕ
    обходит: правило «один водитель на проект» не обходится флагом.
    """
    # Прокси-переменные — из реестра (22.11, находка Управляющего): сетевой
    # сторож мог починить мёртвый прокси, а вызывающий процесс держит старые
    # значения ⇒ новый ход упал бы с «Connection error». В send_to_session()
    # это уже есть, здесь — нет.
    core.refresh_env()
    if not by_decision:
        live = core.live_sessions(area)
        if live:
            first = live[0]
            return {"ok": False,
                    "why": "в проекте уже живая сессия %s (%s) — вторая не поднимается: "
                           "сначала завершите и заархивируйте её"
                           % (first["session_id"][:12], (first["title"] or "")[:40])}
    folder = folder or area.get("folder") or ""
    if not folder or not Path(folder).is_dir():
        return {"ok": False, "why": "папка проекта недоступна: %s" % folder}
    logs = core.LOCAL / "sessionwatch-sends.log"
    logs.parent.mkdir(parents=True, exist_ok=True)
    with open(logs, "a", encoding="utf-8") as logfile:
        proc = core.mcode_run(["exec", "--cwd", folder, prompt], area["dir"],
                              background=True, logfile=logfile)
    time.sleep(3)
    if proc.poll() is None:
        return {"ok": True, "why": "новая сессия пошла (pid %s)" % proc.pid}
    return {"ok": False, "why": "процесс умер сразу (rc=%s)" % proc.returncode}


def archive_session(area_dir: Path, session_id: str) -> dict:
    """Архивировать сессию: точечный UPDATE одной строки.

    ⚠️ База живёт под рантаймом ⇒ пишем одной короткой операцией, с
    busy_timeout, и только если строка ещё не архивирована.
    """
    db = core.area_db(area_dir)
    if not db.is_file():
        return {"ok": False, "why": "нет базы"}
    try:
        con = sqlite3.connect(str(db), timeout=15)
    except sqlite3.Error as exc:
        return {"ok": False, "why": "база не открылась: %s" % exc}
    try:
        con.execute("PRAGMA busy_timeout=10000")
        row = con.execute("select record_json, archived from local_runtime_sessions "
                          "where session_id=?", (session_id,)).fetchone()
        if not row:
            return {"ok": False, "why": "сессии нет"}
        if row[1]:
            return {"ok": True, "why": "уже архивирована"}
        try:
            rec = json.loads(row[0] or "{}")
        except ValueError:
            rec = {}
        rec["archived"] = "true" if isinstance(rec.get("archived"), str) else True
        con.execute("update local_runtime_sessions set archived=1, record_json=?, "
                    "updated_at_ms=? where session_id=?",
                    (json.dumps(rec, ensure_ascii=False), core.now_ms(), session_id))
        con.commit()
        return {"ok": True, "why": "архивирована"}
    except sqlite3.Error as exc:
        return {"ok": False, "why": "UPDATE не прошёл: %s" % exc}
    finally:
        con.close()


def wait_closed(area: dict, session_id: str, timeout: int = CLOSE_TIMEOUT) -> bool:
    """Дождаться, пока сессия корректно завершится (статус idle, ход завершён)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        con = core.connect(core.area_db(area["dir"]))
        if con:
            try:
                sess = None
                for s in sessions_of(area["dir"]):
                    if s["session_id"] == session_id:
                        sess = s
                        break
                turn = last_turn(con, session_id)
                final = last_message(con, session_id, final_only=True)
            finally:
                con.close()
            if sess and sess["status"] != "started" and turn.get("status") in ("completed", "failed"):
                if final or turn.get("status") == "completed":
                    return True
        time.sleep(10)
    return False


CLOSE_COMMAND = (
    "Завершай работу в этой сессии правильно:\n"
    "1) сохрани состояние нитки в handoff-файл репозитория (для репозитория Мозг — "
    "команда /close_session; для обычного проекта — запиши, что сделано и что дальше, "
    "в файл состояния нитки);\n"
    "2) напечатай ИТОГОВУЮ СВОДКУ работы сессии: что сделано, что не сделано, что дальше;\n"
    "3) после сводки остановись — эту сессию закрывают, работу продолжит новая сессия."
)


RESET_COMMAND = (
    "Завершай работу в этой сессии правильно:\n"
    "1) сохрани состояние нитки в handoff-файл репозитория (для репозитория Мозг — "
    "команда /close_session; для обычного проекта — запиши, что сделано и что дальше, "
    "в файл состояния нитки);\n"
    "2) напечатай ИТОГОВУЮ СВОДКУ работы сессии: что сделано, что не сделано, что дальше;\n"
    "3) после сводки остановись — эту сессию сейчас очистят и продолжат В НЕЙ ЖЕ."
)


def area_compaction_trigger(area: dict) -> dict:
    """Порог компактизации для области — по решению владельца (24.11, #96).

    Постоянные 0.9 относятся к области `minigen` (COMPACTION_AREA) — её
    конфиг и приводится к этому значению. В чужих областях конфиг **не
    трогаем**: у `brain2` свой порог (0.5), у остальных — дефолт рантайма.
    «Очистка» сброса от порога не зависит: сессия уже перешла порог, и
    рантайм сожмёт контекст сам (при следующем вызове модели).
    """
    if area.get("slug") == COMPACTION_AREA:
        return core.set_compaction_trigger(area["dir"], COMPACTION_TRIGGER)
    return {"ok": True, "changed": False,
            "why": "решение владельца касается области %s — конфиг не трогаем"
                   % COMPACTION_AREA}


def reset_session(area: dict, sess: dict, brief: str = "") -> dict:
    """Сброс сессии НА МЕСТЕ: правильно завершить -> очистить -> начать заново.

    Заказ владельца (24.11): **не начинать новую сессию**, а в этой же —
    1) сессия сама доходит до конца и печатает сводку (нить не теряется);
    2) контекст очищается: рантайм сжимает контекст САМ, внутри этой же
       сессии (порог компактизации постоянный — 0.9 в области `minigen`;
       сброс лишь приводит конфиг этой области к нему, опускать порог больше
       не нужно, а конфиги чужих областей сброс не трогает вовсе);
    3) та же сессия продолжается с командой «продолжи нитку».

    Почему так: новая сессия — это новая нитка с нуля (агент всё перечитывает
    заново), а сброс на месте сохраняет идентичность сессии и её историю.

    ⚠️ Откат: если сброс не удался (сессия не откликнулась) — прежнее
    поведение, ротация (архивирование + новая сессия). Сброс на месте не
    должен быть хуже прежнего.

    ⚠️ ПРОМТ #172 (26.11): это — **ручной** инструмент (кнопка в окне, ключ
    `--reset`). Автоматический путь лечения сессии сюда **больше не ходит**:
    решение принимает «Управляющий», и при отсутствии отчёта о выполнении
    сессия не должна требовать завершения (промт #172, п. 1).
    """
    report = {"session_id": sess["session_id"], "steps": [], "mode": "на месте"}
    if probe_of(area, sess):
        report["ok"] = False
        report["why"] = "это проба лечащего — сброс запрещён"
        core.log("СЕССИИ: сброс запрещён — проба %s" % sess["session_id"][:12])
        return report
    core.log("СЕССИИ: сброс на месте %s (%s)" % (sess["session_id"][:12], sess["title"][:50]))
    core.report("info", "session", "сброс сессии %s на месте: %s"
                % (sess["session_id"][:12], sess["title"][:50]))
    # 0) порог компактизации: постоянный 0.9 — рантайм сожмёт контекст сам,
    #    внутри этой же сессии (новая сессия не понадобится); конфиг области
    #    приводится к этому значению (если он уже такой — «уже так»)
    #    ⚠️ ТОЛЬКО для области minigen: решение владельца (24.11, промт #96)
    #    касается её одной. В чужих областях конфиг не трогаем (у brain2
    #    свой порог 0.5, у остальных — дефолт рантайма).
    trig = area_compaction_trigger(area)
    report["steps"].append({"compaction_trigger": trig})
    # 1) правильно завершить: сводка + handoff (нить не теряется)
    res = send_to_session(area, sess, RESET_COMMAND, background=True, force=True,
                          tag="сброс на месте:%s" % sess["session_id"][:12],
                          why="команда завершения перед сбросом на месте")
    report["steps"].append({"close_command": res})
    if not res.get("ok"):
        report["ok"] = False
        report["why"] = "команду завершения не приняли: %s" % res.get("why")
        return report
    closed = wait_closed(area, sess["session_id"])
    report["steps"].append({"closed": closed})
    if not closed:
        report["ok"] = False
        report["why"] = "сессия не закончила ход за отведённое время"
        return report
    # 2) продолжить ТУ ЖЕ сессию: контекст при этом сожмётся (это и есть
    #    «очистка» — рантайм делает это сам, своими родными средствами)
    text = brief or continuation_brief(area, sess)
    again = send_to_session(area, sess, text, background=True, force=True,
                            tag="сброс на месте:%s" % sess["session_id"][:12],
                            why="продолжение после сброса на месте")
    report["steps"].append({"continued": again})
    report["ok"] = bool(again.get("ok"))
    report["why"] = again.get("why")
    return report


def rotate_session(area: dict, sess: dict, brief: str = "") -> dict:
    """Ротация сессии: корректное закрытие → архивирование → новая сессия.

    Порядок именно такой (заказ владельца 21.11): сначала сессия сама
    доходит до конца и печатает сводку, потом архивируем, потом открываем
    новую с начальной командой — чтобы нитка не потеряла контекст.
    """
    report = {"session_id": sess["session_id"], "steps": []}
    if probe_of(area, sess):
        report["ok"] = False
        report["why"] = "это проба лечащего — ротация запрещена"
        core.log("СЕССИИ: ротация запрещена — проба %s" % sess["session_id"][:12])
        return report
    core.log("СЕССИИ: ротация %s (%s)" % (sess["session_id"][:12], sess["title"][:50]))
    core.report("warn", "session", "ротация сессии %s: %s"
                % (sess["session_id"][:12], sess["title"][:50]))
    res = send_to_session(area, sess, CLOSE_COMMAND, background=True, force=True,
                          tag="ротация:%s" % sess["session_id"][:12],
                          why="команда закрытия перед ротацией")
    report["steps"].append({"close_command": res})
    if not res.get("ok"):
        report["ok"] = False
        report["why"] = "команду закрытия не приняли: %s" % res.get("why")
        return report
    closed = wait_closed(area, sess["session_id"])
    report["steps"].append({"closed": closed})
    if not closed:
        report["ok"] = False
        report["why"] = "сессия не закрылась за отведённое время"
        return report
    arc = archive_session(area["dir"], sess["session_id"])
    report["steps"].append({"archived": arc})
    if not arc.get("ok"):
        report["ok"] = False
        report["why"] = "не архивировалась: %s" % arc.get("why")
        return report
    text = brief or continuation_brief(area, sess)
    opened = new_session(area, text)
    report["steps"].append({"new_session": opened})
    report["ok"] = bool(opened.get("ok"))
    report["why"] = opened.get("why")
    return report


def handoff_of(folder: str) -> tuple:
    """Файл состояния нитки в рабочей папке (путь и текст)."""
    root = Path(folder or "")
    for name in ("docs/SESSION_HANDOFF.md", "docs/HANDOFF.md", "HANDOFF.md"):
        p = root / name
        if p.is_file():
            try:
                return str(p), p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return str(p), ""
    return "", ""


def area_brief_of(folder: str) -> str:
    """Бриф области (`docs/orchestrator/BRIEF.md`) — курс работы «Управляющего»."""
    p = Path(folder or "") / "docs" / "orchestrator" / "BRIEF.md"
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""


def first_user_message(con, session_id: str) -> str:
    """Первая реплика владельца — обычно это и есть задание нитки."""
    if con is None:
        return ""
    r = core.rows(con, "select data_json from local_runtime_message_rows "
                       "where session_id=? and role='user' order by id asc limit 1",
                  (session_id,))
    if not r:
        return ""
    try:
        data = json.loads(r[0][0] or "{}")
    except ValueError:
        return ""
    return (data.get("msg_content") or "").strip()


def session_context(con, sess: dict, folder: str) -> dict:
    """Что известно о нитке из рантайма: задание, сводка, handoff."""
    sid = sess.get("session_id") or ""
    final = last_message(con, sid, final_only=True) if con is not None else {}
    path, text = handoff_of(folder)
    return {
        "task": first_user_message(con, sid),
        "last_final": (final.get("text") or "").strip(),
        "handoff_path": path,
        "handoff": text,
        "area_brief": area_brief_of(folder),
    }


def thread_passport(area: dict, sess: dict, save: bool = False) -> dict:
    """Паспорт нитки: что делалось, что осталось, где работать, что принять.

    Паспорт собирается из рантайма (задание, сводка, handoff) и из git
    (репозиторий, ветка, worktree, файлы нитки, открытые PR). Лежит **на
    диске машины, вне репозитория** (D-038) и переживает перезапуск окна.
    """
    folder = str(sess.get("workspace_dir") or area.get("folder") or "")
    con = core.connect(core.area_db(area["dir"]))
    context: dict = {}
    try:
        context = session_context(con, sess, folder)
    finally:
        if con:
            con.close()
    passport = thread.build_passport(area, sess, context)
    if context.get("area_brief"):
        passport["area_brief"] = context["area_brief"]
    if save:
        thread.save_passport(passport)
    return passport


def continuation_brief(area: dict, sess: dict) -> str:
    """Начальная команда для новой сессии: продолжить нитку, а не начать заново.

    ⚠️ Промт #172: бриф собирается из **паспорта нитки**, а не из последнего
    финального сообщения. Прежний бриф не говорил новой сессии, что осталось
    и в какой ветке работать, — и нитка терялась по-настоящему.
    """
    passport = thread_passport(area, sess)
    parts = [thread.passport_text(passport), ""]
    if passport.get("area_brief"):
        parts += ["Бриф области:", str(passport.get("area_brief")), ""]
    parts += ["Продолжи нитку разработки в новой сессии.",
              "Сначала прочитай паспорт и скажи, что делаешь дальше. "
              "Не начинай новую тему — продолжай ту же нитку."]
    return "\n".join(parts)


def parallel_sessions(area: dict, sess: dict) -> list:
    """Параллельные сессии того же репозитория (ADR-0011).

    В одном репозитории работают несколько ниток (в Матрице РМЗ — три полосы
    в трёх worktree). Продолжение нитки обязано знать о соседях: чужие
    деревья не трогать, при пересечении — ждать merge и ребейзить.
    """
    sid = sess.get("session_id") or ""
    folder = str(sess.get("workspace_dir") or area.get("folder") or "")
    key = thread.repo_key(folder)
    out = []
    try:
        for other in sessions_of(area["dir"]):
            if other["session_id"] == sid or other["archived"]:
                continue
            if other["kind"] != "conversation":
                continue
            other_folder = other.get("workspace_dir") or area.get("folder") or ""
            if thread.repo_key(other_folder) != key:
                continue
            out.append({"session_id": other["session_id"], "title": other["title"][:60],
                        "worktree": other_folder, "status": other["status"]})
    except Exception:  # noqa: BLE001 — соседи не обязательны
        return out
    return out


def treatment_facts(area: dict, sess: dict, verdict: dict, passport: dict,
                    neighbors: list) -> dict:
    """Факты о ситуации — на вход решения «Управляющего» (промт #172).

    На вход идут **факты**, а не простыня логов: вердикт сторожа, есть ли
    отчёт о выполнении, событие сжатия контекста, заполнение контекста,
    когда был последний ход, репозиторий/ветка/worktree нитки, что осталось,
    критерии приёмки, открытые PR и параллельные сессии того же репозитория.

    ⚠️ ПРОМТ КАРМАНА #177 (п. 6): встроенная нейросеть **смотрит на вывод
    сессии** — последнее слово и выводы («выполнила такую-то работу»). Поэтому
    вывод сессии (`last_text`) — часть фактов, а не украшение.
    """
    return {
        "area": str(area.get("slug") or ""),
        "verdict": verdict.get("verdict"),
        "reason": verdict.get("reason"),
        "has_report": bool(verdict.get("has_report")),
        "report_kind": verdict.get("report_kind") or "",
        # вывод сессии — чтобы решение принималось по словам агента
        "last_text": (verdict.get("last_text") or "")[:1500],
        # ⚠️ ПРОМТ КАРМАНА #184, п. 4: **текст последнего сообщения сессии
        # целиком** — решение обязано опираться на него («работа закончена»,
        # «жду владельца», «что осталось»). Раньше в фактах было только первые
        # 400 знаков, а модель их почти не видела ⇒ решение принималось «на
        # фактах», а не по выводу сессии.
        "last_output": (verdict.get("last_output") or "")[:4000],
        "session_closed": bool(verdict.get("closed")),
        "compaction": verdict.get("compaction") or {},
        "context": verdict.get("context") or {"fill": verdict.get("fill"),
                                              "peak_fill": verdict.get("peak_fill")},
        "last_turn": verdict.get("last_turn") or {},
        "thread": {
            "repo": passport.get("repo"),
            "branch": passport.get("branch"),
            "worktree": passport.get("worktree"),
            "prs": passport.get("prs"),
            "files": passport.get("files"),
            "done": passport.get("done"),
            "left": passport.get("left"),
            "criteria": passport.get("criteria"),
            "merged": passport.get("merged"),
            "branch_exists": passport.get("branch_exists"),
        },
        "parallel_sessions": neighbors,
        "rules": list(thread.RULES),
    }


def apply_decision(area: dict, sess: dict, verdict: dict, decision: dict,
                   passport: dict, state: dict) -> dict:
    """Исполнить решение «Управляющего» (промт #172, п. 2–4).

    - **продолжать** — сессию не закрывать и не архивировать; если она стоит,
      мягко подтолкнуть («продолжи работу по нитке» — с тем, что делать);
    - **завершать** — закрыть можно (отчёт о выполнении либо решил
      «Управляющий»);
    - **новая_сессия** — перенос: паспорт → корректно завершить старую →
      архивировать → открыть новую и отдать ей паспорт + «продолжи нитку».

    ⚠️ ПРОМТ КАРМАНА #184, п. 5: **завершённая сессия закрывается правильно**:
    она уже отработала, поэтому на месте её нельзя ни «продолжать» (сессия
    закрыта — команда бессмысленна), ни просто «завершать» (тогда нитка
    умирает вместе с сессией). Значит, решение приводится к **переносу нитки**:
    паспорт → архив → новая сессия с паспортом и «продолжи нитку». Само
    решение (чьё оно) остаётся видно в отчёте.
    """
    action = str(decision.get("action") or "")
    instruction = str(decision.get("instruction") or "")
    report: dict = {"ok": False, "action": action, "steps": [],
                    "source": decision.get("source") or "",
                    "reason": decision.get("reason") or "",
                    "instruction": instruction}
    # ⚠️ Промт КАРМАНА #177 (п. 6): сессия, чья работа **уже завершена**
    # (вердикт «готова» — завершение определено по выводу сессии), не требует
    # команды закрытия: она и так закрыта. Лишняя команда — лишний ход и лишние
    # очки. Проверка «сессия закрылась корректно» при этом остаётся.
    already_done = str((verdict or {}).get("verdict") or "") == "готова"
    # ⚠️ ПРОМТ КАРМАНА #184, п. 5: решение для **завершённой** сессии
    # приводится к переносу нитки. «Продолжать» на месте неисполнимо (сессия
    # закрыта), а «завершать» без переноса убило бы нитку ⇒ перенос.
    if already_done and action in (thread.CONTINUE, thread.FINISH):
        report["action_was"] = action
        report["action"] = thread.NEW_SESSION
        report["escalated"] = (
            "работа уже закончена: решение «%s» на месте неисполнимо — нитка "
            "переносится в новую сессию с паспортом" % action)
        # ⚠️ Промт #184, п. 3: в журнале видно, что в закрытую сессию команда
        # **не отправлялась** (и почему).
        core.log("СЕССИИ: %s — %s (%s)" % (sess["session_id"][:12],
                                           CLOSED_SESSION_NOTE, report["escalated"]))
        core.report("info", "session", "%s: %s — %s"
                    % (area.get("slug") or "", sess["session_id"][:12],
                       CLOSED_SESSION_NOTE), area=str(area.get("slug") or ""))
        action = thread.NEW_SESSION
    if action == thread.CONTINUE:
        # Ничего разрушающего: ни «заверши работу», ни архивирования.
        report["ok"] = True
        report["why"] = "сессию не трогаем (решение: продолжать)"
        cool = int((state.get("revived") or {}).get(sess["session_id"]) or 0)
        if int(time.time()) - cool < REVIVE_COOLDOWN:
            report["steps"].append({"nudge": "недавно уже подталкивали — ждём"})
            return report
        text = thread.continue_text(passport, instruction)
        res = send_to_session(area, sess, text, background=True,
                              tag="продолжение:%s" % situation_token(verdict),
                              why="мягкое «Продолжай» по решению «%s»"
                                  % (decision.get("source") or "правило"))
        state.setdefault("revived", {})[sess["session_id"]] = int(time.time())
        report["steps"].append({"nudge": res})
        report["ok"] = bool(res.get("ok"))
        report["why"] = ("подтолкнули продолжить работу по нитке: %s"
                         % res.get("why")) if res.get("ok") else res.get("why")
        return report
    if action == thread.FINISH:
        # Закрывать можно: отчёт о выполнении есть либо решил «Управляющий».
        # Паспорт снимаем ДО закрытия — нитка не теряется ни при каком исходе.
        thread.save_passport(passport)
        report["steps"].append({"passport": "сохранён"})
        if already_done:
            report["steps"].append({"close_command": "не нужна: работа уже завершена"})
        else:
            res = send_to_session(area, sess, CLOSE_COMMAND, background=True,
                                  tag="закрытие:%s" % situation_token(verdict),
                                  why="команда закрытия по решению «%s»"
                                      % (decision.get("source") or "правило"))
            report["steps"].append({"close_command": res})
            if not res.get("ok"):
                report["why"] = "команду закрытия не приняли: %s" % res.get("why")
                return report
        closed = wait_closed(area, sess["session_id"])
        report["steps"].append({"closed": closed})
        if not closed:
            report["why"] = "сессия не закрылась за отведённое время"
            return report
        arc = archive_session(area["dir"], sess["session_id"])
        report["steps"].append({"archived": arc})
        report["ok"] = bool(arc.get("ok"))
        # ⚠️ Промт #184, п. 2: правда в журнале — «завершено по решению
        # «Управляющего»» писалось даже тогда, когда решение приняло правило-
        # фолбэк. Источник решения — его настоящее имя.
        report["why"] = ("завершено по решению «%s»: %s"
                         % (decision.get("source") or ROLE_RULE, arc.get("why")))
        return report
    if action == thread.NEW_SESSION:
        # Перенос нитки: паспорт → завершение → архив → новая сессия.
        thread.save_passport(passport)
        report["steps"].append({"passport": "сохранён"})
        if already_done:
            report["steps"].append({"close_command": "не нужна: работа уже завершена"})
        else:
            res = send_to_session(area, sess, CLOSE_COMMAND, background=True,
                                  tag="закрытие:%s" % situation_token(verdict),
                                  why="команда закрытия перед переносом нитки")
            report["steps"].append({"close_command": res})
            if not res.get("ok"):
                report["why"] = "команду закрытия не приняли: %s" % res.get("why")
                return report
        closed = wait_closed(area, sess["session_id"])
        report["steps"].append({"closed": closed})
        if not closed:
            report["why"] = "сессия не закрылась за отведённое время"
            return report
        # Паспорт дополняем тем, что сессия напечатала при закрытии, и PR.
        con = core.connect(core.area_db(area["dir"]))
        summary = ""
        try:
            if con:
                final = last_message(con, sess["session_id"], final_only=True)
                summary = (final.get("text") or "").strip()
        finally:
            if con:
                con.close()
        if summary:
            passport["done"] = summary[:4000]
        try:
            thread.enrich_with_prs(passport)
        except Exception:  # noqa: BLE001 — PR не обязательны
            pass
        thread.save_passport(passport)
        arc = archive_session(area["dir"], sess["session_id"])
        report["steps"].append({"archived": arc})
        if not arc.get("ok"):
            report["why"] = "не архивировалась: %s" % arc.get("why")
            return report
        text = thread.passport_text(passport, instruction)
        text += ("\n\nПродолжи нитку разработки в новой сессии: начни с того, что "
                 "осталось (см. паспорт выше), и не начинай новую тему.")
        # ⚠️ Новая сессия открывается в **рабочей папке нитки** (свой
        # worktree), а не в общей папке области: иначе параллельная нитка
        # продолжилась бы в чужом дереве (ADR-0011).
        #
        # ⚠️ ПРОМТ #174 (п. 3): это **единственное исключение** из запрета
        # «вторая сессия не поднимается, пока первая жива» — решение
        # «Управляющего» «новая сессия с продолжением нитки». Старая сессия
        # выше уже закрыта и архивирована, то есть порядок «архив старой →
        # новая» соблюдён. Право на исключение есть только у решения
        # «Управляющего» (правило-фолбэк перенос нитки не выбирает).
        by_decision = str(decision.get("source") or "") == "Управляющий"
        # ⚠️ ПРОМТ КАРМАНА #177 (п. 6): нитка должна **продвигаться**. Если с
        # прошлого переноса нового «сделано» не появилось (та же сводка) —
        # новая сессия не поднимается: иначе нитка крутится вхолостую (сессия
        # за сессией, а работы столько же). Запись — в состоянии сторожа, по
        # нитке (thread_id).
        #
        # ⚠️ ПРОМТ КАРМАНА #184: сравнивать **прозу** сводки ненадёжно — новая
        # сессия ответит «всё уже сделано», сводка изменится, и кружение
        # продолжится. Поэтому сравниваем **состояние работы** (коммиты, PR,
        # «впереди origin/master»): работа не продвинулась — новая сессия не
        # поднимается.
        rot = state.setdefault("rotations", {})
        tid = str(passport.get("thread_id") or "")
        prev = rot.get(tid) or {}
        done_now = summary.strip()
        fingerprint = thread.work_fingerprint(passport)
        prev_fp = str(prev.get("fp") or "")
        same_prose = bool(done_now and done_now == str(prev.get("done") or "").strip())
        same_work = bool(fingerprint and prev_fp and fingerprint == prev_fp)
        if prev and (same_prose or same_work):
            rot[tid] = {"done": done_now, "at": core.now_ms(), "fp": fingerprint}
            report["ok"] = True
            report["why"] = ("нитка не продвинулась с прошлого переноса (%s) — "
                             "новая сессия не поднимается"
                             % ("работа та же" if same_work else "та же сводка"))
            core.log("СЕССИИ: %s — нитка не продвинулась с прошлого переноса: "
                     "новая сессия не поднимается" % sess["session_id"][:12])
            return report
        rot[tid] = {"done": done_now, "at": core.now_ms(), "fp": fingerprint}
        opened = new_session(area, text, folder=str(passport.get("worktree") or ""),
                             by_decision=by_decision)
        report["steps"].append({"new_session": opened})
        report["ok"] = bool(opened.get("ok"))
        report["why"] = ("нитка перенесена в новую сессию с паспортом: %s"
                         % opened.get("why"))
        return report
    report["why"] = "неизвестное действие: %s" % action
    return report


def treat_session(area: dict, sess: dict, verdict: dict, state: dict,
                  decision: dict = None) -> dict:
    """Лечение сессии по решению **встроенной нейросети «Управляющего»** (#172).

    ⚠️ Отсутствие отчёта о выполнении — **не повод требовать завершения**.
    Повод — только отчёт о выполнении либо решение «Управляющего». Во всех
    остальных случаях сессия получает команду **продолжить работу**.

    Решение видно в отчёте: кто (Управляющий / правило-фолбэк), когда, по каким
    фактам, какое действие и что сделано.

    ⚠️ ПРОМТ КАРМАНА #184, п. 2: в отчёте и в журнале видно **кто принял
    решение и кто его исполнил** — эти две роли путают чаще всего:
      * принял: «Управляющий» (встроенная нейросеть) либо «правило-фолбэк»
        (модель недоступна или ответила не по схеме);
      * исполнил: «сторож сессий» (он же — тот, кто ходит в сессию).
    """
    passport = thread_passport(area, sess)
    neighbors = parallel_sessions(area, sess)
    facts = treatment_facts(area, sess, verdict, passport, neighbors)
    # ⚠️ Область модели не задаём: решение принимает **тот же** встроенный
    # агент, что отвечает в чате (у него своя область из настроек программы).
    # ⚠️ Промт #184, п. 7: `decision` можно передать **готовое** — тогда модель
    # не спрашивается (очки аккаунта) и повторяется уже принятое решение.
    if decision is None:
        decision = thread.decide(facts)
    report = apply_decision(area, sess, verdict, decision, passport, state)
    report["facts"] = facts
    report["decision"] = {"action": decision.get("action"),
                           "source": decision.get("source"),
                           "reason": decision.get("reason"),
                           "instruction": decision.get("instruction"),
                           "model_why": decision.get("model_why", "")}
    # ⚠️ Промт #184, п. 2: кто принял решение и кто исполнил — словами.
    source = str(decision.get("source") or "")
    report["decided_by"] = ROLE_MANAGER if source == ROLE_MANAGER else ROLE_RULE
    report["done_by"] = ROLE_WATCHDOG
    report["passport_id"] = passport.get("thread_id")
    # ⚠️ Промт #174 (п. 5): в отчёте видно, **кто водит** проект сейчас и
    # какое действие по чьему решению.
    drv = core.driver_of(area)
    report["driver"] = drv
    core.log("СЕССИИ: лечение %s — %s: решение принял «%s», исполнил «%s», "
             "водитель — %s: %s"
             % (sess["session_id"][:12], decision.get("action"),
                report["decided_by"], report["done_by"], drv["title"],
                report.get("why")))
    core.report("warn" if decision.get("action") != thread.CONTINUE else "info",
                "session", "лечение сессии %s: %s — решение принял «%s», исполнил "
                "«%s», водитель — %s — %s"
                % (sess["session_id"][:12], decision.get("action"),
                   report["decided_by"], report["done_by"], drv["title"],
                   report.get("why")),
                area=str(area.get("slug") or ""))
    return report


REVIVE_PROMPT = (
    "Продолжай нитку.\n"
    "Порядок:\n"
    "1) Сначала посмотри свой контекст: если работа уже закончена и напечатана "
    "итоговая сводка — не начинай заново, просто подтверди, что всё закрыто, и жди.\n"
    "2) Если нужен выбор владельца — перечисли варианты и жди, ничего не решай сам.\n"
    "3) Иначе продолжай работу с места, где остановился.\n"
    "МиниГен поднял тебя после простоя/обрыва связи."
)


# ────────────────────────────────────────────────────────── проход демона

def survey(area: dict, state: dict) -> list:
    """Разбор всех сессий области с вердиктами."""
    con = core.connect(core.area_db(area["dir"]))
    if not con:
        return []
    out = []
    compacted = compacted_sessions(area["dir"])
    try:
        for sess in sessions_of(area["dir"]):
            if sess["kind"] != "conversation":
                continue
            if sess["updated_at_ms"] and core.now_ms() - sess["updated_at_ms"] < 5 * 60 * 1000:
                pass  # свежая сессия — всё равно показываем, но не трогаем
            out.append(classify(area, sess, con, compacted))
    finally:
        con.close()
    return out


def stuck_reaction(v: dict, state: dict) -> str:
    """Реакция агента на попытку продолжить зависшую сессию (#177, п. 7).

    Возвращает "" — реакции пока нет (ждём). Иначе — что получилось:
    очки кончились / работа идёт дальше / работа закончена / реакции нет.

    Реакция читается из **состояния сессии**, а не из наших предположений:
    заговорил агент (вывод изменился) — работа идёт; пришёл отказ по очкам —
    кончились; вердикт «готова» — работа закончена.
    """
    stuck = state.get("stuck") or {}
    rec = stuck.get(v["session_id"]) or {}
    pending = rec.get("pending")
    if not pending:
        return ""
    if v["verdict"] == "ход завис":
        # сессия молчит — реакции ещё нет: ждём, сколько положено
        waited = core.now_ms() - int(pending.get("at") or 0)
        if waited < STUCK_REACTION_WAIT * 1000:
            return ""
        rec["pending"] = None
        stuck[v["session_id"]] = rec
        return ("реакции нет: сессия молчит и дальше (%d с) — ждём следующей попытки"
                % (waited // 1000))
    rec["pending"] = None
    stuck[v["session_id"]] = rec
    if v["verdict"] == "кончились очки":
        return "очки кончились (попытка продолжения вскрыла отказ по очкам)"
    if v["verdict"] == "готова":
        return "работа закончена: сессия закрылась сама после попытки продолжения"
    if (v.get("last_text") or "") != (pending.get("text") or ""):
        return "работа идёт дальше: агент ответил после попытки продолжения"
    return "работа идёт дальше (вердикт: %s)" % v["verdict"]


def act(area: dict, verdicts: list, state: dict, dry_run: bool = False) -> list:
    """Действия по вердиктам: оживление и ротация."""
    actions = []
    slug = area["slug"]
    # ⚠️ ПРОМТ КАРМАНА #174: «жизнь сессии» принадлежит ОДНОМУ водителю.
    # Водитель — либо Управляющий, либо автомат управления сессиями, либо
    # никто. Считаем его один раз на проход (правило по умолчанию смотрит
    # живую сессию Управляющего, переключатель проекта — важнее).
    drv = core.driver_of(area)
    # Пометки «видели отказ по очкам» для сессий, которые уже не в отказе,
    # убираем — иначе они копятся и мешают.
    by_id = {v["session_id"]: v for v in verdicts}
    seen_map = state.setdefault("balance_seen", {})
    for sid in list(seen_map.keys()):
        if (by_id.get(sid) or {}).get("verdict") != "кончились очки":
            seen_map.pop(sid, None)
    for v in verdicts:
        last = int((state.get("revived") or {}).get(v["session_id"]) or 0)
        cool = int(time.time()) - last < REVIVE_COOLDOWN
        if v["verdict"] == "управляющий":
            # Промт #174 (п. 4): сессия Управляющего — не оживляем, не
            # сбрасываем, не ротируем (как служебные пробы).
            actions.append({"session": v["session_id"],
                            "action": "сессия Управляющего — не трогаем"})
            continue
        # ⚠️ ПРОМТ КАРМАНА #177 (п. 7): реакция на попытку продолжения
        # зависшей сессии. Смотрим ДО отсева «готова/работает» — реакция может
        # быть и «работа идёт дальше».
        reaction = stuck_reaction(v, state)
        if reaction:
            actions.append({"session": v["session_id"],
                            "action": "зависшая сессия — %s" % reaction,
                            "driver": drv["driver"], "why": drv["why"]})
            core.log("СЕССИИ: %s — зависшая сессия: %s" % (slug, reaction))
            core.report("info", "session", "%s: зависшая сессия — %s" % (slug, reaction),
                        area=slug)
        if v["verdict"] == "готова" and not v.get("archived"):
            # ⚠️ Промт КАРМАНА #177 (п. 6): завершение работы определяется **по
            # выводу сессии** (последнее слово, выводы «выполнила такую-то
            # работу»). Решение принимает встроенная нейросеть: работа
            # закончена — закрыть, архивировать и поднять новую сессию с
            # продолжением нитки разработки; не закончена — продолжить.
            #
            # ⚠️ Промт КАРМАНА #184, п. 7: решение **не переспрашивается на
            # каждом проходе** (оно тратит очки аккаунта). Спрашиваем, если
            # прошло два часа ИЛИ ситуация изменилась (стала другой вердикт,
            # другой ход, сессия закрылась).
            #
            # Под водителем «Внешнее управление» или «Оператор» автомат в
            # сессии **не вмешивается**: жить сессией водит внешний агент
            # (слова о невыполненном действии пишутся там, где действие
            # действительно было бы, — см. ветки ниже).
            if drv["driver"] != core.DRIVER_AUTOMATON:
                continue
            sig = situation_token(v)
            last = state.setdefault("decision", {}).get(v["session_id"]) or {}
            last_at = int(last.get("at") or 0) if isinstance(last, dict) else int(last or 0)
            last_sig = last.get("sig") or "" if isinstance(last, dict) else ""
            if last_at and int(time.time()) - last_at < DECISION_COOLDOWN and last_sig == sig:
                actions.append({"session": v["session_id"],
                                "action": "решение спрашивали недавно, ситуация не "
                                          "изменилась — не трогаем"})
                continue
            if dry_run:
                actions.append({"session": v["session_id"],
                                "action": "работа завершена — решение «Управляющего» (сухой прогон)"})
                continue
            sess = {"session_id": v["session_id"], "title": v["title"],
                    "workspace_dir": v.get("workspace_dir") or area.get("folder", "")}
            rep = treat_session(area, sess, v, state)
            state["decision"][v["session_id"]] = {"at": int(time.time()), "sig": sig}
            actions.append({"session": v["session_id"],
                            "action": "работа завершена: %s" % (rep.get("action") or "—"),
                            "report": rep})
            continue
        if v["verdict"] in ("готова", "работает", "ждёт владельца"):
            continue
        # Дальше — только решения, которые меняют жизнь сессии. Здесь решает
        # водитель: если это не автомат — действие НЕ выполняется, но решение
        # пишется в отчёт словами (промт #174, п. 2).
        if drv["driver"] != core.DRIVER_AUTOMATON:
            actions.append({"session": v["session_id"],
                            "action": "водитель — %s, действие не выполнялось" % drv["title"],
                            "driver": drv["driver"], "why": drv["why"]})
            core.report("info", "session", "%s: водитель — %s, действие не выполнялось "
                        "(вердикт: %s — %s)" % (slug, drv["title"], v["verdict"],
                                               v.get("reason") or ""), area=slug)
            core.log("СЕССИИ: %s — водитель — %s, действие не выполнялось (%s): %s"
                     % (slug, drv["title"], v["verdict"], v.get("reason") or ""))
            continue
        if v["verdict"] == "ход завис":
            # ⚠️ ПРОМТ КАРМАНА #177 (п. 7): задание не выполнено, а сессия
            # встала (ход идёт, но сообщений нет). Решение принимает
            # **встроенная нейросеть** — она смотрит на вывод сессии; её
            # «продолжать» — это **попытка продолжения**.
            #
            # Попытки ограничены (STUCK_ATTEMPT_LIMIT): иначе «зависшая» нитка
            # получала бы «Продолжай» до бесконечности. Когда попытки
            # исчерпаны — пишем это в журнал и в отчёт и больше не трогаем
            # сессию (решение о её жизни принимает владелец).
            stuck = state.setdefault("stuck", {})
            rec = stuck.get(v["session_id"]) or {}
            attempt = int(rec.get("attempt") or 0)
            why = v.get("reason") or "ход идёт, сообщений нет"
            if attempt >= STUCK_ATTEMPT_LIMIT:
                actions.append({"session": v["session_id"],
                                "action": ("зависшая сессия — попытки исчерпаны (%d), "
                                           "больше не продолжаем" % attempt),
                                "driver": drv["driver"], "why": drv["why"]})
                core.log("СЕССИИ: %s — зависшая сессия: попытки исчерпаны (%d): %s"
                         % (slug, attempt, why))
                core.report("warn", "session",
                            "%s: зависшая сессия — попытки исчерпаны (%d): %s"
                            % (slug, attempt, why), area=slug)
                continue
            if dry_run:
                actions.append({"session": v["session_id"],
                                "action": "зависшая сессия — решение «Управляющего» (сухой прогон)"})
                continue
            # ⚠️ Промт #184, п. 7: решение лечения не переспрашивается на
            # каждом проходе. Для зависшей сессии «продолжать» — это **попытка
            # продолжения**, и её считает счётчик попыток (предел 3), а не
            # модель: значит, модель повторно не спрашиваем, а **повторяем
            # прежнее решение** (оно уже принято и с situations не изменилось).
            last = (state.get("decision") or {}).get(v["session_id"]) or {}
            prev = last.get("decision") if isinstance(last, dict) else None
            sess = {"session_id": v["session_id"], "title": v["title"],
                    "workspace_dir": v.get("workspace_dir") or area.get("folder", "")}
            if prev and prev.get("action") == thread.CONTINUE:
                rep = treat_session(area, sess, v, state, decision=prev)
                core.log("СЕССИИ: %s — зависшая сессия: решение «продолжать» "
                         "повторяем (модель не спрашиваем)" % slug)
            else:
                rep = treat_session(area, sess, v, state)
            decision = (rep.get("decision") or {}).get("action")
            state.setdefault("decision", {})[v["session_id"]] = {
                "at": int(time.time()), "sig": situation_token(v),
                "action": decision or "",
                "decision": (rep.get("decision") or {})}
            if decision == thread.CONTINUE:
                # Попытка продолжения сделана — ждём реакцию агента.
                attempt += 1
                stuck[v["session_id"]] = {
                    "attempt": attempt,
                    "pending": {"at": core.now_ms(), "text": v.get("last_text") or "",
                                "why": why},
                    "at": core.now_ms(),
                }
                core.log("СЕССИИ: %s — зависшая сессия: попытка продолжения %d из %d (%s)"
                         % (slug, attempt, STUCK_ATTEMPT_LIMIT, why))
                core.report("info", "session",
                            "%s: зависшая сессия — попытка продолжения %d из %d (%s)"
                            % (slug, attempt, STUCK_ATTEMPT_LIMIT, why), area=slug)
            else:
                stuck[v["session_id"]] = {"attempt": attempt}
            actions.append({"session": v["session_id"],
                            "action": "зависшая сессия: %s" % (rep.get("action") or "—"),
                            "report": rep})
            continue
        if v["verdict"] == "кончились очки":
            # ⚠️ ПРОМТ #174: аккаунты и сессии — РАЗНЫЕ автоматы. Лечащий
            # (автомат аккаунтов) меняет аккаунт и в сессии не пишет ни одного
            # сообщения; продолжить работу после смены аккаунта — дело здесь,
            # у сторожа сессий. Поэтому ждём, когда лечащий обновит аккаунт
            # (его состояние — `core.AUTHEAL_STATE`), и продолжаем сессию
            # ОДНИМ «Продолжай» на каждую смену аккаунта.
            failed_ms = int((v.get("last_turn") or {}).get("completed_at_ms")
                            or v.get("updated_at_ms") or 0)
            healed_at = int(float((core.read_state(core.AUTHEAL_STATE)
                                   .get("last_heal") or {}).get(slug) or 0) * 1000)
            sent_at = int(seen_map.get(v["session_id"]) or 0)
            if healed_at and healed_at > failed_ms and healed_at > sent_at:
                if cool:
                    actions.append({"session": v["session_id"],
                                    "action": "аккаунт обновлён — ждём кулдаун «Продолжай»"})
                    continue
                if dry_run:
                    actions.append({"session": v["session_id"],
                                    "action": "«Продолжай» после смены аккаунта (сухой прогон)"})
                    continue
                res = send_to_session(area, {"session_id": v["session_id"],
                                      "workspace_dir": area.get("folder", "")},
                                      REVIVE_PROMPT, background=True,
                                      tag="смена аккаунта:%d" % healed_at,
                                      why="«Продолжай» после смены аккаунта "
                                          "(автомат аккаунтов)")
                seen_map[v["session_id"]] = healed_at
                state.setdefault("revived", {})[v["session_id"]] = int(time.time())
                actions.append({"session": v["session_id"],
                                "action": "«Продолжай» после смены аккаунта",
                                "result": res})
                core.log("СЕССИИ: %s — очки кончились, аккаунт обновлён лечащим: %s"
                         % (slug, res.get("why")))
                continue
            actions.append({"session": v["session_id"],
                            "action": "очки кончились — ждём новый аккаунт (автомат аккаунтов)"})
            continue
        if v["verdict"] in ("скомпактилась", "контекст заполнен"):
            # ⚠️ ПРОМТ КАРМАНА #172 (26.11): решение лечения принимает
            # **встроенная нейросеть «Управляющий»** — не жёсткое правило.
            # И главное: **отсутствие отчёта о выполнении — не повод требовать
            # завершения**. Раньше здесь стояло «сброс на месте» (команда
            # «заверши работу правильно… напечатай ИТОГОВУЮ СВОДКУ… остановись»),
            # и нитка обрывалась на полпути.
            if dry_run:
                actions.append({"session": v["session_id"],
                                "action": "решение «Управляющего» (сухой прогон)"})
                continue
            # ⚠️ Промт #184, п. 7: решение не переспрашивается на каждом
            # проходе, но при **изменившейся ситуации** принимается новое.
            sig = situation_token(v)
            last = (state.get("decision") or {}).get(v["session_id"]) or {}
            last_at = int(last.get("at") or 0) if isinstance(last, dict) else int(last or 0)
            last_sig = (last.get("sig") or "") if isinstance(last, dict) else ""
            if last_at and int(time.time()) - last_at < DECISION_COOLDOWN and last_sig == sig:
                actions.append({"session": v["session_id"],
                                "action": "решение спрашивали недавно, ситуация не "
                                          "изменилась — не трогаем"})
                continue
            sess = {"session_id": v["session_id"], "title": v["title"],
                    "workspace_dir": v.get("workspace_dir") or area.get("folder", "")}
            rep = treat_session(area, sess, v, state)
            state.setdefault("decision", {})[v["session_id"]] = {"at": int(time.time()),
                                                                "sig": situation_token(v)}
            actions.append({"session": v["session_id"],
                            "action": "лечение: %s" % (rep.get("action") or "—"),
                            "report": rep})
            core.log("СЕССИИ: %s — лечение: %s (%s): %s" % (
                slug, rep.get("action"), (rep.get("source") or "правило"),
                rep.get("why")))
            continue
        if v["verdict"] == "упала по сети":
            # Поднимаем, когда сеть **сейчас** в порядке (по данным сетевого
            # сторожа) и сессия упала не сейчас (иначе ход ещё качается).
            net = core.read_state(core.NET_STATE).get("last_run") or {}
            if not net.get("stable"):
                actions.append({"session": v["session_id"], "action": "ждём сеть"})
                continue
            failed_at = int(v.get("updated_at_ms") or 0)
            if failed_at and core.now_ms() - failed_at < 120000:
                actions.append({"session": v["session_id"], "action": "ход ещё мог упасть — ждём"})
                continue
            if cool:
                continue
            if dry_run:
                actions.append({"session": v["session_id"], "action": "«Продолжай» после сети (сухой прогон)"})
                continue
            res = send_to_session(area, {"session_id": v["session_id"],
                                  "workspace_dir": area.get("folder", "")},
                                  REVIVE_PROMPT, background=True,
                                  tag="сеть:%d" % failed_at,
                                  why="«Продолжай» после восстановления сети")
            state.setdefault("revived", {})[v["session_id"]] = int(time.time())
            actions.append({"session": v["session_id"], "action": "«Продолжай» после сети", "result": res})
            core.log("СЕССИИ: %s — сессия %s упала по сети, связь вернулась: %s" % (
                slug, v["session_id"][:12], res.get("why")))
            continue
        if v["verdict"] == "вендор недоступен":
            # «The model service is unstable. Please try again.» — это сбой
            # ВЕНДОРА, а не сети-области и не аккаунта. Лечение: дождаться, когда
            # вендор (или путь до него) оживёт, и продолжить сессию «Продолжай».
            # Перезапуск десктопа тут НЕ нужен и вреден: он убивает живые
            # сессии области (23.11 — так «умер» рабочий десктоп РМЗ 2).
            net = core.read_state(core.NET_STATE).get("last_run") or {}
            if not net.get("stable"):
                actions.append({"session": v["session_id"], "action": "ждём вендора/сеть"})
                continue
            failed_at = int(v.get("updated_at_ms") or 0)
            if failed_at and core.now_ms() - failed_at < 120000:
                actions.append({"session": v["session_id"], "action": "ход ещё мог упасть — ждём"})
                continue
            if cool:
                continue
            if dry_run:
                actions.append({"session": v["session_id"], "action": "«Продолжай» после вендора (сухой прогон)"})
                continue
            res = send_to_session(area, {"session_id": v["session_id"],
                                  "workspace_dir": area.get("folder", "")},
                                  REVIVE_PROMPT, background=True,
                                  tag="вендор:%d" % failed_at,
                                  why="«Продолжай» после восстановления вендора")
            state.setdefault("revived", {})[v["session_id"]] = int(time.time())
            actions.append({"session": v["session_id"], "action": "«Продолжай» после вендора", "result": res})
            core.log("СЕССИИ: %s — сессия %s упала на нестабильности модели, вендор ожил: %s" % (
                slug, v["session_id"][:12], res.get("why")))
            continue
        if v["verdict"] in ("брошена", "есть оборванные субагенты"):
            # ⚠️ ПРОМТ #174: вторая сессия в проекте не поднимается, пока
            # живая сессия (своя или чужая). Здесь мы ничего не поднимаем —
            # только оживляем существующую, — но решение «новая сессия» ниже
            # запрет соблюдает (`new_session`).
            # «Ход завис» сюда НЕ входит: он разобран выше отдельной веткой
            # (промт КАРМАНА #177, п. 7) — там решение принимает встроенная
            # нейросеть и делается **попытка продолжения** с пределом попыток.
            if cool:
                continue
            if dry_run:
                actions.append({"session": v["session_id"], "action": "«Продолжай» (сухой прогон)"})
                continue
            res = send_to_session(area, {"session_id": v["session_id"],
                                  "workspace_dir": area.get("folder", "")},
                                  REVIVE_PROMPT, background=True,
                                  tag="оживление:%s" % (v.get("turn_id")
                                                        or v.get("verdict") or ""),
                                  why="«Продолжай» брошенной нитке")
            state.setdefault("revived", {})[v["session_id"]] = int(time.time())
            actions.append({"session": v["session_id"], "action": "«Продолжай»", "result": res})
            core.log("СЕССИИ: %s — сессия %s: %s → «Продолжай»: %s" % (
                slug, v["session_id"][:12], v["verdict"], res.get("why")))
    return actions


def cycle(state: dict, dry_run: bool = False) -> dict:
    core.refresh_env()
    running = core.running_areas()
    report = {"at": core.now_ms(), "areas": [], "actions": []}
    for area in core.areas():
        # ⚠️ ПРОМТ КАРМАНА #174: водитель проекта решает, смотрит ли сторож
        # область и действует ли он в ней. «Никто» — не смотрим вовсе;
        # «Управляющий» — смотрим, но не действуем; «автомат» — смотрим и
        # действуем.
        drv = core.driver_of(area)
        if drv["driver"] == core.DRIVER_NONE:
            report["areas"].append({"slug": area["slug"], "title": area["title"],
                                    "driver": drv, "verdicts": [], "actions": [],
                                    "skipped": "водитель — никто, область не смотрим"})
            continue
        if area["autoheal"] is False and drv["driver"] != core.DRIVER_MANAGER:
            # Ручные области лечатся вручную — но СЕССИИ в них сторож всё же
            # не трогает: правило владельца «незапущенные/ручные не трогаем».
            # Исключение — область, где водитель «Управляющий»: там нитки
            # ведёт он, а сторож только наблюдает.
            continue
        if area["dir"].name not in running:
            continue
        if not area["dir"].is_dir():
            continue
        verdicts = survey(area, state)
        acts = act(area, verdicts, state, dry_run=dry_run)
        report["areas"].append({"slug": area["slug"], "title": area["title"],
                                 "driver": drv, "verdicts": verdicts, "actions": acts})
        report["actions"].extend(acts)
    state["last_run"] = report
    core.write_state(core.SESSION_STATE, state)
    return report


# ────────────────────────────────────────────────────────── CLI

def area_by_slug(slug: str) -> dict:
    for a in core.areas():
        if a["slug"] == slug:
            return a
    return {}


def cli_list(args) -> int:
    running = core.running_areas()
    print("%-14s %-12s %-26s %-7s %s" % ("область", "сессия", "вердикт", "контекст", "заголовок"))
    for area in core.areas():
        if area["dir"].name not in running:
            continue
        verdicts = survey(area, core.read_state(core.SESSION_STATE))
        for v in verdicts:
            print("%-14s %-12s %-26s %-7s %s" % (
                area["slug"], v["session_id"][:12], v["verdict"],
                ("%s%%" % v["fill"]) if v.get("fill") else "—", v["title"]))
    return 0


def cli_read(args) -> int:
    area_slug, session_id = args.read[0], args.read[1]
    area = area_by_slug(area_slug)
    if not area:
        print("нет такой области")
        return 1
    con = core.connect(core.area_db(area["dir"]))
    if not con:
        print("нет базы области")
        return 1
    try:
        sess = None
        for s in sessions_of(area["dir"]):
            if s["session_id"] == session_id or s["session_id"].startswith(session_id):
                sess = s
                break
        if not sess:
            print("сессии нет")
            return 1
        print("=== %s | %s | %s ===" % (sess["session_id"], sess["title"], sess["status"]))
        r = core.rows(con, "select role, created_at_ms, data_json from local_runtime_message_rows "
                           "where session_id=? order by id desc limit ?",
                      (sess["session_id"], args.last))
        for role, at, data in reversed(r):
            try:
                d = json.loads(data or "{}")
            except ValueError:
                continue
            text = (d.get("msg_content") or "").strip()
            if not text:
                continue
            print("--- [%s] %s" % (role, core.fmt_ms(at)))
            print(text[:1200])
            print()
    finally:
        con.close()
    return 0


def cli_send(args) -> int:
    area_slug, session_id, text = args.send[0], args.send[1], args.send[2]
    area = area_by_slug(area_slug)
    if not area:
        print("нет такой области")
        return 1
    sess = None
    for s in sessions_of(area["dir"]):
        if s["session_id"] == session_id or s["session_id"].startswith(session_id):
            sess = s
            break
    if not sess:
        print("сессии нет")
        return 1
    res = send_to_session(area, sess, text, background=False, force=bool(args.force))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def cli_close(args) -> int:
    area_slug, session_id = args.close[0], args.close[1]
    area = area_by_slug(area_slug)
    sess = None
    for s in sessions_of(area["dir"]):
        if s["session_id"] == session_id or s["session_id"].startswith(session_id):
            sess = s
            break
    if not sess:
        print("сессии нет")
        return 1
    res = send_to_session(area, sess, CLOSE_COMMAND, background=True,
                          force=bool(args.force),
                          tag="закрытие:вручную:%s" % sess["session_id"][:12],
                          why="команда закрытия вручную")
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def cli_new(args) -> int:
    area_slug, text = args.new[0], args.new[1]
    area = area_by_slug(area_slug)
    if not area:
        print("нет такой области")
        return 1
    res = new_session(area, text)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


def cli_reset(args) -> int:
    """Сбросить сессию на месте: завершить -> очистить -> продолжить её же."""
    area_slug, session_id = args.reset[0], args.reset[1]
    area = area_by_slug(area_slug)
    if not area:
        print("область не найдена: %s" % area_slug)
        return 2
    sess = None
    for s in sessions_of(area["dir"]):
        if s["session_id"] == session_id or s["session_id"].startswith(session_id):
            sess = s
            break
    if not sess:
        print("сессия не найдена: %s" % session_id)
        return 2
    if not getattr(args, "force", False) and probe_of(area, sess):
        print("это служебная сессия-проба — сбрасывать её нельзя (--force — если очень надо)")
        return 2
    state = core.read_state(core.SESSION_STATE)
    last = int((state.get("reset") or {}).get(sess["session_id"]) or 0)
    if last and int(time.time()) - last < RESET_COOLDOWN \
            and not getattr(args, "force", False):
        print("сброс этой сессии был %d мин назад — подожди (--force — если очень надо)"
              % ((int(time.time()) - last) // 60))
        return 2
    rep = reset_session(area, sess)
    if not rep.get("ok"):
        rep = rotate_session(area, sess)
        rep["fallback"] = "ротация"
    if rep.get("ok"):
        state.setdefault("reset", {})[sess["session_id"]] = int(time.time())
        core.write_state(core.SESSION_STATE, state)
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:2000])
    return 0 if rep.get("ok") else 1


def cli_rotate(args) -> int:
    area_slug, session_id = args.rotate[0], args.rotate[1]
    area = area_by_slug(area_slug)
    sess = None
    for s in sessions_of(area["dir"]):
        if s["session_id"] == session_id or s["session_id"].startswith(session_id):
            sess = s
            break
    if not sess:
        print("сессии нет")
        return 1
    if not getattr(args, "force", False) and probe_of(area, sess):
        print("это служебная сессия-проба — ротировать её нельзя (--force — если очень надо)")
        return 1
    rep = rotate_session(area, sess)
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3000])
    return 0 if rep.get("ok") else 1


def cli_passport(args) -> int:
    """Показать паспорт нитки: что делалось, что осталось, где работать.

    Паспорт лежит на диске машины (`%LOCALAPPDATA%\\minigen-gui\\
    thread-passports.json`, вне репозитория — D-038) и переживает перезапуск
    окна: по нему новая сессия продолжает нитку.
    """
    area_slug, session_id = args.passport[0], args.passport[1]
    area = area_by_slug(area_slug)
    if not area:
        print("область не найдена: %s" % area_slug)
        return 2
    sess = None
    for s in sessions_of(area["dir"]):
        if s["session_id"] == session_id or s["session_id"].startswith(session_id):
            sess = s
            break
    if sess:
        passport = thread_passport(area, sess)
    else:
        found = [item for item in thread.load_passports().values()
                 if str(item.get("session_id") or "").startswith(session_id)]
        passport = found[0] if found else {}
    if not passport:
        print("паспорт нитки не найден")
        return 2
    print(thread.passport_text(passport))
    print("\nФайл паспортов: %s" % thread.passport_path())
    return 0


def cli_status(args) -> int:
    state = core.read_state(core.SESSION_STATE)
    last = state.get("last_run") or {}
    print("=== СЕССИИ (проход %s) ===" % core.fmt_ms(last.get("at") or 0))
    for a in last.get("areas") or []:
        print("  [%s]" % a["slug"])
        # ⚠️ Промт #174 (п. 5): видно, кто водит проект сейчас.
        drv = a.get("driver") or {}
        if drv:
            print("    водитель: %s (%s) — %s" % (drv.get("title") or "—",
                                                  drv.get("source") or "правило",
                                                  drv.get("why") or ""))
        for v in a.get("verdicts") or []:
            print("    %-12s %-26s %-7s %s" % (v["session_id"][:12], v["verdict"],
                                               ("%s%%" % v["fill"]) if v.get("fill") else "—",
                                               v.get("reason") or ""))
    acts = last.get("actions") or []
    if acts:
        print("  действия: %d" % len(acts))
        for x in acts[:10]:
            print("    %s — %s" % (x.get("session", "")[:12], x.get("action")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="МиниГен: сторож сессий")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--read", nargs=2, metavar=("ОБЛАСТЬ", "SESSION_ID"))
    ap.add_argument("--last", type=int, default=12)
    ap.add_argument("--send", nargs=3, metavar=("ОБЛАСТЬ", "SESSION_ID", "ТЕКСТ"))
    ap.add_argument("--close", nargs=2, metavar=("ОБЛАСТЬ", "SESSION_ID"))
    ap.add_argument("--new", nargs=2, metavar=("ОБЛАСТЬ", "ТЕКСТ"))
    ap.add_argument("--rotate", nargs=2, metavar=("ОБЛАСТЬ", "SESSION_ID"))
    ap.add_argument("--reset", nargs=2, metavar=("ОБЛАСТЬ", "SESSION_ID"),
                    help="сбросить сессию НА МЕСТЕ: правильно завершить, "
                         "очистить контекст и продолжить эту же сессию")
    ap.add_argument("--passport", nargs=2, metavar=("ОБЛАСТЬ", "SESSION_ID"),
                    help="показать паспорт нитки: что делалось, что осталось, "
                         "репозиторий, ветка/worktree, PR, критерии приёмки")
    ap.add_argument("--force", action="store_true",
                    help="действовать с пробой лечащего и обойти стражи "
                         "«сессия уже закрыта» / «повтор команды» (обычно нельзя)")
    args = ap.parse_args()

    if args.list:
        return cli_list(args)
    if args.read:
        return cli_read(args)
    if args.send:
        return cli_send(args)
    if args.close:
        return cli_close(args)
    if args.new:
        return cli_new(args)
    if args.reset:
        return cli_reset(args)
    if args.rotate:
        return cli_rotate(args)
    if args.passport:
        return cli_passport(args)
    if args.status:
        return cli_status(args)
    if args.once:
        rep = cycle(core.read_state(core.SESSION_STATE), dry_run=args.dry_run)
        print(json.dumps(rep, ensure_ascii=False, indent=2)[:6000])
        return 0

    core.log("СЕССИИ: сторож запущен")
    stamp = core.script_stamp(Path(__file__))
    while True:
        try:
            if core.script_stamp(Path(__file__)) != stamp:
                core.log("СЕССИИ: файл сторожа изменился — перезапускаюсь "
                         "(правка на диске не действует в живом процессе)")
                if core.supervisor_alive():
                    return 0              # супервизор поднимет свежий процесс
                core.respawn_self()
                return 0
            cycle(core.read_state(core.SESSION_STATE), dry_run=False)
        except Exception as exc:  # noqa: BLE001
            core.log("СЕССИИ: сбой прохода: %s" % exc)
        time.sleep(int(core.net_config().get("interval_sec") or 180))


if __name__ == "__main__":
    sys.exit(main())
