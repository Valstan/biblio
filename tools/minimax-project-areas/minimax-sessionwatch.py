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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minigen_core as core  # noqa: E402

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
CLOSE_TIMEOUT = 900      # сколько ждём корректного закрытия сессии, сек


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
            "text": data.get("msg_content") or ""}


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
                    now = _dt.datetime.now()
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
    final_msg = last_message(con, sid, final_only=True)
    fill = context_fill(con, sid, sess.get("extra") or {})
    kids = broken_subagents(con, sid)

    v = {
        "session_id": sid, "title": sess["title"][:70], "status": sess["status"],
        "archived": sess["archived"], "kind": sess["kind"], "verdict": "непонятно",
        "reason": "", "fill": fill.get("fill"), "turn_status": turn.get("status"),
        "updated_at_ms": sess["updated_at_ms"], "broken_kids": len(kids),
        "last_text": (last_msg.get("text") or "")[:400],
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

    # 3) Упавший ход: сеть или нет?
    if turn.get("status") == "failed" or sess["status"] == "error" or outcome == "failed":
        blob = ("%s %s" % (sess["error_message"] or "", turn.get("input_json") or "")).lower()
        net = any(m in blob for m in NET_MARKERS) or sess["error_code"] == NET_CODE
        v["verdict"] = "упала по сети" if net else "ошибка"
        v["reason"] = "ход упал: %s" % ((sess["error_message"] or "без текста")[:120])
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
        if kids:
            v["verdict"] = "есть оборванные субагенты"
            v["reason"] = "%d оборванных субагентов — работа не закончена" % len(kids)
            return v
        if final_msg and looks_finished(final_msg.get("text")):
            v["verdict"] = "готова"
            v["reason"] = "последний ход завершён, напечатана итоговая сводка"
            return v
        if asks_owner(last_msg.get("text")):
            v["verdict"] = "ждёт владельца"
            v["reason"] = "последнее сообщение — вопрос владельцу"
            return v
        v["verdict"] = "брошена"
        v["reason"] = "ход завершён, но итоговой сводки нет"
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


def new_session(area: dict, prompt: str, force: bool = False) -> dict:
    """Открыть НОВУЮ сессию в области с начальной командой.

    В области Управляющего — только если там нет живой сессии (один
    Управляющий на область, G679); `force` обходит запрет.
    """
    if not force:
        busy = live_manager_session(area)
        if busy:
            return {"ok": False, "why": "в области уже живая сессия Управляющего %s — "
                                        "вторая не открывается (один Управляющий на область)" % busy[:12]}
    folder = area.get("folder") or ""
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
    text = brief or continuation_brief(area, sess)
    opened = new_session(area, text)
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
    """Действия по вердиктам: оживление и ротация."""
    actions = []
    for v in verdicts:
        slug = area["slug"]
        last = int((state.get("revived") or {}).get(v["session_id"]) or 0)
        cool = int(time.time()) - last < REVIVE_COOLDOWN
        if v["verdict"] in ("готова", "работает", "кончились очки", "ждёт владельца"):
            continue
        if v["verdict"] == "скомпактилась":
            if dry_run:
                actions.append({"session": v["session_id"], "action": "ротация после компактизации (сухой прогон)"})
                continue
            sess = {"session_id": v["session_id"], "title": v["title"],
                    "workspace_dir": area.get("folder", "")}
            rep = rotate_session(area, sess)
            actions.append({"session": v["session_id"], "action": "ротация после компактизации", "report": rep})
            core.log("СЕССИИ: %s — сессия %s сжата рантаймом, ротирую: %s" % (
                slug, v["session_id"][:12], rep.get("why")))
            continue
        if v["verdict"] == "контекст заполнен":
            if dry_run:
                actions.append({"session": v["session_id"], "action": "ротация (сухой прогон)"})
                continue
            sess = {"session_id": v["session_id"], "title": v["title"],
                    "workspace_dir": area.get("folder", "")}
            rep = rotate_session(area, sess)
            actions.append({"session": v["session_id"], "action": "ротация", "report": rep})
            core.log("СЕССИИ: %s — ротация: %s" % (slug, rep.get("why")))
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
