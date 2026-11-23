"""МиниГен: авто-лечение областей MiniMax Code (решение владельца 16.11.2026).

Что делает. Раз в интервал смотрит за областями из `mcode-project-areas.json`
с флагом `autoheal` (и без флага `paused`). Если область **застряла на отказе
по очкам** — то есть аккаунт кончился, — берёт у Кармана следующий аккаунт,
сажает его в область, активирует бонусные очки, перезапускает область и
отправляет в её сессию «Продолжай», чтобы работа над проектом возобновилась.

Детектор (2026-11-17, переписан после трёх остановок подряд). Ключевая правка:
статус хода `failed` **сам по себе ни о чём не говорит** — рантайм помечает
так и отказ провайдера, и прерванный ход (владелец написал «продолжай» в
разгар работы), и compaction. Поэтому признак отказа по очкам — **только
вендорский код**, а не текст:

- **T1 (точный).** В строке сессии рантайма: `error_code = 50110`
  (`error_message = "insufficient balance (1008)"`). Плюс два якоря:
  отказ случился **после установки текущего аккаунта** и **область после него
  не двигалась** (нет ни одного успешного хода). Иначе отказ считается
  устаревшим (аккаунт уже сменили, работа возобновилась).
- **T2 (эвристика + живая проверка).** Ход упал, не дав ни одного ответа
  агента (провайдер отказал сразу), область после него застряла. Такие
  случаи лечатся **только после живой проверки**: в область отправляется
  короткий ход; если провайдер отвечает отказом по очкам — лечим, если ход
  проходит — не лечим (пул Кармана не жжём).

Границы (решение владельца от 16.11, отмена D-222 / G636):
- лечим **только отказ по очкам**, а не «приложение считает себя вышедшим»;
- **кулдаун**: не чаще одного лечения на область за интервал — иначе общий
  пул Кармана выжигается;
- если у Кармана **нет свободного аккаунта** — пишем в журнал и ждём;
- **основной десктоп (без `--user-data-dir`) не трогаем никогда**;
- область, помеченная `paused`, не трогаем (владелец снял её с присмотра);
- «Продолжай» уходит **в фоне**: ход длинный, ждать его в цикле нельзя.

Механика входа — **единственная реализация** `minimax-switch.py` (здесь она
подключается как модуль, а не дублируется). Адрес проекта для Кармана берётся
из поля `karman` конфига — статистика расхода идёт по проектам.

Запуск:  python C:\\Users\\valstan\\bin\\minimax-autheal.py --status  # что видно по областям
         python C:\\Users\\valstan\\bin\\minimax-autheal.py --dry-run --once
         python C:\\Users\\valstan\\bin\\minimax-autheal.py --once     # один проход
         python C:\\Users\\valstan\\bin\\minimax-autheal.py            # демон (по умолчанию)
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BIN = Path(r"C:\Users\valstan\bin")
CONFIG = BIN / "mcode-project-areas.json"
AREAS_ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT", r"C:\Users\valstan\.minimax-areas"))
SWITCH = BIN / "minimax-switch.py"
# Детектор оборванных субагентов: лечащий ссылается на него в самом «Продолжай».
# Путь — в bin, а не в репозитории: файл нужен в любой области, а не только в Мозге.
ORPHANS = BIN / "minimax-orphans.py"
MCODE = Path(os.environ.get("APPDATA", "")) / "npm" / "mcode.cmd"
JOURNAL = Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web" / "journal.log"
# Кулдаун лечения хранится в файле, а не в памяти: перезапуск супервизора не должен
# обнулять «уже лечили» — иначе пул Кармана выжигается повторными лечениями.
STATE_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web" / "autheal-state.json"

FAST_INTERVAL = 10       # секунд между проверками области, которая жива
HEAL_QUIET = 180         # секунд до следующей проверки области, которую только
                        # что вылечили (сразу после лечения ловить нечего —
                        # ход ещё идёт, очки только что начислены)
INTERVAL = 180          # (обратная совместимость) пауза между проходами по умолчанию
COOLDOWN = 1800         # секунд между лечениями одной области
EARLY_REHEAL_GAP = 300  # минимальный пауз перед ПОВТОРНЫМ лечением, если тот
                        # аккаунт, который мы поставили, уже умер (17.11: Мозг
                        # сдох за 5 минут после лечения — ждать 30 минут нельзя,
                        # область всё это время стоит)
PROBE_COOLDOWN = 600    # секунд между ЖИВЫМИ проверками (T2) одной области:
                        # при паузе 15 с иначе проверка шла бы каждый проход
# ── Ограничители расхода пула (17.11: за день сгорело 77+ аккаунтов)
REJECT_BACKOFF = 900      # (не используется: отклонённый аккаунт ⇒ берём следующий)
FAST_DEATH_GAP = 900      # (не используется: быстрая смерть аккаунта — это норма)
FAST_DEATH_BACKOFF = 7200 # (не используется)
POOL_EMPTY_BACKOFF = 600 # Карман ответил «свободных аккаунтов нет» — ждём 10 минут.
                          # ⚠️ Прежнее «каждые полчаса возвращаются» было НЕВЕРНО:
                          # у Кармана аккаунт, активированный в 03:00–24:00, свободен
                          # с 03:00 следующих суток, а активированный в 00:00–03:00 — с
                          # 00:00 следующих суток (DAILY_RESET_HOURS = [0, 3] в его коде).
                          # Полный пул восстанавливается к 03:00 МСК. Опрос раз в 10 минут
                          # нужен, чтобы поймать момент, когда владелец добавит аккаунты вручную.
BURST_WINDOW = 180         # окно подсчёта «активационной лавины», сек
BURST_LIMIT = 5            # столько аккаунтов на проект за окно ⇒ аларм и стоп
LIVE_SWITCH = True       # смена аккаунта без перезапуска десктопа (заказ владельца)
PROBE_TTL = 1800          # сколько ждём разбора пробного хода, сек (30 мин)
PROBE_NO_TURN_TIMEOUT = 120 # если нового хода нет дольше этого — откат: перезапуск
PROBE_FAIL_FAST = 180      # падение пробного хода быстрее этого ⇒ десктоп
                          # отверг токен (откат); дольше ⇒ очки кончились
ACTIVATE_TIMEOUT = 150     # сколько ждём, пока десктоп поднимется и войдёт
ACTIVATION_POLL = 5        # шаг опроса состояния десктопа, сек
ERROR_WINDOW = 86400    # окно отказа по очкам (сутки): сломанный аккаунт сам не
                        # восстановится до ночного сброса, а главная защита от
                        # повторного лечения — время установки ТЕКУЩЕГО аккаунта
BALANCE_MARK = "insufficient balance"
BALANCE_CODE = 50110    # код рантайма на отказ провайдера «insufficient balance (1008)»
PROBE_TIMEOUT = 150     # секунд на живой проверку (T2)
MIN_TOKEN_LIFE_MIN = 30 # минимальный остаток жизни access-токена, который мы ставим
MAX_PULL_TRIES = 3      # сколько аккаунтов пробуем взять, пока не найдём живой токен
LOST_LOGIN_STATES = ("logout_pending", "none", "lease-expired", "logged_out", "signed_out")

_state = {"last_heal": {}, "install": {}, "probe": {}, "pulls": {}, "backoff": {}, "activated": {}, "burst": {}, "alarm": {}, "probe_turn": {}, "pause_notice": {}, "failure": {}}
_last_verdict = {}      # прошлый вердикт по области — чтобы не писать в журнал
                        # одно и то же «лечить нечего» каждые 15 секунд
_next_check = {}        # slug → время следующей проверки области


# ────────────────────────────────────────────────────────── журнал и утилиты

def load_cooldowns() -> None:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _state["last_heal"] = {str(k): float(v) for k, v in (data.get("last_heal") or {}).items()}
            _state["install"] = {str(k): v for k, v in (data.get("install") or {}).items()
                                 if isinstance(v, dict)}
            _state["probe"] = {str(k): float(v) for k, v in (data.get("probe") or {}).items()}
            _state["pulls"] = {str(k): [float(t) for t in v] for k, v in (data.get("pulls") or {}).items()
                                if isinstance(v, list)}
            _state["backoff"] = {str(k): v for k, v in (data.get("backoff") or {}).items()
                                 if isinstance(v, dict)}
            _state["activated"] = {str(k): float(v) for k, v in (data.get("activated") or {}).items()}
            _state["burst"] = {str(k): [float(t) for t in v] for k, v in (data.get("burst") or {}).items()
                                if isinstance(v, list)}
            _state["alarm"] = data.get("alarm") if isinstance(data.get("alarm"), dict) else {}
            _state["probe_turn"] = {str(k): v for k, v in (data.get("probe_turn") or {}).items()
                                   if isinstance(v, dict)}
            _state["pause_notice"] = {str(k): v for k, v in (data.get("pause_notice") or {}).items()}
            _state["failure"] = {str(k): v for k, v in (data.get("failure") or {}).items()
                                 if isinstance(v, dict)}
    except (OSError, ValueError, TypeError):
        _state["last_heal"] = {}
        _state["install"] = {}
        _state["probe"] = {}
        _state["pulls"] = {}
        _state["backoff"] = {}
        _state["activated"] = {}
        _state["burst"] = {}
        _state["alarm"] = {}
        _state["probe_turn"] = {}


def save_cooldowns() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(_state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError:
        pass


def log(msg: str) -> None:
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


_switch_cache = {}


def load_switch():
    """Подключаем minimax-switch.py как модуль (он — единственная реализация механики)."""
    if "mod" in _switch_cache:
        return _switch_cache["mod"]
    spec = importlib.util.spec_from_file_location("minimax_switch", str(SWITCH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _switch_cache["mod"] = mod
    return mod


_cfg_cache = {}


def config() -> dict:
    """Конфиг областей. ⚠️ Если файл битый — НЕ теряем надзор: держим последний
    удачный разбор и пишем в журнал. (17.11: запись конфига из панели на секунду
    сделала файл невалидным — при пустом разборе лечащий молча перестал бы
    следить за всеми областями.)"""
    global _cfg_cache
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("projects"):
            _cfg_cache = data
            return data
    except (OSError, ValueError) as exc:
        log("конфиг %s не читается (%s) — держу последний рабочий" % (CONFIG, exc))
    if _cfg_cache:
        return _cfg_cache
    return {}


def _fmt(ms) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m %H:%M:%S") if ms else "—"


# ────────────────────────────────────────────────────────── области

def all_areas() -> list:
    """Все проектные области из конфига с их флагами."""
    out = []
    for p in config().get("projects", []):
        slug = p["slug"]
        out.append({
            "slug": slug,
            "title": p.get("title", slug),
            "folder": p.get("folder", ""),
            "karman": p.get("karman", slug),
            "autoheal": bool(p.get("autoheal")),
            "dir": AREAS_ROOT / ("p-" + slug),
        })
    return out


def healable_areas() -> list:
    """Области под авто-лечением: **только ЗАПУЩЕННЫЕ**.

    ⚠️ Замечание владельца 18.11: «эти области не запущены и я с ними не работаю
    пока, поэтому ты на них внимание не должен обращать пока я их не запущу и не
    начну там программировать». Значит незапущенные области мы **не смотрим
    вовсе**: ни диагностики, ни лечения, ни строк в журнале — пока владелец их
    не запустит ([D-217](../docs/DECISIONS.md)).

    ⚠️ Конфиг перечитывается **на каждом проходе** — переключатель
    «Автомат / Вручную» в панели меняет поле `autoheal`, и лечащий
    подхватывает это без перезапуска.
    """
    switch = load_switch()
    running = switch.running_areas() or set()
    return [a for a in all_areas()
            if a["autoheal"] and a["dir"].is_dir() and a["dir"].name in running]


def last_install_ts(area_dir: Path) -> int:
    """Когда в область посажен ТЕКУЩИЙ аккаунт (profile.json, updated_at).

    ⚠️ `profile.json` переписывает **сам десктоп** при каждом входе (примерно
    через полминуты после старта) — это время НЕ равно времени установки
    аккаунта. Поэтому для точности опираемся на **поколение записи токена**
    (`generation` в сторе): поколение растёт строго при каждой установке.
    Если мы сами ставили аккаунт (помним поколение и время в файле состояния)
    — берём своё время; если ставил кто-то другой (панель, владелец) — берём
    профиль (чуть позже true-факта, но не раньше).
    """
    try:
        meta = json.loads((area_dir / "profile.json").read_text(encoding="utf-8"))
        ts = str(meta.get("updated_at") or "")
        prof = int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").timestamp() * 1000) if ts else 0
    except (OSError, ValueError, KeyError, TypeError):
        prof = 0
    slug = area_dir.name.replace("p-", "")
    known = (_state.get("install") or {}).get(slug) or {}
    gen = store_generation(area_dir)
    if known and gen and int(known.get("generation") or 0) == int(gen):
        return min(int(known.get("at") or 0) or prof, prof or int(known.get("at") or 0))
    return prof


def store_generation(area_dir: Path) -> int:
    """Поколение текущей записи токена в сторе области (0 — записи нет)."""
    try:
        switch = load_switch()
        rec = (switch.read_store(area_dir).get("records") or {}).get(switch.store_key(area_dir)) or {}
        return int(rec.get("generation") or 0)
    except Exception:  # noqa: BLE001
        return 0


# ────────────────────────────────────────────────────────── детектор

def _rows(con, sql, args=()) -> list:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _area_progress(con) -> int:
    """Когда область последний раз РЕАЛЬНО работала (завершённый ход).

    ⚠️ Только `completed`: ход, который merely НАЧАЛСЯ (`accepted`), — не
    доказательство, что аккаунт жив. Иначе лечащий, послав «Продолжай» после
    отказа, сам собой гасил бы этот отказ (начатый «Продолжай»-ход становился
    «прогрессом» — и область с пустым аккаунтом оставалась стоять, а лечащий
    говорил «жива»; 18.11, Карман: отказ 09:11 → accepted 09:13 → отказ
    потерян).
    """
    row = _rows(con, "select max(accepted_at_ms) from local_runtime_turn_ingress "
                     "where status = 'completed'")
    return int(row[0][0] or 0) if row else 0


def _session_progress(con, session_id: str) -> int:
    """Когда ЭТА сессия последний раз РЕАЛЬНО работала (завершённый ход).

    ⚠️ 18.11: «отказ устарел, потому что область двигалась» надо проверять по
    **своей** сессии, а не по области. Область — это много сессий: если одна
    из них упала по очкам, а другая в это время успела сделать ход, отказ
    первой списывался в «устаревшие» — и область с пустым аккаунтом оставалась
    стоять, а лечащий говорил «жива».
    """
    row = _rows(con, "select max(accepted_at_ms) from local_runtime_turn_ingress "
                     "where session_id = ? and status = 'completed'",
                (session_id,))
    return int(row[0][0] or 0) if row else 0


def _area_progress_db(area_dir: Path) -> int:
    """Когда область последний раз РЕАЛЬНО работала (завершённый ход) — из базы."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return 0
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return 0
    try:
        return _area_progress(con)
    finally:
        con.close()


def _session_progress_db(area_dir: Path, session_id: str) -> int:
    """Когда СЕССИЯ последний раз реально работала (завершённый ход) — из базы."""
    if not session_id:
        return 0
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return 0
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return 0
    try:
        return _session_progress(con, session_id)
    finally:
        con.close()


def _remember_failure(slug: str, at: int, why: str, session_id: str = "") -> None:
    """Запомнить увиденный отказ по очкам.

    ⚠️ Зачем: рантайм стирает `error_code` из строки сессии через пару минут
    после отказа. Без памяти отказ пропадал из вида, и область с пустым
    аккаунтом оставалась стоять, а лечащий говорил «жива» (18.11).

    Сессию запоминаем вместе с отказом: отказ в сессии А гасится только
    работой **той же** сессии А. Ход в соседней сессии области (у другого
    чата) — не доказательство, что аккаунт жив.
    """
    if not at:
        return
    _state.setdefault("failure", {})[slug] = {
        "at": int(at), "why": str(why)[:200], "sid": str(session_id or "")}
    save_cooldowns()


def vendor_balance_failure(area_dir: Path, install: int, since: int):
    """T1: точный вендорский отказ по очкам.

    Возвращает (время_отказа, описание) либо None. Условия:
    - рантайм записал отказ провайдера в сессии (`error_code = 50110`,
      `error_message = "insufficient balance (1008)"`);
    - отказ этой сессии случился **после установки текущего аккаунта**;
    - после отказа область **не двигалась** (нет успешных ходов) — иначе
      отказ устарел: аккаунт сменили, работа пошла.
    """
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        progress = _area_progress(con)
        cands = []
        for sid, code, msg in _rows(
                con,
                "select session_id, error_code, error_message from local_runtime_sessions "
                "where error_code = ? or lower(coalesce(error_message,'')) like ?",
                (BALANCE_CODE, "%" + BALANCE_MARK + "%")):
            row = _rows(con, "select max(accepted_at_ms) from local_runtime_turn_ingress "
                             "where session_id = ? and status = 'failed'", (sid,))
            fail = int(row[0][0] or 0) if row else 0
            if not fail or fail < install or fail < since:
                continue
            if _session_progress(con, sid) > fail:
                continue          # после отказа двигалась ЭТА ЖЕ сессия — отказ устарел
            cands.append((fail, "вендорский отказ в сессии %s (%s)" % (sid[:12], (msg or "").strip()), sid))
        # саб-агенты: фоновая задача, УПАВШАЯ с тем же отказом.
        # Смотрим именно поле lastError задачи, а не весь record_json: в выводе
        # задачи может просто упоминаться фраза (например, наш собственный
        # диагностический скрипт печатает «insufficient balance») — это не отказ.
        for rid, rec, ended in _rows(
                con,
                "select rowid, record_json, coalesce(ended_at_ms, updated_at_ms) "
                "from local_runtime_background_tasks where updated_at_ms >= ?", (install,)):
            try:
                task = json.loads(rec or "{}")
            except ValueError:
                continue
            if str(task.get("status") or "") != "failed":
                continue
            err = (task.get("lastError") or {}).get("message") or ""
            if BALANCE_MARK not in str(err).lower():
                continue
            ts = int(ended or 0)
            if ts < install or ts < since:
                continue
            # Упавшая фоновая задача — тоже «своя» единица: смотрим прогресс её
            # сессии-владельца, а не всей области (иначе ход в другой сессии
            # списывал бы отказ в устаревшие). Владельца не знаем — берём область.
            owner = str(task.get("ownerSessionId") or (task.get("metadata") or {}).get("parentSessionId") or "")
            moved = _session_progress(con, owner) if owner else progress
            if moved and moved > ts:
                continue
            cands.append((ts, "фоновая задача #%s упала по очкам" % rid, owner))
        if cands:
            cands.sort(reverse=True)
            return cands[0]
    finally:
        con.close()
    return None


def silent_failure(area_dir: Path, install: int, since: int):
    """T2: ход упал, не дав ни одного ответа агента, и область застряла.

    Само по себе это ещё не отказ по очкам (так могут выглядеть и другие сбои),
    поэтому требуется **живая проверка** — см. `probe_exhausted`.
    """
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        progress = _area_progress(con)
        for turn_id, ts in _rows(
                con,
                "select turn_id, accepted_at_ms from local_runtime_turn_ingress "
                "where status = 'failed' and accepted_at_ms >= ? and accepted_at_ms >= ? "
                "order by accepted_at_ms desc limit 10", (install, since)):
            if progress and progress > ts:
                return None            # область после отказа двигалась — не наш случай
            said = _rows(con, "select count(*) from local_runtime_message_rows "
                              "where turn_id = ? and role = 'assistant'", (turn_id,))
            if said and int(said[0][0] or 0) > 0:
                continue                # агент что-то ответил — это не отказ провайдера
            return int(ts), "ход %s упал без ответа агента" % turn_id[:8]
    finally:
        con.close()
    return None


def token_expiry_ms(area_dir: Path) -> int:
    """Когда протухнет access-токен области (0 = неизвестно).

    ⚠️ `auth-state.json` после истечения токена остаётся со `status =
    authenticated` — его никто не переписывает. Поэтому «живой» статус
    приложения нельзя считать доказательством живого входа: срок токена —
    единственный честный признак (18.11).
    """
    switch = load_switch()
    exp = 0
    try:
        app = switch.read_app_state(area_dir) or {}
        exp = int(app.get("expiresAtMs") or 0)
    except Exception:  # noqa: BLE001
        exp = 0
    if not exp:
        try:
            rec = (switch.read_store(area_dir).get("records") or {}).get(switch.store_key(area_dir)) or {}
            exp = int(rec.get("expiresAtMs") or 0)
        except Exception:  # noqa: BLE001
            exp = 0
    return exp


def our_account(area_dir: Path) -> bool:
    """Есть ли у области аккаунт, выданный Карманом (значит, его можно переавторизовать)."""
    switch = load_switch()
    try:
        meta = switch.read_meta(area_dir) or {}
    except Exception:  # noqa: BLE001
        meta = {}
    return bool(meta.get("email") or meta.get("karman_account_id"))


def login_lost(area_dir: Path) -> str:
    """T3: вход потерян — область висит незалогиненной.

    Случай 17.11: Карман отдал аккаунт, у которого access-токен жил 9 минут;
    в 09:58:27 он протух, десктоп его отверг и **стёр запись входа** — окно
    осталось незалогиненным. Лечится так же, как отказ по очкам: новый
    аккаунт от Кармана.

    ⚠️ Лечим **только потерянный** вход: если аккаунта у области **никогда**
    не было (нет e-mail в профиле) — это не поломка, а «нужен вход владельца
    в браузере» ([D-217](../docs/DECISIONS.md)), и тратить на него аккаунт из
    общего пула нельзя.

    **18.11 — протухший токен тоже потерянный вход.** Раньше область с истёкшим
    access-токеном вечно считалась «жива»: запись в сторе есть, а
    `auth-state.json` всё ещё говорит `authenticated` (никто его не
    переписывает). Так не лечились Гоньба, Казанская, Тренер и «Матрица РМЗ 2»
    — токены протухли ещё 12–17.11.
    """
    switch = load_switch()
    try:
        meta = switch.read_meta(area_dir)
    except Exception:  # noqa: BLE001
        meta = {}
    if not (meta.get("email") or meta.get("karman_account_id")):
        return ""                      # аккаунта здесь никогда не было — не наш случай
    try:
        rec = (switch.read_store(area_dir).get("records") or {}).get(switch.store_key(area_dir)) or {}
    except Exception:  # noqa: BLE001
        rec = {}
    if not rec or not rec.get("accessToken"):
        return "в сторе области нет записи входа (был %s)" % (meta.get("email") or "?")
    try:
        app = switch.read_app_state(area_dir)
    except Exception:  # noqa: BLE001
        app = {}
    status = str(app.get("status") or "")
    if status and status in LOST_LOGIN_STATES:
        return "приложение считает себя вышедшим (%s)" % status
    # Токен протух, а запись в сторе и «authenticated» в app-state остались.
    exp = token_expiry_ms(area_dir)
    if exp and exp < int(time.time() * 1000):
        return "токен входа протух (до %s)" % _fmt(exp)
    return ""


def wait_activation(area: dict) -> tuple:
    """Дождаться, пока десктоп области поднимется и войдёт новым аккаунтом.

    Зачем (17.11, замечание владельца): раньше «Продолжай» уходил через 3 секунды
    после команды запуска, **пока десктоп ещё грузился**. Два рантайма на одной
    области дрались за сессию, ход падал — и лечащий принимал это за «очки
    кончились» и переключал аккаунт по кругу, **не давая очкам израсходоваться**
    (за день сгорело 77+ аккаунтов).

    Теперь шаги **последовательные**: установили аккаунт → дождались входа →
    отправили «Продолжай» → и только потом судим про баланс.

    Возвращает (ok, activated_at_ms, описание).
    """
    switch = load_switch()
    area_dir = area["dir"]
    deadline = time.time() + ACTIVATE_TIMEOUT
    last = "десктоп не отвечает"
    while time.time() < deadline:
        try:
            app = switch.read_app_state(area_dir)
            rec = (switch.read_store(area_dir).get("records") or {}).get(switch.store_key(area_dir)) or {}
        except Exception:  # noqa: BLE001
            app, rec = {}, {}
        status = str(app.get("status") or "")
        tok = rec.get("accessToken") or ""
        if status == "authenticated" and tok:
            http, _payload = switch.panel(tok)
            if http == 200:
                now = int(time.time() * 1000)
                _state["activated"][area["slug"]] = now
                save_cooldowns()
                return True, now, "десктоп вошёл, вендор подтвердил токен"
        last = "состояние приложения: %s" % (status or "нет")
        time.sleep(ACTIVATION_POLL)
    return False, 0, "десктоп не подтвердил вход за %d с (%s)" % (ACTIVATE_TIMEOUT, last)


def account_anchor(area: dict) -> int:
    """Момент, с которого текущий аккаунт области считается «рабочим».

    Это **не** время установки токена, а время, когда десктоп реально поднялся
    и вошёл. Отказы, случившиеся раньше (шум старта: перезапуск, два рантайма на
    одной области), отказом по очкам считаться не должны — иначе лечащий
    переключает аккаунт по кругу, не давая очкам израсходоваться.
    """
    inst = last_install_ts(area["dir"])
    act = int(_state["activated"].get(area["slug"], 0) or 0)
    return max(inst, act)


def next_karman_reset() -> tuple:
    """Когда у Кармана аккаунты снова станут свободными.

    У Кармана сброс суточного бонуса — **в 00:00 и 03:00 МСК**
    (`DAILY_RESET_HOURS = [0, 3]` в его коде): аккаунт, активированный в
    03:00–24:00, свободен с 03:00 следующих суток, а активированный в
    00:00–03:00 — с 00:00 следующих суток. Значит полный пул восстанавливается
    к 03:00, а НЕ «каждые полчаса» — прежнее понимание было неверным, из-за
    него лечащий опрашивал пул ночью зря и области стояли часами.

    Возвращает (ближайший_сброс, полный_пул).
    """
    now = datetime.now()
    cands = []
    for hour in (0, 3):
        t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if t <= now:
            t = t + timedelta(days=1)
        cands.append(t)
    return min(cands), max(cands)


def pull_account(switch, area: dict):
    """Взять аккаунт у Кармана и посадить его в область.

    **Отклонённый аккаунт — не приговор:** возможно, выпал именно ошибочный, а
    следующий или третий окажется живым (решение владельца 17.11) — поэтому
    пробуем следующий, но не больше трёх попыток подряд. Если пул пуст — пауза
    30 минут (у Кармана каждые полчаса возвращаются в оборот неизрасходованные
    аккаунты, так что за это время что-то может появиться).

    Возвращает (rc, описание). rc = 0 — аккаунт стоит и годен.
    """
    for attempt in range(1, MAX_PULL_TRIES + 1):
        args = argparse.Namespace(area="p-" + area["slug"], apply=True, note=True)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = switch.cmd_pull(args)
        except Exception as exc:  # noqa: BLE001
            return 1, "сбой запроса аккаунта: %s" % exc
        for line in buf.getvalue().strip().splitlines():
            log("%s: %s" % (area["title"], line))
        if rc != 0:
            if rc == 2:
                # Свободных аккаунтов нет. У Кармана аккаунты возвращаются на
                # НОЧНОМ сбросе (00:00 / 03:00 МСК), а не «каждые полчаса»:
                # ждём 10 минут (поймаем, если владелец добавит аккаунты руками)
                # и говорим владельцу, когда пул восстановится сам.
                reset_at, full_at = next_karman_reset()
                _state["backoff"][area["slug"]] = {
                    "until": time.time() + POOL_EMPTY_BACKOFF,
                    "why": "пул Кармана пуст — сброс в %s (полный пул к %s)" % (
                        reset_at.strftime("%d.%m %H:%M"), full_at.strftime("%d.%m %H:%M"))}
                save_cooldowns()
                return rc, ("пул Кармана пуст — аккаунты вернутся к %s, полный пул к %s" % (
                    reset_at.strftime("%d.%m %H:%M"), full_at.strftime("%d.%m %H:%M")))
            if rc == 3:
                # Карман отдал негодный токен — пробуем следующий: возможно, выпал
                # именно ошибочный, а следующий будет живым.
                log("%s: Карман отдал негодный токен — пробую следующий (попытка %s из %s)" % (
                    area["title"], attempt, MAX_PULL_TRIES))
                continue
            # rc == 1 — это отказ или сеть, а НЕ «пул пуст» (пул пуст — это rc == 2
            # с честным текстом выше). Причина отказа напечатана строкой выше:
            # 21.11 сеть была убита прокси, а в журнале читалось «пул пуст».
            return rc, "Карман ответил отказом — причина в строке выше"
        rec = (switch.read_store(area["dir"]).get("records") or {}).get(switch.store_key(area["dir"])) or {}
        exp = int(rec.get("expiresAtMs") or 0)
        life = (exp - time.time() * 1000) / 60000.0
        if life < MIN_TOKEN_LIFE_MIN:
            log("%s: токен протухает (%.0f мин) — пробую следующий" % (area["title"], life))
            continue
        # Токен должен ещё и приниматься вендором: если Карман отдал «не тот»
        # токен (или мёртвый), панель вендора ответит 401 — такой не оставляем.
        tok = rec.get("accessToken") or ""
        status, _payload = switch.panel(tok)
        if status != 200:
            log("%s: вендор не принимает выданный токен (HTTP %s) — пробую следующий" % (
                area["title"], status))
            continue
        return 0, "аккаунт %s, токен жив ещё %.0f мин, вендор отвечает 200" % (
            rec.get("email"), life)
    return 4, "Карман %d раз подряд отдал негодный токен — нужен живой аккаунт" % MAX_PULL_TRIES


def probe_exhausted(area_dir: Path, folder: str) -> str:
    """Живая проверка: отправляем в область короткий ход и смотрим ответ.

    Возвращает ("да"/"нет"/"неясно", пояснение). Проверка нужна для T2: без неё
    мы сожгли бы аккаунт из общего пула Кармана на каждый сбой подряд.
    """
    if not folder or not Path(folder).is_dir():
        return "неясно", "папка проекта недоступна"
    env = dict(os.environ)
    env["MINIMAX_DATA_DIR"] = str(area_dir)
    env["MAVIS_DATA_DIR"] = str(area_dir)
    try:
        proc = subprocess.run([str(MCODE), "exec", "--cwd", folder, "ping"],
                              env=env, capture_output=True, text=True,
                              timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "неясно", "проверка не ответила за %s с" % PROBE_TIMEOUT
    except Exception as exc:  # noqa: BLE001
        return "неясно", "проверка не прошла: %s" % exc
    out = ((proc.stdout or "") + (proc.stderr or "")).lower()
    if BALANCE_MARK in out:
        return "да", "провайдер ответил отказом по очкам"
    if proc.returncode == 0:
        return "нет", "ход прошёл — аккаунт живой"
    return "нет", "ход упал, но не по очкам (rc=%s)" % proc.returncode


def diagnose(area: dict) -> dict:
    """Разбор области: что видно, нужен ли пропуск лечения."""
    out = {"slug": area["slug"], "title": area["title"], "verdict": "жива", "reason": "",
           "install": 0, "failure": 0}
    if not area["dir"].is_dir():
        out["verdict"] = "нет области"
        out["reason"] = "каталога %s нет" % area["dir"]
        return out
    install = account_anchor(area)
    out["install"] = install
    since = int((time.time() - ERROR_WINDOW) * 1000)

    # 0) ПОМЕЧЕННЫЙ отказ — переживает чистку runtime. ⚠️ 18.11: рантайм
    #    стирает `error_code` из строки сессии через пару минут после отказа
    #    (проверено: отказ Кармана в 09:11:24 к 09:2x из базы исчез). После
    #    этого область снова выглядела «живой» — и оставалась стоять с пустым
    #    аккаунтом. Поэтому увиденный отказ запоминаем в состоянии.
    mem = (_state.get("failure") or {}).get(area["slug"]) or {}
    mem_at = int(mem.get("at") or 0)
    if mem_at and mem_at >= install:
        mem_sid = str(mem.get("sid") or "")
        moved = (_session_progress_db(area["dir"], mem_sid) if mem_sid
                 else _area_progress_db(area["dir"]))
        if moved and moved > mem_at:
            # после отказа ОТКАЗАВШАЯ СЕССИЯ отработала — отказ устарел, забываем
            _state.setdefault("failure", {}).pop(area["slug"], None)
            save_cooldowns()
        else:
            out["verdict"] = "ОТКАЗ ПО ОЧКАМ"
            out["failure"] = mem_at
            out["reason"] = mem.get("why") or "отказ по очкам (помечен ранее)"
            out["tier"] = "T1"
            return out

    hit = vendor_balance_failure(area["dir"], install, since)
    if hit:
        out["verdict"] = "ОТКАЗ ПО ОЧКАМ"
        out["failure"] = hit[0]
        out["reason"] = hit[1]
        out["tier"] = "T1"
        _remember_failure(area["slug"], hit[0], hit[1], hit[2] if len(hit) > 2 else "")
        return out
    # ⚠️ ПОРЯДОК ПРОВЕРОК (23.11, разбор «Матрицы РМЗ 2»): «потеря входа»
    # проверяется **ПЕРЕД** «подозрением». «Подозрение» — это про вендора/сеть, и
    # при плохой сети лечащий лечение ОТКЛАДЫВАЕТ; потеря входа — это ФАКТ (в
    # сторе области нет записи входа), и от сети она не зависит.
    #
    # Старый порядок давал «полузависшее»: сеть плохая ⇒ «подозрение» ⇒ лечение
    # отложено ⇒ десктоп тем временем выпал из логина (auth.json с пустыми
    # records, экран «Create an account or log in to continue») ⇒ «ПОТЕРЯН ВХОД»
    # уже не проверялась ⇒ область оставалась мёртвой, и никто её не залогинил.
    lost = login_lost(area["dir"])
    if lost:
        out["verdict"] = "ПОТЕРЯН ВХОД"
        out["reason"] = lost
        out["tier"] = "T3"
        return out
    maybe = silent_failure(area["dir"], install, since)
    if maybe:
        out["verdict"] = "подозрение"
        out["failure"] = maybe[0]
        out["reason"] = maybe[1]
        out["tier"] = "T2"
        return out
    # Токен протух, но аккаунт у области НЕ наш (в профиле нет e-mail) ⇒
    # это «нужен вход владельца в браузере», а не поломка: пул не жжём
    # ([D-217]). Показываем это прямо, а не «жива» (18.11).
    exp = token_expiry_ms(area["dir"])
    if exp and exp < int(time.time() * 1000) and not our_account(area["dir"]):
        out["verdict"] = "нужен вход владельца"
        out["reason"] = ("токен входа протух (до %s), аккаунт не наш — войдите в браузере "
                         "(кнопка «Оживить вход (браузер)»)" % _fmt(exp))
        return out
    out["reason"] = "отказов по очкам не найдено"
    return out


# ────────────────────────────────────────────────────────── лечение

def claim_bonus(switch, area_dir: Path) -> str:
    """Активировать бонусные очки нового аккаунта (claim).

    Важно: **чтение панели очки не начисляет** — начисляет только `claim`
    (`POST …/signin/claim`). Рантайм обычно забирает бонус сам, когда
    начинает работать под аккаунтом, но не всегда (и не успевает при
    мгновенном рестарте) ⇒ делаем claim явно и проверяем результат по панели.
    Claim идемпотентен по суткам: повтор вернёт «уже получено» без очков.
    """
    rec = (switch.read_store(area_dir).get("records") or {}).get(switch.store_key(area_dir)) or {}
    token = rec.get("accessToken") or ""
    if not token:
        return "нет access-токена в сторе"
    url = "%s/claim?timezone_id=%s" % (switch.PANEL_BASE, switch.SIGNIN_TZ)
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="POST",
        data=b"",
    )
    try:
        # Напрямую, мимо локального прокси (см. пояснение в minimax-switch.py:
        # 21.11 мёртвый HTTP-порт прокси ронял и этот запрос тоже).
        with switch._open_direct_then_proxy(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            body = {}
    except Exception as exc:  # noqa: BLE001
        return "claim не прошёл: %s" % exc
    # проверка по панели: клетка «сегодня» должна стать «забрано» (status 3)
    status, payload = switch.panel(token)
    cell = switch.today_cell(payload)
    return "claim=%s, панель %s, клетка %s (status %s, очки %s)" % (
        body.get("claim_result", "?"), status, cell.get("day_no"), cell.get("status"), cell.get("points"))


def desktop_running(slug: str) -> bool:
    """Запущен ли сейчас десктоп области.

    ⚠️ 18.11: откат (перезапуск области) открывал окно десктопа, который
    владелец закрыл, потому что в этой области он не работает. Перезапуск
    должен чинить ТОЛЬКО то, что сломано, — поднимать закрытое окно нельзя.
    """
    try:
        switch = load_switch()
        return ("p-" + slug) in (switch.running_areas() or set())
    except Exception:  # noqa: BLE001
        return True


def area_process_ids(slug: str) -> list:
    """PID'ы процессов десктопа области (главный + дети) — по её каталогу данных."""
    ps = ["powershell", "-NoProfile", "-Command",
          "(Get-CimInstance Win32_Process -Filter \"Name='MiniMax Code.exe'\" | "
          "Where-Object { $_.CommandLine -like '*minimax-areas\\p-%s\\*' }).ProcessId" % slug]
    try:
        out = subprocess.run(ps, capture_output=True, text=True, errors="replace", timeout=40,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        return []
    ids = []
    for line in (out.stdout or "").split():
        if line.strip().isdigit():
            ids.append(int(line.strip()))
    return ids


def wait_area_stopped(slug: str, timeout: int = 30) -> bool:
    """Ушёл ли десктоп области на самом деле (а не только по отчёту скрипта)."""
    deadline = time.time() + timeout
    while True:
        if not area_process_ids(slug):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)


def kill_area_processes(slug: str) -> int:
    """Добить дерево процессов десктопа области (главный + дети)."""
    ids = area_process_ids(slug)
    for pid in ids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, errors="replace", timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return len(ids)


def restart_area(slug: str) -> None:
    """Перезапуск области: стоп → проверка → старт → проверка.

    ⚠️ 23.11, разбор «полузависшего» окна: stop-скрипт **не всегда убивает главный
    процесс** десктопа. Тогда «перезапуск» был фикцией: старый экземпляр оставался
    жив (и висел на экране «Create an account or log in to continue»), а новый
    умирал от single-instance lock — владелец видел окно, которое не вернулось в
    строй. Теперь после каждой команды ПРОВЕРЯЕМ результат:
      - после «стоп» — что процесс действительно ушёл (иначе добиваем дерево);
      - после «старт» — что окно поднялось (иначе «Продолжай» уйдёт в никуда).
    """
    ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File"]
    # ⚠️ CREATE_NO_WINDOW обязателен: лечащий идёт отсоединённо (консоли у него
    # нет), а дочерний powershell без этого флага получает свою консоль — на
    # экране мигает окно (23.11). `-WindowStyle Hidden` — только для PowerShell,
    # окно самой консоли всё равно создаётся.
    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(ps + [str(BIN / "mcode-project-stop.ps1"), "-Project", slug],
                   capture_output=True, text=True, errors="replace", timeout=120,
                   creationflags=hidden)
    if not wait_area_stopped(slug, 30):
        n = kill_area_processes(slug)
        log("СЕССИИ: десктоп %s не остановился stop-скриптом — добиваю дерево (%d процессов)" % (
            slug, n))
        if not wait_area_stopped(slug, 20):
            log("СЕССИИ: десктоп %s всё ещё жив — перезапуск не удался, старт не делаю" % slug)
            return
    time.sleep(3)
    subprocess.run(ps + [str(BIN / "mcode-project-start.ps1"), "-Project", slug],
                   capture_output=True, text=True, errors="replace", timeout=180,
                   creationflags=hidden)
    deadline = time.time() + 60
    while time.time() < deadline:
        if area_process_ids(slug):
            return
        time.sleep(2)
    log("СЕССИИ: десктоп %s не поднялся после старта" % slug)


def broken_subagents(area_dir: Path) -> list:
    """Оборванные субагенты области: работа, которая пропала из-за обрыва.

    Состояние ребёнка определяется его **последним ходом**: `completed` ⇒ работа
    закончена (не трогаем), `failed`/`aborted` ⇒ работа оборвана (будим).

    Факт (17.11): CLI не умеет открывать сессии субагентов («Sub-agent Sessions
    are internal and cannot be opened») — будить ребёнка можно только
    инструментом рантайма `task_append` по `task_id`; он поднимает ребёнка
    с его же контекстом.
    """
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return []
    out = []
    try:
        for rec in _rows(con, "select record_json from local_runtime_background_tasks "
                               "where kind = 'subagent' "
                               "and status in ('failed','canceled','aborted')"):
            try:
                task = json.loads(rec[0] or "{}")
            except ValueError:
                continue
            child = (task.get("metadata") or {}).get("childSessionId")
            if not child:
                continue
            row = _rows(con, "select turn_id, status from local_runtime_turn_ingress "
                             "where session_id = ? order by accepted_at_ms desc limit 1",
                        (child,))
            if not row:
                continue
            if row[0][1] == "completed":
                continue                      # ребёнок закончил — не трогаем
            out.append({"task_id": task.get("taskId"),
                        "description": task.get("description") or "",
                        "child_session": child,
                        "last_turn": row[0][0],
                        "last_turn_status": row[0][1]})
    finally:
        con.close()
    return out


def continue_prompt(area_dir: Path) -> str:
    """«Продолжай» + список оборванных субагентов, которых надо поднять.

    Лечащий вкладывает список в **само сообщение**: агент после перезахода
    сразу видит, кого разбудить через `task_append`, а тех, чью работу он
    уже сделал сам, — пропускает (у него свой контекст).
    """
    slug = area_dir.name.replace("p-", "")
    kids = broken_subagents(area_dir)
    if not kids:
        return "Продолжай"
    lines = ["Продолжай", "",
             "ПОРЯДОК (важно, заказ владельца 18.11):",
             "1) СНАЧАЛА разбуди оборванных субагентов — через task_append "
             "(инструмент рантайма) по task_id из списка ниже; дождись их.",
             "2) ПОТОМ продолжай основной чат.",
             "",
             "Оборванные субагенты (последний ход failed/aborted — работа не закончена):"]
    for kid in kids:
        lines.append("- task_id=%s  «%s»" % (kid["task_id"], kid["description"][:80]))
    lines += ["",
              "Тех, чью работу ты уже сделал сам, не буди. "
              "Полный список — python %s %s" % (ORPHANS, slug)]
    return "\n".join(lines)


def _continue_once(area_dir: Path, folder: str, logfile):
    """Одна попытка отправить «Продолжай» (фоном).

    Возвращает ("ok", описание) — ход пошёл; или ("busy", хвост лога) — сессия
    занята («Session already has an active Turn»); или ("dead", хвост лога) —
    умер по другой причине.

    logfile — **открытый файловый объект** (stdout/stderr для CLI), а не путь:
    Popen не умеет принимать Path как поток. Читать его надо по пути.
    """
    env = dict(os.environ)
    env["MINIMAX_DATA_DIR"] = str(area_dir)
    env["MAVIS_DATA_DIR"] = str(area_dir)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [str(MCODE), "exec", "--continue", "--cwd", folder, continue_prompt(area_dir)]
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=logfile, stderr=logfile,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    except Exception as exc:  # noqa: BLE001
        return "dead", "не запустилось: %s" % exc
    time.sleep(3)
    if proc.poll() is None:
        return "ok", "отправлено (ход идёт в фоне, pid %s, лог %s)" % (
            proc.pid, getattr(logfile, "name", logfile))
    # Процесс умер сразу — читаем, что он написал.
    # ⚠️ logfile здесь бывает НЕ Path, а открытым файловым объектом (TextIOWrapper):
    # у него нет read_text, и вызов .read_text() ронял лечение целиком
    # (18.11 03:04 — «лечение сорвалось: '_io.TextIOWrapper' object has no
    # attribute 'read_text'»). Поэтому читаем ПО ПУТИ, а не по объекту.
    try:
        log_path = logfile if isinstance(logfile, Path) else Path(str(getattr(logfile, "name", "")))
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-3:])
    except (OSError, ValueError):
        tail = ""
    if "active turn" in tail.lower():
        return "busy", tail
    return "dead", tail or ("процесс умер сразу, rc=%s" % proc.returncode)


# ── Зависший ход: «Продолжай» ушёл бы в очередь рантайма ─────────────────────
#
# 18.11 (жалоба владельца): «панель Миниген снова разучилась отправлять слово
# „Продолжай“ поверх очереди запросов — два проекта так и не продолжили сессию
# из-за того, что там в очереди сообщений висел запрос».
#
# Что происходит на самом деле: CLI **не отказывается** на занятом ходе (как
# раньше, «Session already has an active Turn») — он **ставит сообщение в
# очередь рантайма** (`local_runtime_queue_items`, доставка помечена
# `queue-delivery:claim_…`). Очередь разбирается, когда текущий ход закончится,
# поэтому «Продолжай» ждёт зависшего хода и работа не возобновляется.
#
# Сорвать ход из CLI нечем: `mcode exec` умеет только поставить сообщение в
# очередь. Единственный доступный способ — **перезапуск области** (ход умирает,
# сообщение после перезапуска встаёт первым). Но срывать надо ТОЛЬКО зависший
# ход: живая работа агента (долгий ход, сборка) — не повод.
#
# Признак зависания: ход идёт (`status='accepted'`, `completed_at_ms IS NULL`),
# а в сессии давно нет ни одного сообщения (`local_runtime_message_rows`).
# Живой ход сообщения пишет постоянно — это и есть сигнал прогресса.
# ⚠️ `local_runtime_session_agent_state.updated_at_ms` для проверки НЕ годится:
# он не меняется от начала хода до конца (проверено замером 18.11).

# ⚠️ Порог тишины (23.11, правка после ложного перезапуска десктопа Управляющего).
# 120 с было СЛИШКОМ мало: агент может молчать и при живой работе — длинная
# команда (сборка, тесты, ожидание), чтение большого файла, ожидание ответа
# инструмента. В 09:34 лечащий решил, что ход «завис», и перезапустил область,
# хотя беседа работала. Теперь тишина в 10 минут — кандидат на «завис», и даже
# тогда область не перезапускается (см. preempt_stuck): срыв хода перезапуском
# убивает ВСЕ сессии области, включая живую беседу владельца.
STUCK_IDLE_SEC = 600      # ход идёт, но сообщений нет дольше этого ⇒ кандидат на «завис»
WAIT_IDLE_SEC = 30        # сколько ждём освобождения сессии перед отправкой
PREEMPT_PAUSE_SEC = 6     # пауза после перезапуска области перед отправкой


def session_is_idle(area_dir: Path) -> bool:
    """Сессия свободна: нет идущего хода, и рантайм считает её не «started».

    ⚠️ Проверять только «есть активный ход» мало: после падения хода рантайм
    ещё какое-то время держит сессию в «started» (разбор хода), и сообщение,
    отправленное в это окно, **встаёт в очередь** — а очередь разбирается только
    на следующей границе хода (наблюдение 18.11: отправили в 16:50, ход начался
    в 17:02 — 12 минут простоя).
    """
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return True
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return True
    try:
        busy = _rows(con, "select count(*) from local_runtime_turn_ingress "
                          "where status = 'accepted' and completed_at_ms is null")
        if busy and int(busy[0][0] or 0) > 0:
            return False
        rows = _rows(con, "select status from local_runtime_sessions "
                          "order by updated_at_ms desc limit 1")
        return not (rows and str(rows[0][0] or "") == "started")
    finally:
        con.close()


def wait_for_idle(area: dict, seconds: int) -> bool:
    """Подождать, пока сессия освободится. True — освободилась (или ждать нечего).

    Ждать дольше нескольких секунд нельзя: лечащий обходит области по очереди,
    и залипание на одной области подвешивает остальные.
    """
    deadline = time.time() + max(0, seconds)
    while True:
        if session_is_idle(area["dir"]):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(3)


def active_turn(area_dir: Path):
    """Ход, который сейчас идёт в области: (turn_id, session_id, accepted_at_ms)."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        rows = _rows(con, "select turn_id, session_id, accepted_at_ms from local_runtime_turn_ingress "
                          "where status = 'accepted' and completed_at_ms is null "
                          "order by accepted_at_ms desc limit 1")
        return (rows[0][0], rows[0][1], int(rows[0][2] or 0)) if rows else None
    finally:
        con.close()


def session_progress_ms(area_dir: Path, session_id: str) -> int:
    """Когда в сессии последний раз было сообщение — это сигнал прогресса хода."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file() or not session_id:
        return 0
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return 0
    try:
        rows = _rows(con, "select max(created_at_ms) from local_runtime_message_rows "
                          "where session_id = ?", (session_id,))
        return int(rows[0][0] or 0) if rows else 0
    finally:
        con.close()


def session_waits_owner(area_dir: Path, session_id: str) -> bool:
    """Агент ждёт ответа владельца (вопрос) — такой ход срывать нельзя."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file() or not session_id:
        return False
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        rows = _rows(con, "select count(*) from questionnaire_requests "
                          "where session_id = ? and answered_at is null and dismissed_at is null",
                     (session_id,))
        return bool(rows and int(rows[0][0] or 0) > 0)
    finally:
        con.close()


def turn_is_delivery(area_dir: Path, turn_id: str) -> bool:
    """Ход **доставки** сообщения в очередь занятой сессии — это не «зависание».

    Почему это важно (23.11, разбор ложного перезапуска десктопа Управляющего):
    когда «Продолжай» отправляют в ЗАНЯТУЮ сессию, рантайм создаёт отдельный
    ход доставки (`turn_task_delivery`). Он висит ровно столько, сколько занята
    сессия, и **по определению молчит** — сообщений он не пишет. Лечащий брал
    его за «зависший ход» (тишина > 120 с), считал, что «Продолжай» не
    доставился, и перезапускал область — убивая живую беседу.

    Теперь ход доставки зависанием **не считается никогда**.
    """
    if not turn_id:
        return False
    low = str(turn_id).lower()
    if "delivery" in low or "deliver" in low:
        return True
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return False
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        rows = _rows(con, "select source, claim_source from local_runtime_turn_ingress "
                          "where turn_id = ?", (turn_id,))
        if not rows:
            return False
        src = " ".join(str(x or "").lower() for x in rows[0])
        return "delivery" in src
    finally:
        con.close()


def stuck_turn(area: dict):
    """Сессия висит на ходе, который не идёт: (turn_id, простой_секунд) либо None.

    None — либо хода нет, либо ход живой (сообщения идут), либо агент ждёт
    ответа владельца, либо это ход **доставки** в очередь (он молчит по
    определению — зависанием не считается).
    """
    at = active_turn(area["dir"])
    if not at:
        return None
    turn_id, sid, accepted = at
    if session_waits_owner(area["dir"], sid):
        return None
    if turn_is_delivery(area["dir"], turn_id):
        return None
    prog = session_progress_ms(area["dir"], sid)
    idle_ms = int(time.time() * 1000) - int(prog or accepted)
    if idle_ms < STUCK_IDLE_SEC * 1000:
        return None
    return (turn_id, int(idle_ms // 1000))


def active_turn_failed(area_dir: Path) -> bool:
    """Активный ход УПАЛ (failed/aborted), а не просто молчит."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return False
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        rows = _rows(con, "select status from local_runtime_turn_ingress "
                          "where status in ('failed','aborted') and completed_at_ms is null "
                          "order by accepted_at_ms desc limit 1")
        return bool(rows)
    finally:
        con.close()


def preempt_stuck(area: dict, why: str) -> bool:
    """Сорвать зависший ход, чтобы «Продолжай» встал в начало, а не в очередь.

    Перезапуск области — единственный доступный способ сорвать ход (CLI не умеет
    прерывать). Окно при этом закрывается и открывается заново — поэтому делаем
    это только когда ход действительно висит, и только в работающей области
    (правило владельца 18.11: окно в неработающей области не открываем).

    ⚠️ 23.11, правка после ложного перезапуска: **перезапуск области — крайняя
    мера**, он убивает ВСЕ сессии области, включая живую беседу владельца.
    Поэтому применяем его только когда ход **УПАЛ** (failed/aborted), а не когда
    он просто молчит: молчание — норма для длинной работы (сборка, тесты,
    ожидание инструмента). Молчащий ход мы оставляем в покое.
    """
    slug = area["slug"]
    if not active_turn_failed(area["dir"]):
        log("%s: %s — ход не упал, просто молчит; десктоп НЕ трогаю (перезапуск "
            "убил бы живые сессии области)" % (area["title"], why))
        return False
    if not desktop_running(slug):
        log("%s: %s, но десктоп не запущен — окно не открываем" % (area["title"], why))
        return False
    log("%s: %s — срываю его перезапуском области, чтобы «Продолжай» встал в начало" % (
        area["title"], why))
    restart_area(slug)
    time.sleep(PREEMPT_PAUSE_SEC)
    ok, _at, why2 = wait_activation(area)
    if not ok:
        log("%s: после перезапуска вход не подтверждён — %s" % (area["title"], why2))
    return ok


def say_continue(area: dict) -> str:
    """Отправить «Продолжай» в последнюю сессию области — **в начало, а не в очередь**.

    Нюанс (17.11, замечание владельца): если в сессии **идёт ход**, CLI
    отказывается — «Session already has an active Turn. Use queue send to
    deliver the message after it». Ждать конца чужого хода нельзя (область
    должна продолжить работу сразу), поэтому мы **срываем зависший ход
    перезапуском области** и отправляем «Продолжай» заново: сообщение встаёт
    первым.

    Фоновый запуск, не ждём: ход длинный, ждать его в цикле лечения нельзя —
    остальные области зависнут.
    """
    area_dir = area["dir"]
    folder = area.get("folder") or ""
    if not folder or not Path(folder).is_dir():
        return "папка проекта недоступна — «Продолжай» не отправляли"
    # «Продолжай» НЕЛЬЗЯ отправлять в занятую сессию: CLI не отказывается, а
    # ставит сообщение в очередь рантайма, и очередь разбирается только на
    # следующей границе хода (наблюдение 18.11: отправили в 16:50 — ход
    # стартовал лишь в 17:02, 12 минут простоя).
    #
    # Три состояния, и каждое — своё действие:
    # 1) ход ВИВЫЙ (сообщения идут) — не мешаем работающему агенту: сообщение
    #    встанет в очередь и отработает после хода, это правильно;
    # 2) ход ЗАВИС (сообщений нет долго) — срываем его перезапуском области,
    #    чтобы «Продолжай» встал в начало, а не ждал конца висящего хода;
    # 3) хода нет, но рантайм ещё считает сессию занятой (разбор хода) — ждём
    #    освобождения; не освободилась — перезапуск чистит заевшее состояние.
    at = active_turn(area["dir"])
    stuck = stuck_turn(area) if at else None
    if stuck:
        preempt_stuck(area, "ход %s висит (сообщений нет %d с)" % (stuck[0][:12], stuck[1]))
    elif at:
        log("%s: сессия занята живым ходом — «Продолжай» встанет в очередь "
            "и отработает после него" % area["title"])
    elif not wait_for_idle(area, WAIT_IDLE_SEC):
        preempt_stuck(area, "сессия не освободилась (ходов нет, а рантайм считает её занятой)")
    logfile = area_dir / "logs" / "continue.log"
    try:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = open(logfile, "a", encoding="utf-8")
    except OSError:
        fh = subprocess.DEVNULL
    try:
        for attempt in (1, 2):
            status, info = _continue_once(area_dir, folder, fh)
            if status == "ok":
                return info
            if status == "busy":
                if not desktop_running(area["slug"]):
                    log("%s: сессия занята ходом, но десктоп не запущен — «Продолжай» не отправляем "
                        "(окно не открываем: владелец в этой области не работает)" % area["title"])
                    return "не отправлено: десктоп не запущен"
                log("%s: сессия занята ходом — срываю его перезапуском области, "
                    "чтобы «Продолжай» встал в начало (попытка %s)" % (
                        area["title"], attempt))
                restart_area(area["slug"])
                # ⚠️ 23.11: после перезапуска команда «Продолжай» уходила через
                # 6 секунд — а десктоп после старта грузится и входит десятки
                # секунд. Команда приходила в ещё не готовый рантайм, терялась,
                # и лечащий уходил в ещё один перезапуск (каскад «ОТКАТ» в 00:30,
                # 00:35, 01:12, 01:26 — команды накладывались друг на друга).
                # Теперь: ЖДЁМ готовности (приложение поднялось + вошло + вендор
                # подтвердил токен) и только потом шлём следующую команду.
                ok, _at, why = wait_activation(area)
                if not ok:
                    log("%s: после перезапуска приложение не подтвердило вход (%s) — "
                        "«Продолжай» пока не отправляю" % (area["title"], why))
                    return "не отправлено: десктоп не готов (%s)" % why
                log("%s: десктоп поднялся и вошёл — повторяю «Продолжай»" % area["title"])
                time.sleep(PREEMPT_PAUSE_SEC)
                continue
            return "не отправлено: %s" % info
        # Сессия занята и после перезапуска — последний путь: новая сессия.
        env = dict(os.environ)
        env["MINIMAX_DATA_DIR"] = str(area_dir)
        env["MAVIS_DATA_DIR"] = str(area_dir)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([str(MCODE), "exec", "--cwd", folder, continue_prompt(area_dir)],
                                env=env, stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                                creationflags=flags)
        time.sleep(3)
        if proc.poll() is None:
            return "сессия была занята — отправил «Продолжай» в НОВУЮ сессию (pid %s)" % proc.pid
        return "сессия занята и новая сессия не поднялась — ждём следующего прохода"
    finally:
        if fh is not subprocess.DEVNULL:
            try:
                fh.close()
            except OSError:
                pass


def latest_turn(area_dir: Path):
    """Последний ход в рантайме области: (turn_id, status, accepted_at_ms)."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        rows = _rows(con, "select turn_id, status, accepted_at_ms from local_runtime_turn_ingress "
                           "order by accepted_at_ms desc limit 1")
        return (rows[0][0], rows[0][1], int(rows[0][2] or 0)) if rows else None
    finally:
        con.close()


def turn_after(area_dir: Path, before_ms: int):
    """Ход, начавшийся ПОСЛЕ отметки before_ms (или None)."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return None
    try:
        rows = _rows(con, "select turn_id, status, accepted_at_ms from local_runtime_turn_ingress "
                           "where accepted_at_ms > ? order by accepted_at_ms desc limit 1",
                     (int(before_ms),))
        return (rows[0][0], rows[0][1], int(rows[0][2] or 0)) if rows else None
    finally:
        con.close()


def probe_pending(area: dict) -> bool:
    """Разбираемся с пробным ходом после смены аккаунта без перезапуска.

    True — область занята пробой (диагностику пропускаем), False — можно лечить.
    """
    slug = area["slug"]
    pend = (_state.get("probe_turn") or {}).get(slug)
    if not pend:
        return False
    if time.time() - float(pend.get("at") or 0) > PROBE_TTL:
        _state["probe_turn"].pop(slug, None)
        save_cooldowns()
        log("%s: пробный ход устарел (%d мин) — возвращаюсь к обычной проверке" % (
            area["title"], PROBE_TTL // 60))
        return False
    turn = turn_after(area["dir"], int(pend.get("before_ms") or 0))
    if turn is None:
        # Хода нет: то ли он ещё не начался (сессия была занята), то ли рантайм
        # его не принял. Долго ждать нельзя — область стоит.
        age = time.time() - float(pend.get("at") or 0)
        # Ждём полный таймаут, только если ход живой. Если ход висит (нет
        # сообщений) — «Продолжай» лежит в очереди за ним, и ждать бессмысленно:
        # срываем ход сейчас.
        stuck_now = stuck_turn(area)
        if age < PROBE_NO_TURN_TIMEOUT and not stuck_now:
            return True      # ещё рано — подождём следующего прохода
        live = active_turn(area["dir"])
        if live and not stuck_now:
            # Живой ход: «Продолжай» стоит в очереди за ним и отработает после
            # него. «Нового хода нет» — не значит «сессия мертва»: перезапуск
            # десктопа тут срывал бы живую работу (23.11, 01:09 — так был
            # перезапущен рабочий десктоп «Матрицы РМЗ»).
            log("%s: «Продолжай» стоит в очереди за живым ходом %s — жду, "
                "десктоп не трогаю"
                % (area["title"], (live[1] if len(live) > 1 else "?")[:12]))
            return True
        if stuck_now:
            log("%s: «Продолжай» лежит в очереди за зависшим ходом %s (сообщений нет %d с) — "
                "жду дальше только бессмысленно" % (area["title"], stuck_now[0][:12], stuck_now[1]))
            # ⚠️ 23.11: ЗДЕСЬ БЫЛ НЕТ `return` ⇒ выполнение падало в «ОТКАТ:
            # перезапускаю область», и десктоп перезапускался на живом ходе.
            # В 09:34 так был перезапущен десктоп Управляющего «ни с чего ни с
            # сего», пока владелец смотрел на свою рабочую беседу.
            #
            # Теперь: молчащий (или висящий в очереди) ход — НЕ повод трогать
            # десктоп. Откат с перезапуском остаётся только для настоящего
            # случая «доставка не удалась» — когда активного хода нет вовсе.
            _state["probe_turn"].pop(slug, None)
            save_cooldowns()
            return True
        _state["probe_turn"].pop(slug, None)
        save_cooldowns()
        log("%s: «Продолжай» без перезапуска не доставился (нового хода нет %d с) — "
            "ОТКАТ: перезапускаю область и повторяю" % (area["title"], int(age)))
        if not desktop_running(slug):
            # Десктоп закрыт (владелец в этой области не работает) — окно не
            # открываем. Вход уже обновлён, «Продолжай» дойдёт, когда владелец
            # сам запустит область (18.11: так открылось лишнее окно ВМалмыже).
            log("%s: десктоп не запущен — откат без перезапуска, окно не открываем" % area["title"])
            return False
        restart_area(slug)
        ok, _at, why = wait_activation(area)
        if not ok:
            log("%s: после перезапуска вход не подтверждён — %s" % (area["title"], why))
            return False
        _state["activated"][slug] = int(time.time() * 1000)
        save_cooldowns()
        res = say_continue(area)
        log("%s: «Продолжай» после перезапуска — %s" % (area["title"], res))
        return False
    status = turn[1]
    if status == "completed":
        _state["probe_turn"].pop(slug, None)
        save_cooldowns()
        log("%s: десктоп принял новый вход БЕЗ перезапуска (ход %s завершён) — сессия не прерывалась" % (
            area["title"], turn[0][:8]))
        return False
    if status in ("failed", "aborted"):
        _state["probe_turn"].pop(slug, None)
        save_cooldowns()
        # Сколько ход шёл до падения? Короткое падение ⇒ десктоп отверг старый
        # токен (нужен откат). Долгое ⇒ новый аккаунт израсходовался (обычное
        # лечение, перезапуск не нужен и только тратит время).
        ran_ms = int(turn[2] or 0) - int(float(pend.get("at") or 0) * 1000)
        if ran_ms > PROBE_FAIL_FAST * 1000:
            log("%s: пробный ход %s шёл %d мин и упал — это не «десктоп отверг токен», "
                "а очки кончились: лечу обычным порядком (следующий аккаунт), "
                "перезапуск не нужен" % (area["title"], turn[0][:8], int(ran_ms // 60000)))
            return False
        log("%s: десктоп новый вход без перезапуска не взял (ход %s: %s, упал сразу) — "
            "ОТКАТ: перезапускаю область и повторяю «Продолжай»" % (
                area["title"], turn[0][:8], status))
        if not desktop_running(slug):
            log("%s: десктоп не запущен — откат без перезапуска, окно не открываем" % area["title"])
            return False
        restart_area(slug)
        ok, _at, why = wait_activation(area)
        if not ok:
            log("%s: после перезапуска вход не подтверждён — %s" % (area["title"], why))
            return False
        # Отказы пробного хода — не про очки: двигаем якорь, чтобы лечащий
        # не счёл их отказом по балансу и не покусил ещё один аккаунт.
        _state["activated"][slug] = int(time.time() * 1000)
        save_cooldowns()
        res = say_continue(area)
        log("%s: «Продолжай» после перезапуска — %s" % (area["title"], res))
        return False
    return True              # ход ещё идёт — ждём


def renew(switch, area: dict, burst: list = None) -> tuple:
    """Поставить области новый аккаунт и продолжить её работу.

    **Общий путь** для автоматического лечения и для кнопки «Новый вход
    (токен)» в панели.

    Смена аккаунта = смена логина. Перезагрузка десктопа при этом **НЕ**
    делается (заказ владельца 19.11, ночь: «убери перезагрузку приложения
    минимакса при смене аккаунта — это не помогает для разморозки залипшего
    чата»): новый access и состояние кладутся в токен-стор области (поколение
    выше, чем у приложения), и рантайм подхватывает их на следующем запросе.

    ⚠️ ИСКЛЮЧЕНИЕ (23.11): если десктоп **без входа** (стор пуст, экран «Create
    an account or log in to continue») — новый токен ему некуда положить. Тогда
    перезапуск НУЖЕН, и за ним обязательно идёт проверка входа
    (`wait_activation`): «Продолжай» уходит только в подтверждённо готовый
    десктоп. Это и есть лечение «из логина вышли, а обратно не зашли».

    Дальше сообщение «Продолжай» уходит сначала в субагенты (если они есть и если
    они были прерваны), а потом в основной чат.

    Порядок «субагенты → основной чат» задан в самом тексте сообщения
    (`continue_prompt`).

    Возвращает (ok, описание).
    """
    slug = area["slug"]
    last = _state["last_heal"].get(slug, 0)
    gap = time.time() - last
    # ⚠️ Состояние входа запоминаем ДО установки нового токена (23.11): после
    # установки стор непуст и app-state записан «authenticated» ⇒ проверка
    # «десктоп без входа» уже ничего не видит, а десктоп при этом может
    # продолжать показывать экран «Create an account or log in to continue».
    was_logged_out = bool(login_lost(area["dir"]))
    rc, why = pull_account(switch, area)
    if rc != 0:
        log("%s: %s — ждём, повторов не гоним" % (area["title"], why))
        return False, why
    log("%s: аккаунт получен (%s)" % (area["title"], why))
    _state["pulls"].setdefault(slug, []).append(time.time())
    _state["pulls"][slug] = [t for t in _state["pulls"][slug] if time.time() - t < 86400]
    # Счётчик «активационной лавины»: 5+ аккаунтов на проект за 3 минуты ⇒
    # аларм и остановка (проверка выше).
    now_b = time.time()
    window = [t for t in (_state.get("burst") or {}).get(slug, []) if now_b - t < BURST_WINDOW]
    _state.setdefault("burst", {})[slug] = window + [now_b]

    _state["last_heal"][slug] = time.time()
    # Отказ, ради которого лечили, снят: новый аккаунт поставлен, старый
    # отказ больше не должен висеть в памяти (иначе область «застрянет» на
    # вердикте «ОТКАЗ ПО ОЧКАМ»).
    _state.setdefault("failure", {}).pop(slug, None)
    if last and gap < 900:
        log("%s: прошлый аккаунт прожил всего %d мин — активность тяжёлая, "
            "пул Кармана горит быстро" % (area["title"], int(gap // 60)))
    # Помним, какое поколение токена поставили и когда: это точный якорь
    # «текущего аккаунта» (profile.json переписывает сам десктоп при входе).
    _state["install"][slug] = {"generation": store_generation(area["dir"]),
                                "at": int(time.time() * 1000)}
    save_cooldowns()

    # Бонусные очки нового аккаунта — до «Продолжай», чтобы агент начал
    # работать уже с очками (claim идемпотентен по суткам).
    res = claim_bonus(switch, area["dir"])
    log("%s: бонусные очки — %s" % (area["title"], res))

    # Смена аккаунта = СМЕНА ЛОГИНА. Перезагрузки десктопа НЕТ.
    #
    # Заказ владельца 19.11 (ночь): «убери перезагрузку приложения минимакса при смене
    # аккаунта — это не помогает для разморозки залипшего чата, я нашёл другой способ его
    # разморозки, вручную». Значит — как 18.11: новый access и состояние кладутся в
    # токен-стор области (поколение выше, чем у приложения), и рантайм подхватывает их на
    # следующем запросе. Интерфейс может остаться в старой сессии — залипший чат
    # размораживает владелец, руками; лишний рестарт области тут не помогает.
    #
    # ⚠️ ИСКЛЮЧЕНИЕ (23.11): если десктоп **без входа** (стор пуст, экран «Create an
    # account or log in to continue») — новый токен ему некуда положить, и «Продолжай»
    # уйдёт в пустоту. Вот тут перезапуск НУЖЕН: десктоп должен войти новым
    # аккаунтом. И это ровно тот случай, который владелец видел как «из логина вышли,
    # а обратно не зашли» (область «Матрица РМЗ 2», 23.11 09:40).
    lost = login_lost(area["dir"])
    if was_logged_out or lost:
        why_lost = lost or "десктоп был без входа"
        log("%s: десктоп без входа (%s) — перезапускаю, чтобы он вошёл новым аккаунтом" % (
            area["title"], why_lost))
        if desktop_running(slug):
            restart_area(slug)
            ok, _at, why = wait_activation(area)
            if not ok:
                log("%s: после перезапуска вход НЕ подтверждён — %s (область останется "
                    "на экране входа; нужен вход владельца или новый аккаунт)" % (
                        area["title"], why))
                return False, "вход не подтверждён: %s" % why
            log("%s: вход подтверждён после перезапуска" % area["title"])
        else:
            log("%s: десктоп не запущен — окно не открываем; когда владелец запустит "
                "область, она войдёт новым входом" % area["title"])
    _state["activated"][slug] = int(time.time() * 1000)
    save_cooldowns()
    if area["dir"].name not in (switch.running_areas() or set()):
        log("%s: десктоп не запущен — вход обновлён, «Продолжай» не отправляли "
            "(живой сессии нет; когда запустите область, она войдёт новым входом)" % area["title"])
        return True, "вход обновлён, десктоп не запущен — «Продолжай» не отправляли"
    before = latest_turn(area["dir"])
    before_ms = before[2] if before else 0
    res = say_continue(area)
    log("%s: «Продолжай» без перезагрузки — %s" % (area["title"], res))
    _state["probe_turn"][slug] = {"before_ms": before_ms, "at": time.time()}
    save_cooldowns()
    return True, res


def network_unhealthy() -> str:
    """Сеть сейчас сломана? Тогда «подозрение» лечить нельзя.

    Замер 21.11.2026: прокси-переменные указывали на мёртвый порт `10809`
    (живой — `10808`), и **все** живые проверки падали с «Connection error».
    Лечащий видел это как «возможно, кончились очки» и лез бы лечить аккаунт,
    хотя аккаунт был цел. Теперь перед живой проверкой спрашиваем сетевого
    сторожа: если сеть/прокси сломаны — проверку не делаем и ждём.

    Возвращает описание поломки (пусто — сеть в порядке).
    """
    try:
        state = json.loads((Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web" /
                            "netwatch-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    last = state.get("last_run") or {}
    if last.get("stable") and not (last.get("proxy") or {}).get("broken"):
        return ""
    proxy = last.get("proxy") or {}
    dead = proxy.get("dead") or []
    if dead:
        return "прокси-переменные сломаны: %s" % ", ".join(dead[:3])
    if last.get("action") and last.get("action") not in ("всё живо", "нет"):
        return "сеть: %s" % last.get("action")
    if not last:
        return ""
    return "сеть: устойчивый доступ не подтверждён"


def heal(area: dict, switch, force: bool = False) -> bool:
    slug = area["slug"]
    # Сначала — есть ли что лечить. Иначе в журнал идёт «пропуск — кулдаун»
    # даже для исправной области, и это выглядит как «автоматика пропустила».
    diag = diagnose(area)
    if diag["verdict"] not in ("ОТКАЗ ПО ОЧКАМ", "ПОТЕРЯН ВХОД", "подозрение"):
        # Молчим, если ничего не изменилось: при паузе 15 с иначе журнал
        # забьётся одинаковыми строками и спрячет важное.
        prev = _last_verdict.get(slug)
        _last_verdict[slug] = diag["verdict"]
        if force or prev != diag["verdict"]:
            if diag["verdict"] == "нужен вход владельца":
                log("%s: %s" % (area["title"], diag["reason"]))
            else:
                log("%s: лечить нечего (%s)" % (area["title"], diag["reason"]))
        return False
    _last_verdict[slug] = diag["verdict"]

    # ── Аларм на «активационную лавину» ───────────────────────────────────────
    # Решение владельца 17.11: лимитов на выдачу НЕ вводим (нечего ждать
    # столько), но если на один проект за 3 минуты активируются 5+ аккаунтов —
    # это признак, что что-то пошло не так ⇒ активацию останавливаем и
    # предупреждаем оператора (в панели — всплывающее окно).
    now = time.time()
    bo = _state["backoff"].get(slug) or {}
    if not force and float(bo.get("until") or 0) > now:
        # Не спамим: «пауза до …» пишем только когда пауза ИЗМЕНИЛАСЬ (конец
        # сдвинулся или сменилась причина). Иначе журнал забивается одной
        # строкой каждые 10 секунд (18.11 — 1000+ таких строк за ночь).
        until = int(float(bo["until"]) * 1000)
        if _state.get("pause_notice", {}).get(slug) != until:
            _state.setdefault("pause_notice", {})[slug] = until
            save_cooldowns()
            log("%s: пауза до %s (%s)" % (
                area["title"], _fmt(until), bo.get("why", "ожидание")))
        return False
    burst = [t for t in (_state.get("burst") or {}).get(slug, []) if now - t < BURST_WINDOW]
    if len(burst) >= BURST_LIMIT and not force:
        text = ("%s: за %d минут активировано %d аккаунтов — активация ОСТАНОВЛЕНА, "
                "разберись, что происходит" % (area["title"], BURST_WINDOW // 60, len(burst) + 1))
        _state["backoff"][slug] = {"until": now + POOL_EMPTY_BACKOFF,
                                    "why": "аларм: активационная лавина"}
        _state["alarm"] = {"slug": slug, "area": area["title"], "text": text,
                           "at": int(now * 1000)}
        save_cooldowns()
        log("АЛАРМ: %s" % text)
        return False

    last = _state["last_heal"].get(slug, 0)
    gap = time.time() - last
    if not force and gap < COOLDOWN:
        # Отказ **новее** нашего последнего лечения ⇒ тот аккаунт, который мы
        # поставили, уже мёртв, и ждать кулдаун нельзя: область стоит. Именно
        # это случилось 17.11 с Мозгом: в 11:00 мы вылечили его (id=30), тот
        # аккаунт сдох за 5 минут, и ещё 25 минут лечащий молчал «кулдаун».
        fresh = (diag["verdict"] == "ПОТЕРЯН ВХОД") or (diag.get("failure", 0) > last * 1000)
        if not (fresh and gap >= EARLY_REHEAL_GAP):
            log("%s: пропуск — кулдаун (лечили %d с назад, осталось %d с; вердикт: %s)" % (
                area["title"], int(gap), int(COOLDOWN - gap), diag["verdict"]))
            return False
        log("%s: лечу РАНЬШЕ СРОКА — аккаунт, который поставили, уже негоден "
            "(прошло %d с), ждать кулдаун значит держать область мёртвой" % (
                area["title"], int(gap)))

    if diag["verdict"] == "подозрение" and not force:
        # ⚠️ Сеть сломана ⇒ «подозрение» может быть не про аккаунт, а про
        # прокси (21.11: все живые проверки падали с «Connection error» из-за
        # мёртвого порта 10809). Спрашиваем сетевого сторожа и ждём.
        net_problem = network_unhealthy()
        if net_problem:
            log("%s: подозрение, но сеть не в порядке (%s) — живую проверку не делаю, "
                "лечение откладываю" % (area["title"], net_problem))
            return False
        # Живая проверка стоит реального хода — при паузе 15 с её нельзя
        # делать каждый проход, иначе сама проверка станет источником нагрузки.
        last_probe = _state["probe"].get(slug, 0)
        if time.time() - last_probe < PROBE_COOLDOWN:
            log("%s: подозрение, но живую проверку делали %d с назад — не повторяю" % (
                area["title"], int(time.time() - last_probe)))
            return False
        _state["probe"][slug] = time.time()
        save_cooldowns()
        verdict, why = probe_exhausted(area["dir"], area.get("folder", ""))
        log("%s: проверка вживую — %s (%s)" % (area["title"], verdict, why))
        if verdict != "да":
            log("%s: подтверждения отказа по очкам нет — не лечу" % area["title"])
            return False
        diag["verdict"] = "ОТКАЗ ПО ОЧКАМ"

    if diag["verdict"] not in ("ОТКАЗ ПО ОЧКАМ", "ПОТЕРЯН ВХОД"):
        log("%s: лечить нечего (%s)" % (area["title"], diag["reason"]))
        return False

    log("%s: %s (%s) — беру следующий аккаунт у Кармана" % (
        area["title"], diag["verdict"], diag["reason"]))
    ok, _why = renew(switch, area, burst)
    return ok


# ────────────────────────────────────────────────────────── режимы

def show_status() -> int:
    areas = all_areas()
    switch = load_switch()
    running = switch.running_areas() or set()
    print("МиниГен — авто-лечение: %s" % datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    print("%-13s %-9s %-8s %-13s %-13s %s" % (
        "область", "присмотр", "аккаунт", "установлен", "последний отказ", "вердикт"))
    for area in areas:
        watch = "автомат" if area["autoheal"] else "вручную"
        # Незапущенные области лечащий НЕ смотрит (замечание владельца 18.11):
        # вердикт у них был бы враньём — показываем, что они вне присмотра.
        if area["dir"].name not in running:
            print("%-13s %-9s %-8s %-13s %-13s %s" % (
                area["title"], watch,
                "есть" if area["dir"].is_dir() else "—",
                "—", "—", "не запущена — не смотрим"))
            continue
        diag = diagnose(area)
        print("%-13s %-9s %-8s %-13s %-13s %s" % (
            area["title"], watch,
            "есть" if area["dir"].is_dir() else "—",
            _fmt(diag["install"]), _fmt(diag["failure"]),
            diag["verdict"] if diag["verdict"] != "жива" else "жива — отказов нет"))
    print()
    for area in areas:
        last = _state["last_heal"].get(area["slug"], 0)
        if last:
            left = max(0, int(COOLDOWN - (time.time() - last)))
            print("  %s: лечили %s, кулдаун ещё %s с" % (
                area["title"], _fmt(int(last * 1000)), left))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="МиниГен: авто-лечение областей MiniMax")
    ap.add_argument("--once", action="store_true", help="один проход и выход")
    ap.add_argument("--dry-run", action="store_true", help="только диагностика, ничего не лечить")
    ap.add_argument("--status", action="store_true", help="показать состояние областей и выйти")
    ap.add_argument("--force", action="store_true", help="лечить принудительно (для проверки вручную)")
    ap.add_argument("--interval", type=int, default=0,
                    help="пауза между проходами, сек (0 = FAST_INTERVAL)")
    ap.add_argument("--area", default="",
                    help="вылечить ТОЛЬКО эту область по команде владельца "
                         "(кнопка «Новый вход (токен)» в панели): тот же путь, "
                         "что и автоматическое лечение, но без диагностики и кулдауна")
    args = ap.parse_args()

    switch = load_switch()
    load_cooldowns()

    if args.status:
        return show_status()

    areas = healable_areas()
    log("МиниГен-автолечение: областей под присмотром %d (%s)" % (
        len(areas), ", ".join(a["slug"] for a in areas) or "—"))

    while True:
        # Конфиг перечитываем на каждом проходе: переключатель
        # «Автомат / Вручную» в панели меняет поле `autoheal` на ходу.
        if args.area:
            # Ручная команда владельца («Новый вход (токен)» в панели):
            # лечим ТОЛЬКО эту область и НЕ по расписанию, даже если её
            # десктоп не запущен (владелец позвал — значит, он в ней работает).
            areas = [a for a in all_areas() if a["slug"] == args.area]
            if not areas:
                log("область %s не найдена в конфиге" % args.area)
                return 1
        else:
            areas = healable_areas()
        now = time.time()
        for area in areas:
            if not area["dir"].is_dir():
                continue
            # Своё расписание у каждой области: живая проверяется каждые
            # FAST_INTERVAL секунд, а только что вылеченная — не раньше чем
            # через HEAL_QUIET (3 минуты): сразу после лечения ловить нечего.
            due = _next_check.get(area["slug"], 0)
            if now < due:
                continue
            _next_check[area["slug"]] = time.time() + FAST_INTERVAL
            if args.dry_run:
                try:
                    diag = diagnose(area)
                    log("%s: %s — %s" % (area["title"], diag["verdict"], diag["reason"]))
                except Exception as exc:  # noqa: BLE001
                    log("%s: диагностика не удалась: %s" % (area["title"], exc))
                continue
            try:
                if args.area:
                    # Ручное лечение: тот же путь, что и автоматическое, но
                    # без диагностики и кулдауна (владелец позвал сам).
                    log("%s: ручное лечение по команде владельца" % area["title"])
                    ok, why = renew(switch, area)
                    log("%s: %s" % (area["title"], why))
                    _next_check[area["slug"]] = time.time() + HEAL_QUIET
                    continue
                if probe_pending(area):
                    continue          # ждём разбора пробного хода
                # diagnose() внутри heal() — второй раз не читаем: проверка
                # локальная, но при короткой паузе лишние чтения не нужны.
                healed = heal(area, switch, force=args.force)
            except Exception as exc:  # noqa: BLE001
                log("%s: лечение сорвалось: %s" % (area["title"], exc))
                continue
            if healed:
                _next_check[area["slug"]] = time.time() + HEAL_QUIET
        if args.once or args.dry_run:
            break
        time.sleep(max(5, args.interval if args.interval else FAST_INTERVAL))
    return 0


if __name__ == "__main__":
    sys.exit(main())
