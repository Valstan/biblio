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
3. **Ротирует** сессию при переполнении контекста: команда корректного
   закрытия → ожидание закрытия → архивирование → новая сессия с начальной
   командой (продолжение нитки).
4. **Даёт CLI для «Управляющего»** — сессии, которая сама читает чужие чаты,
   отвечает в них, решает по движению разработки и открывает новые нитки:
       python minimax-sessionwatch.py --list
       python minimax-sessionwatch.py --read <область> <session_id>
       python minimax-sessionwatch.py --send <область> <session_id> "текст"
       python minimax-sessionwatch.py --close <область> <session_id>
       python minimax-sessionwatch.py --new <область> "задача"
       python minimax-sessionwatch.py --rotate <область> <session_id>

⚠️ Промт КАРМАНА #172 — «лечение сессий: решение принимает “Управляющий”,
нитку переносим, а не теряем»:

- **без отчёта о выполнении завершать нельзя.** Отсутствие отчёта — не повод
  требовать завершения: сессия получает команду **продолжить работу** по
  нитке, а не «заверши правильно / напечатай сводку / остановись». Завершение
  или ротация — только при наличии отчёта либо по решению «Управляющего»;
- **решение принимает встроенная модель программы** («Управляющий», та же
  нейросеть, что отвечает в чате): на вход — факты о ситуации, на выход —
  ровно одно из трёх действий (продолжать / завершать / новая сессия с
  продолжением нитки) + инструкция обслуживаемому агенту + причина. Если
  модель недоступна — работает **предсказуемое правило-фолбэк**, и оно тоже
  не требует завершения без отчёта;
- **перенос нитки — с паспортом**: что делалось, что осталось, репозиторий,
  ветка/worktree, открытые PR, критерии приёмки. Паспорт лежит на диске
  машины (вне репозитория, D-038) и переживает перезапуск окна;
- **параллельные нитки одного репозитория не путаются**: паспорт различает
  нитки по репозиторию/ветке/worktree, чужие деревья не трогаются
  (ADR-0011 — один пишущий агент — одно дерево).

Границы:
- **незапущенные области не трогаем** (то же правило, что у лечащего);
- **готовую работу не продолжаем** — это главный заказ владельца;
- **отказ по очкам не лечим здесь** — это лечащий, иначе пул Кармана выжигается;
- **архивирование — точечный UPDATE одной строки**, не переписывание базы.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minigen_core as core  # noqa: E402

#: Паспорт нитки (промт #172). Модуль лежит рядом — на машине его раскатывает
#: `minigen_rollout.py`. Если его нет (старая раскатка) — сторож работает, но
#: перенос нитки делается по-старому (без паспорта) и говорит об этом.
try:
    import minigen_passport as passport_mod  # noqa: E402
except ImportError:  # noqa: BLE001 — паспорта нет ⇒ работаем без него
    passport_mod = None

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
# Области, где работает «Управляющий» — сессия, которая управляет сессиями.
# Правило: **один Управляющий на область** (урок ночи, G679): вторая живая
# сессия там = два писателя в одном worktree.
MANAGER_AREAS = ("brain2",)
RECOVERY_WAIT = 900      # после восстановления сети ждём 15 минут перед «Продолжай»
REVIVE_COOLDOWN = 900    # не чаще одного «Продолжай» на сессию за 15 минут
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

# ────────────────────────────────────── решение лечения (промт КАРМАНА #172)

#: Адрес встроенной модели «Управляющий»: программа «МиниГен» держит локальный
#: API, и в том же процессе живёт модель ⇒ сторож на машине спрашивает решение
#: **той же** нейросети, что отвечает в чате.
MANAGER_URL = "http://127.0.0.1:8097/manager-decision"
#: Сколько ждём ответа программы (сек).
MANAGER_TIMEOUT = 120
#: Не чаще раза в `DECISION_COOLDOWN` на сессию спрашиваем решение: решение
#: лечения тратит очки аккаунта, а спрашивать его на каждом проходе сторожа
#: нельзя — только когда вердикт требует действия (как сейчас `RESET_COOLDOWN`).
DECISION_COOLDOWN = 7200
#: Предел «продолжать» без отчёта (часов): столько сессия может молчать, а
#: потом нитка переносится в новую сессию.
#:
#: ⚠️ Это **решение владельца**, и значение здесь НЕ выбрано: 0 = предел
#: выключен (сессия продолжается, пока владелец не скажет иначе). Промт #172:
#: «нужен предел (например, N часов без отчёта ⇒ перенос нитки) — это решение
#: владельца, вынеси его явно и не выбирай сам». Чтобы включить — поставь
#: число часов (например 6) и перескажи владельцу.
STUCK_LIMIT_HOURS = 0
#: Куда класть паспорта ниток: диск машины, вне репозитория (D-038).
PASSPORT_DIR = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "minigen-gui" / "passports")


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


def final_answer(con, session_id: str, turn_id: str = "") -> dict:
    """Финальный ответ хода (`msg_type=1`) — **текущего** хода.

    Ответ из прошлого хода сводкой этого хода не считаем (22.11: ложное
    «Продолжай» в живую сессию).
    """
    legacy_final = last_message(con, session_id, final_only=True)
    if not legacy_final.get("turn_id") or (turn_id and legacy_final.get("turn_id") == turn_id):
        return legacy_final
    return {}


def has_report(con, session_id: str, turn_id: str = "") -> bool:
    """Есть ли отчёт о выполнении (промт #172).

    ⚠️ Что считаем отчётом — решение владельца, зафиксировано здесь
    (промт #172: «финальный ответ рантайма (`msg_type=1`) или только текст со
    словами “готово/закрыто”? … зафиксируй оба случая в коде»):

    - **финальный ответ хода** (`msg_type=1`) — рантайм сам помечает им
      завершённый ход;
    - **текст со словами закрытия** («готово», «закрыто», «итоговая сводка»…) —
      `looks_finished()`.

    Оба случая — отчёт. Отсутствие отчёта — **не** повод требовать завершения:
    лечение такой сессии = «продолжать» (D-249 ⑤).
    """
    if final_answer(con, session_id, turn_id):
        return True
    last = last_message(con, session_id)
    return looks_finished(last.get("text") or "")


def asks_owner(text: str) -> bool:
    """Ждёт ли сессия решения владельца (короткий вопрос без ответа)."""
    low = (text or "").strip()
    if not low:
        return False
    if len(low) > 1200:
        return False
    return bool(re.search(r"(?i)(нужен ваш выбор|нужно ваше решение|жду ответа|вопрос к вам|"
                           r"как вам|выберите|нужно подтверждение|подтвердите)", low))


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
    turn_id = turn.get("turn_id") or ""
    final_msg = final_answer(con, sid, turn_id)
    fill = context_fill(con, sid, sess.get("extra") or {})
    kids = broken_subagents(con, sid)

    v = {
        "session_id": sid, "title": sess["title"][:70], "status": sess["status"],
        "archived": sess["archived"], "kind": sess["kind"], "verdict": "непонятно",
        "reason": "", "fill": fill.get("fill"), "turn_status": turn.get("status"),
        "updated_at_ms": sess["updated_at_ms"], "broken_kids": len(kids),
        "last_text": (last_msg.get("text") or "")[:400],
        # ⚠️ Отчёт о выполнении (промт #172): есть он или нет — от этого
        # зависит лечение. Без отчёта сессию **нельзя** требовать завершать.
        "has_report": has_report(con, sid, turn_id),
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

    «Живая» = идёт ход либо рантайм считает сессию занятой. Одна такая сессия на
    область — и всё, что запускает вторую (оживление, ручное открытие), должно
    остановиться: два писателя в одном worktree (G679).
    """
    if area.get("slug") not in MANAGER_AREAS:
        return ""
    con = core.connect(core.area_db(area["dir"]))
    if not con:
        return ""
    try:
        for s in sessions_of(area["dir"]):
            if s["archived"] or s["kind"] != "conversation":
                continue
            if is_probe(con, s):
                continue
            turn = last_turn(con, s["session_id"])
            if turn.get("status") == "accepted" and not turn.get("completed_at_ms"):
                return s["session_id"]
            if s["status"] == "started":
                return s["session_id"]
    finally:
        con.close()
    return ""


def send_to_session(area: dict, sess: dict, text: str, background: bool = True,
                     force: bool = False) -> dict:
    """Отправить сообщение в СУЩЕСТВУЮЩУЮ сессию области.

    `mcode exec --session <id>` работает в контексте области (через
    MINIMAX_DATA_DIR) — иначе CLI не найдёт ни сессию, ни авторизацию.

    Перед отправкой перечитываем прокси-переменные из реестра: если сетевой
    сторож починил мёртвый прокси, демон держит старые значения, и ход снова
    упал бы с «Connection error» ([G]: `fetch failed` = мёртвый прокси).
    """
    core.refresh_env()
    if not force and probe_of(area, sess):
        # Пробу не будим: сообщение в пробу = лишний ход и лишние очки.
        return {"ok": False, "why": "это проба лечащего — сообщение не отправлено"}
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
            return {"ok": True, "why": "ход пошёл (pid %s)" % proc.pid}
        return {"ok": False, "why": "процесс умер сразу (rc=%s)" % proc.returncode}
    out = ""
    if proc is not None:
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"ok": bool(proc and proc.returncode == 0), "why": out[-400:]}


def new_session(area: dict, prompt: str, force: bool = False, cwd: str = "") -> dict:
    """Открыть НОВУЮ сессию в области с начальной командой.

    В области Управляющего — только если там нет живой сессии (один
    Управляющий на область, G679); `force` обходит запрет.

    ⚠️ `cwd` — папка нитки: при переносе (промт #172) новая сессия должна
    продолжить **свою** ветку/worktree, а не чужое дерево (ADR-0011).
    """
    # Прокси-переменные — из реестра (22.11, находка Управляющего): сетевой
    # сторож мог починить мёртвый прокси, а вызывающий процесс держит старые
    # значения ⇒ новый ход упал бы с «Connection error». В send_to_session()
    # это уже есть, здесь — нет.
    core.refresh_env()
    if not force:
        busy = live_manager_session(area)
        if busy:
            return {"ok": False, "why": "в области уже живая сессия Управляющего %s — "
                                        "вторая не открывается (один Управляющий на область)" % busy[:12]}
    folder = cwd or area.get("folder") or ""
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
    res = send_to_session(area, sess, RESET_COMMAND, background=True)
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
    again = send_to_session(area, sess, text, background=True)
    report["steps"].append({"continued": again})
    report["ok"] = bool(again.get("ok"))
    report["why"] = again.get("why")
    return report


def rotate_session(area: dict, sess: dict, brief: str = "") -> dict:
    """Ротация сессии: корректное закрытие → архивирование → новая сессия.

    Порядок именно такой (заказ владельца 21.11): сначала сессия сама
    доходит до конца и печатает сводку, потом архивируем, потом открываем
    новую с начальной командой — чтобы нитка не потеряла контекст.

    ⚠️ Промт #172: перед переносом снимается **паспорт нитки** (что делалось,
    что осталось, репозиторий, ветка/worktree, PR, критерии приёмки) — он
    лежит на диске машины (вне репозитория) и переживает перезапуск окна.
    Новая сессия открывается в **своей** ветке/worktree (ADR-0011).
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
    # 0) паспорт нитки — до всего остального (нить не теряется)
    passport = {}
    if passport_mod is not None:
        con = core.connect(core.area_db(area["dir"]))
        try:
            _, passport = thread_facts(area, sess, {"verdict": "ротация"}, con)
        finally:
            if con:
                con.close()
        path = passport_mod.save(passport)
        report["steps"].append({"passport": path})
    res = send_to_session(area, sess, CLOSE_COMMAND, background=True)
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
    text = brief or (passport_mod.as_brief(passport)
                     if passport_mod is not None and passport
                     else continuation_brief(area, sess))
    cwd = str((passport or {}).get("worktree") or area.get("folder") or "")
    opened = new_session(area, text, cwd=cwd)
    report["steps"].append({"new_session": opened})
    report["ok"] = bool(opened.get("ok"))
    report["why"] = opened.get("why")
    return report


def continuation_brief(area: dict, sess: dict) -> str:
    """Начальная команда для новой сессии: продолжить нитку, а не начать заново.

    Для «Управляющего» основой служит его собственный бриф
    (`docs/orchestrator/BRIEF.md` в рабочей папке области) — новая сессия
    должна въехать в курс работы сразу, а не с нуля.
    """
    con = core.connect(core.area_db(area["dir"]))
    summary = ""
    try:
        if con:
            final = last_message(con, sess["session_id"], final_only=True)
            summary = (final.get("text") or "").strip()
    finally:
        if con:
            con.close()
    folder = Path(area.get("folder") or "")
    handoff = ""
    for name in ("docs/SESSION_HANDOFF.md", "docs/HANDOFF.md", "HANDOFF.md"):
        p = folder / name
        if p.is_file():
            try:
                handoff = p.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                handoff = ""
            break
    brief = ""
    p = folder / "docs" / "orchestrator" / "BRIEF.md"
    if p.is_file():
        try:
            brief = p.read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            brief = ""
    parts = []
    if brief:
        parts += [brief.strip(), "", "---", ""]
    parts += ["Продолжи нитку разработки в новой сессии.", ""]
    if handoff:
        parts += ["Состояние нитки (из handoff-файла):", handoff.strip(), ""]
    if summary:
        parts += ["Итоговая сводка закрытой сессии:", summary.strip()[:3000], ""]
    parts += ["Сначала прочитай состояние и скажи, что делаешь дальше. "
              "Не начинай новую тему — продолжай ту же нитку."]
    return "\n".join(parts)


REVIVE_PROMPT = (
    "Продолжай нитку.\n"
    "Порядок:\n"
    "1) Сначала посмотри свой контекст: если работа уже закончена и напечатана "
    "итоговая сводка — не начинай заново, просто подтверди, что всё закрыто, и жди.\n"
    "2) Если нужен выбор владельца — перечисли варианты и жди, ничего не решай сам.\n"
    "3) Иначе продолжай работу с места, где остановился.\n"
    "МиниГен поднял тебя после простоя/обрыва связи."
)


# ────────────────────────────── решение лечения: «Управляющий» (промт #172)

#: Ровно три действия (промт #172).
ACTION_CONTINUE = "continue"
ACTION_FINISH = "finish"
ACTION_ROTATE = "rotate"
ACTION_LABELS = {
    ACTION_CONTINUE: "продолжать",
    ACTION_FINISH: "завершать",
    ACTION_ROTATE: "новая сессия с продолжением нитки",
}


def bridge_token() -> str:
    """Токен локального API программы (его же читает сетевой сторож)."""
    try:
        path = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "minigen-gui" / "bridge-token.txt")
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ask_manager(facts: dict) -> dict:
    """Спросить решение у встроенной модели «Управляющий» (через программу).

    Канал — локальный API программы «МиниГен» (`:8097`): сервер живёт в том
    же процессе, что и встроенная модель, поэтому решение принимает **та же**
    нейросеть, что отвечает в чате.

    Ответ: `{"ok": True, "action": "continue|finish|rotate", "instruction": ...,
    "reason": ...}` либо `{"ok": False, "error": ...}` — тогда решает
    правило-фолбэк.
    """
    token = bridge_token()
    if not token:
        return {"ok": False, "error": "нет токена программы «МиниГен»"}
    body = json.dumps(facts, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MANAGER_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-MiniGen-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=MANAGER_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "программа ответила %s" % exc.code}
    except Exception as exc:  # noqa: BLE001 — программа не запущена или лежит
        return {"ok": False, "error": "программа не ответила: %s" % exc}
    if not isinstance(data, dict):
        return {"ok": False, "error": "ответ программы не разобран"}
    return data


def fallback_decision(facts: dict) -> dict:
    """Правило-фолбэк: предсказуемое и безопасное (промт #172).

    - отчёт о выполнении **есть** → работа закончена ⇒ «завершать»;
    - отчёта **нет** → «продолжать» — завершать без отчёта нельзя (D-249 ⑤);
    - предел простоя (`STUCK_LIMIT_HOURS`, решение владельца) → перенос нитки.

    ⚠️ Правило **не требует** завершения сессии без отчёта — это прямое
    требование критерия приёмки промта #172.
    """
    if facts.get("has_report"):
        return {"action": ACTION_FINISH, "instruction": "",
                "reason": "работа закончена: есть отчёт о выполнении",
                "finish_old": False, "source": "правило-фолбэк"}
    reason = "отчёта о выполнении нет — завершать нельзя, продолжаем нитку"
    action = ACTION_CONTINUE
    idle = int(facts.get("idle_minutes") or 0)
    if STUCK_LIMIT_HOURS and idle >= STUCK_LIMIT_HOURS * 60:
        action = ACTION_ROTATE
        reason = ("сессия молчит %d ч (предел владельца %d ч) — переносим нитку"
                  % (idle // 60, STUCK_LIMIT_HOURS))
    return {"action": action, "instruction": "", "reason": reason,
            "finish_old": False, "source": "правило-фолбэк"}


def treatment_decision(facts: dict) -> dict:
    """Решение о лечении: спросить «Управляющего», иначе — правило-фолбэк."""
    answer = ask_manager(facts)
    action = str((answer or {}).get("action") or "")
    if (answer or {}).get("ok") and action in (ACTION_CONTINUE, ACTION_FINISH,
                                                ACTION_ROTATE):
        return {"action": action,
                "instruction": str(answer.get("instruction") or ""),
                "reason": str(answer.get("reason") or ""),
                "finish_old": bool(answer.get("finish_old")),
                "source": "модель «Управляющий»"}
    decision = fallback_decision(facts)
    decision["error"] = str((answer or {}).get("error")
                            or "встроенная модель недоступна")
    return decision


def git_branch(folder: str) -> str:
    """Ветка в папке (пусто — не git или не папка)."""
    if not folder or not Path(folder).is_dir():
        return ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(["git", "-C", folder, "rev-parse",
                               "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=10,
                              encoding="utf-8", errors="replace",
                              creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def parallel_sessions(area: dict, sess: dict) -> list:
    """Параллельные сессии того же репозитория (ADR-0011: чужие — не трогать)."""
    out: list = []
    mine = str(sess.get("workspace_dir") or area.get("folder") or "")
    try:
        others = sessions_of(area["dir"])
    except Exception:  # noqa: BLE001 — база недоступна ⇒ пусто
        return out
    for other in others:
        if other["session_id"] == sess.get("session_id"):
            continue
        if other["archived"] or other["kind"] != "conversation":
            continue
        folder = other.get("workspace_dir") or ""
        if folder and mine:
            try:
                if Path(folder).resolve() == Path(mine).resolve():
                    continue          # та же папка — не чужое дерево
            except OSError:
                pass
        item = {"session_id": other["session_id"], "title": other["title"][:60],
                "worktree": folder,
                "updated_at": core.fmt_ms(other["updated_at_ms"])}
        branch = git_branch(folder)
        if branch:
            item["branch"] = branch
        out.append(item)
    return out[:10]


def thread_facts(area: dict, sess: dict, v: dict, con) -> tuple:
    """Факты о ситуации и паспорт нитки — для решения «Управляющего».

    На вход модели идут **факты**, а не простыня логов: вердикт сторожа, есть
    ли отчёт о выполнении, событие сжатия контекста, заполнение контекста,
    когда последний раз был ход, репозиторий/ветка/worktree нитки, список
    параллельных сессий того же репозитория.
    """
    turn = last_turn(con, sess["session_id"]) if con is not None else {}
    final = (final_answer(con, sess["session_id"], turn.get("turn_id") or "")
             if con is not None else {})
    last = last_message(con, sess["session_id"]) if con is not None else {}
    report_text = (final.get("text") or last.get("text") or "").strip()
    stamp = (turn.get("completed_at_ms") or turn.get("accepted_at_ms")
             or sess.get("updated_at_ms") or 0)
    idle_ms = max(0, core.now_ms() - (stamp or core.now_ms()))
    facts = {
        "area": area.get("slug") or "",
        "session_id": sess.get("session_id") or "",
        "title": sess.get("title") or "",
        "verdict": v.get("verdict") or "",
        "reason": v.get("reason") or "",
        "turn_status": turn.get("status") or "",
        "has_report": bool(v.get("has_report")),
        "report_excerpt": report_text[:1500],
        "last_words": (last.get("text") or "")[:1500],
        # событие сжатия контекста (его рантайм и оставляет в журнале)
        "compacted": v.get("verdict") == "скомпактилась",
        "compacted_at": core.fmt_ms(int(v.get("compacted_at_ms") or 0))
        if v.get("compacted_at_ms") else "",
        "context_fill": v.get("fill"),
        "peak_fill": v.get("peak_fill"),
        "last_turn_at": core.fmt_ms(stamp),
        "idle_minutes": idle_ms // 60000,
        "note": ("Отчёт о выполнении — главный факт: без него сессию "
                 "завершать нельзя, её лечение = «продолжать»."),
    }
    passport: dict = {}
    if passport_mod is not None:
        passport = passport_mod.build(area, sess, facts)
        facts["repo"] = passport.get("repo") or ""
        facts["branch"] = passport.get("branch") or ""
        facts["worktree"] = passport.get("worktree") or ""
        facts["prs"] = passport.get("prs") or []
        facts["done"] = passport.get("done") or []
        facts["todo"] = passport.get("todo") or []
        facts["acceptance"] = passport.get("acceptance") or []
        facts["files"] = passport.get("files") or []
    facts["parallel_sessions"] = parallel_sessions(area, sess)
    return facts, passport


def continue_instruction(passport: dict, decision: dict) -> str:
    """Инструкция обслуживаемому агенту: что делать дальше (из паспорта).

    ⚠️ Инструкция должна **называть**, что делать дальше (из паспорта нитки),
    а не просто «продолжай» — иначе агент её проигнорирует (риск из
    промта #172).
    """
    text = str(decision.get("instruction") or "").strip()
    parts = [text or "Продолжай работу по нитке."]
    step = ""
    if passport_mod is not None:
        step = passport_mod.next_step(passport or {})
    if step and step.lower() not in parts[0].lower():
        parts.append("Что делать дальше: %s" % step)
    place = []
    if (passport or {}).get("branch"):
        place.append("ветка %s" % passport["branch"])
    if (passport or {}).get("worktree"):
        place.append("worktree %s" % passport["worktree"])
    if place:
        parts.append("Продолжай свою нитку (%s); чужие ветки и деревья не трогай."
                     % ", ".join(place))
    parts.append("Если работа уже закончена и напечатана итоговая сводка — "
                 "подтверди и жди; если нужен выбор владельца — перечисли "
                 "варианты и жди.")
    return "\n".join(parts)


def decision_report(report: dict) -> str:
    """Строка отчёта: кто, когда, по каким фактам, какое решение, что сделано."""
    dec = report.get("decision") or {}
    facts = report.get("facts") or {}
    bits = ["решение лечения сессии %s: действие «%s» (источник: %s)"
            % (str(report.get("session_id") or "?")[:12],
               ACTION_LABELS.get(dec.get("action"), dec.get("action") or "?"),
               dec.get("source") or "?")]
    if dec.get("reason"):
        bits.append("причина: %s" % dec["reason"])
    if dec.get("error"):
        bits.append("запасной путь: %s" % dec["error"])
    bits.append("факты: отчёт о выполнении — %s; контекст — %s%%; простой — %s мин; "
                "ветка — %s; параллельных сессий — %s"
                % ("есть" if facts.get("has_report") else "НЕТ",
                   facts.get("context_fill"), facts.get("idle_minutes"),
                   facts.get("branch") or "?",
                   facts.get("parallel") or 0))
    steps = report.get("steps") or []
    done = []
    for step in steps:
        why = str(step.get("why") or "")
        if not why:
            for value in step.values():
                if isinstance(value, dict) and value.get("why"):
                    why = str(value["why"])
                    break
        if why:
            done.append(why)
    bits.append("что сделано: %s" % (", ".join(done) or "ничего — сессию не трогали"))
    return ". ".join(bits)


def treat_session(area: dict, v: dict, state: dict = None) -> dict:
    """Лечение сессии по решению «Управляющего» (или правилу-фолбэка).

    Порядок (промт #172, п.2):
      1) **паспорт нитки** — снимаем всегда (нить не теряется);
      2) действие:
         - «продолжать» — сессию **не трогаем**: ни команды завершения, ни
           архивации; если она стоит — мягко подталкиваем инструкцией из
           паспорта;
         - «завершать» — сессию можно закрыть;
         - «новая сессия с продолжением нитки» — паспорт → (корректное
           завершение старой сессии, если так решили) → архив → новая сессия
           в СВОЕЙ ветке/worktree с паспортом и командой «продолжи нитку».
    """
    sess = {"session_id": v["session_id"], "title": v.get("title") or "",
            "workspace_dir": area.get("folder", "")}
    report = {"session_id": v["session_id"], "verdict": v.get("verdict"),
              "area": area.get("slug") or "", "steps": []}
    con = core.connect(core.area_db(area["dir"]))
    try:
        facts, passport = thread_facts(area, sess, v, con)
    finally:
        if con:
            con.close()
    decision = treatment_decision(facts)
    action = decision["action"]
    report["action"] = action
    report["decision"] = {
        "action": action, "label": ACTION_LABELS.get(action, action),
        "reason": decision.get("reason") or "",
        "source": decision.get("source") or "",
        "instruction": decision.get("instruction") or "",
    }
    if decision.get("error"):
        report["decision"]["error"] = decision["error"]
    report["facts"] = {"has_report": facts.get("has_report"),
                       "context_fill": facts.get("context_fill"),
                       "peak_fill": facts.get("peak_fill"),
                       "idle_minutes": facts.get("idle_minutes"),
                       "branch": facts.get("branch"),
                       "worktree": facts.get("worktree"),
                       "parallel": len(facts.get("parallel_sessions") or [])}

    # 1) паспорт нитки — всегда (даже если лечение не сработало, нить не теряется)
    if passport_mod is not None and passport:
        path = passport_mod.save(passport)
        report["steps"].append({"passport": path})
        report["passport"] = path

    # 2) действие
    if action == ACTION_CONTINUE:
        instr = continue_instruction(passport, decision)
        last_revive = int((((state or {}).get("revived") or {})
                           .get(sess["session_id"])) or 0)
        if last_revive and int(time.time()) - last_revive < REVIVE_COOLDOWN:
            # Недавно уже подталкивали — второй раз не тыкаем (очки и терпение
            # агента — не бесконечные).
            report["steps"].append({"continue": "недавно подталкивали — не трогаем"})
            report["ok"] = True
            report["why"] = "сессия продолжает работу"
        else:
            res = send_to_session(area, sess, instr, background=True)
            report["steps"].append({"continue": res})
            report["ok"] = bool(res.get("ok"))
            report["why"] = res.get("why")
            if state is not None and res.get("ok"):
                state.setdefault("revived", {})[sess["session_id"]] = int(time.time())
        # ⚠️ НИ команды «заверши работу правильно», НИ архивации: отчёта о
        # выполнении нет — работа не закончена, нитка продолжается здесь.
    elif action == ACTION_FINISH:
        report["steps"].append({"finish": "решение «Управляющего»: закрываем"})
        arc = archive_session(area["dir"], sess["session_id"])
        report["steps"].append({"archived": arc})
        report["ok"] = bool(arc.get("ok"))
        report["why"] = arc.get("why")
    elif action == ACTION_ROTATE:
        # Корректно завершить старую сессию — только если решили (или отчёт
        # есть: тогда работа закончена и сводка не прерывает её на полпути).
        if facts.get("has_report") or decision.get("finish_old"):
            res = send_to_session(area, sess, CLOSE_COMMAND, background=True)
            report["steps"].append({"close_command": res})
            if res.get("ok"):
                closed = wait_closed(area, sess["session_id"])
                report["steps"].append({"closed": closed})
        arc = archive_session(area["dir"], sess["session_id"])
        report["steps"].append({"archived": arc})
        if not arc.get("ok"):
            report["ok"] = False
            report["why"] = "не архивировалась: %s" % arc.get("why")
        else:
            text = (passport_mod.as_brief(passport)
                    if passport_mod is not None and passport
                    else continuation_brief(area, sess))
            cwd = str((passport or {}).get("worktree") or area.get("folder") or "")
            opened = new_session(area, text, cwd=cwd)
            report["steps"].append({"new_session": opened})
            report["ok"] = bool(opened.get("ok"))
            report["why"] = opened.get("why")
    else:
        report["ok"] = False
        report["why"] = "неизвестное действие: %s" % action

    text = decision_report(report)
    core.report("info" if report.get("ok") else "warn", "session", text)
    core.log("СЕССИИ: %s — %s" % (area.get("slug") or "?", text))
    return report


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


def act(area: dict, verdicts: list, state: dict, dry_run: bool = False) -> list:
    """Действия по вердиктам: оживление и **решение «Управляющего»** (промт #172).

    Вердикты «скомпактилась»/«контекст заполнен» больше не решаются правилом
    в коде: решение принимает встроенная модель (см. `treat_session`), а если
    она недоступна — предсказуемое правило-фолбэк, которое **не требует**
    завершения сессии без отчёта о выполнении.
    """
    actions = []
    for v in verdicts:
        slug = area["slug"]
        last = int((state.get("revived") or {}).get(v["session_id"]) or 0)
        cool = int(time.time()) - last < REVIVE_COOLDOWN
        if v["verdict"] in ("готова", "работает", "кончились очки", "ждёт владельца"):
            continue
        if v["verdict"] in ("скомпактилась", "контекст заполнен"):
            # ⚠️ Промт #172: решение лечения принимает **встроенная модель**
            # «Управляющий» (факты → решение: продолжать / завершать / новая
            # сессия с продолжением нитки). Жёсткое правило в коде больше не
            # решает: без отчёта о выполнении сессия НЕ получает команду
            # «заверши работу правильно» — её лечение = «продолжать».
            if dry_run:
                actions.append({"session": v["session_id"],
                                "action": "решение «Управляющего» (сухой прогон)"})
                continue
            last_decision = int((state.get("decision") or {}).get(v["session_id"]) or 0)
            if last_decision and int(time.time()) - last_decision < DECISION_COOLDOWN:
                actions.append({"session": v["session_id"],
                                "action": "решение спрашивали недавно — не трогаем"})
                continue
            rep = treat_session(area, v, state)
            state.setdefault("decision", {})[v["session_id"]] = int(time.time())
            actions.append({"session": v["session_id"],
                            "action": ACTION_LABELS.get(rep.get("action"),
                                                       rep.get("action") or "?"),
                            "report": rep})
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
                                  REVIVE_PROMPT, background=True)
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
                                  REVIVE_PROMPT, background=True)
            state.setdefault("revived", {})[v["session_id"]] = int(time.time())
            actions.append({"session": v["session_id"], "action": "«Продолжай» после вендора", "result": res})
            core.log("СЕССИИ: %s — сессия %s упала на нестабильности модели, вендор ожил: %s" % (
                slug, v["session_id"][:12], res.get("why")))
            continue
        if v["verdict"] in ("брошена", "есть оборванные субагенты"):
            if area["slug"] in MANAGER_AREAS and live_manager_session(area) not in ("", v["session_id"]):
                actions.append({"session": v["session_id"], "action": "место занято (один Управляющий на область)"})
                continue
            # «Ход завис» сюда НЕ входит: сорвать зависший ход и положить
            # «Продолжай» в начало умеет лечащий (перезапуск области) — если
            # послать «Продолжай» сюда, оно встанет в очередь за висящим ходом
            # и не разбудит нитку (та самая беда, что чинили 18.11).
            if cool:
                continue
            if dry_run:
                actions.append({"session": v["session_id"], "action": "«Продолжай» (сухой прогон)"})
                continue
            res = send_to_session(area, {"session_id": v["session_id"],
                                  "workspace_dir": area.get("folder", "")},
                                  REVIVE_PROMPT, background=True)
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
        if area["autoheal"] is False and area["slug"] not in ("brain2",):
            # Ручные области лечатся вручную — но СЕССИИ в них сторож всё же
            # не трогает: правило владельца «незапущенные/ручные не трогаем».
            continue
        if area["dir"].name not in running:
            continue
        if not area["dir"].is_dir():
            continue
        verdicts = survey(area, state)
        acts = act(area, verdicts, state, dry_run=dry_run)
        report["areas"].append({"slug": area["slug"], "title": area["title"],
                                 "verdicts": verdicts, "actions": acts})
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
    res = send_to_session(area, sess, text, background=False)
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
    res = send_to_session(area, sess, CLOSE_COMMAND, background=True)
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
    rep = reset_session(area, sess)
    if not rep.get("ok"):
        rep = rotate_session(area, sess)
        rep["fallback"] = "ротация"
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


def cli_status(args) -> int:
    state = core.read_state(core.SESSION_STATE)
    last = state.get("last_run") or {}
    print("=== СЕССИИ (проход %s) ===" % core.fmt_ms(last.get("at") or 0))
    for a in last.get("areas") or []:
        print("  [%s]" % a["slug"])
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
    ap.add_argument("--force", action="store_true",
                    help="действовать с пробой лечащего (обычно нельзя)")
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
