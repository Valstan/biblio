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
from datetime import datetime
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
POOL_EMPTY_BACKOFF = 1800 # Карман ответил «свободных аккаунтов нет» — не
                          # спрашивать 30 минут (у Кармана каждые полчаса
                          # возвращаются в оборот неизрасходованные аккаунты)
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

_state = {"last_heal": {}, "install": {}, "probe": {}, "pulls": {}, "backoff": {}, "activated": {}, "burst": {}, "alarm": {}, "probe_turn": {}}
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
    """Области под авто-лечением: autoheal = true и каталог есть.

    ⚠️ Конфиг перечитывается **на каждом проходе** — переключатель
    «Автомат / Вручную» в панели меняет поле `autoheal`, и лечащий
    подхватывает это без перезапуска.
    """
    return [a for a in all_areas() if a["autoheal"] and a["dir"].is_dir()]


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
    """Когда область последний раз двигалась (успешный ход)."""
    row = _rows(con, "select max(accepted_at_ms) from local_runtime_turn_ingress "
                     "where status in ('completed','accepted')")
    return int(row[0][0] or 0) if row else 0


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
            if progress and progress > fail:
                continue          # после отказа область возобновилась — отказ устарел
            cands.append((fail, "вендорский отказ в сессии %s (%s)" % (sid[:12], (msg or "").strip())))
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
            if progress and progress > ts:
                continue
            cands.append((ts, "фоновая задача #%s упала по очкам" % rid))
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
                # Свободных аккаунтов нет: ждём 30 минут (у Кармана каждые
                # полчаса возвращаются в оборот неизрасходованные аккаунты).
                _state["backoff"][area["slug"]] = {
                    "until": time.time() + POOL_EMPTY_BACKOFF,
                    "why": "пул Кармана пуст — ждём 30 мин (возвращаются неизрасходованные)"}
                save_cooldowns()
                return rc, "пул Кармана пуст, пауза %d мин" % (POOL_EMPTY_BACKOFF // 60)
            if rc == 3:
                # Карман отдал негодный токен — пробуем следующий: возможно, выпал
                # именно ошибочный, а следующий будет живым.
                log("%s: Карман отдал негодный токен — пробую следующий (попытка %s из %s)" % (
                    area["title"], attempt, MAX_PULL_TRIES))
                continue
            return rc, "аккаунт не получен (пул Кармана пуст или отказ)"
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
    hit = vendor_balance_failure(area["dir"], install, since)
    if hit:
        out["verdict"] = "ОТКАЗ ПО ОЧКАМ"
        out["failure"] = hit[0]
        out["reason"] = hit[1]
        out["tier"] = "T1"
        return out
    maybe = silent_failure(area["dir"], install, since)
    if maybe:
        out["verdict"] = "подозрение"
        out["failure"] = maybe[0]
        out["reason"] = maybe[1]
        out["tier"] = "T2"
        return out
    lost = login_lost(area["dir"])
    if lost:
        out["verdict"] = "ПОТЕРЯН ВХОД"
        out["reason"] = lost
        out["tier"] = "T3"
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
        with urllib.request.urlopen(req, timeout=20, context=switch.SSL_CTX) as resp:
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


def restart_area(slug: str) -> None:
    """Перезапуск области: стоп → старт (собственный каталог, свой AUMID, свой ярлык)."""
    ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File"]
    subprocess.run(ps + [str(BIN / "mcode-project-stop.ps1"), "-Project", slug],
                   capture_output=True, text=True, timeout=120)
    time.sleep(3)
    subprocess.run(ps + [str(BIN / "mcode-project-start.ps1"), "-Project", slug],
                   capture_output=True, text=True, timeout=180)


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
             "Оборванные субагенты (последний ход failed/aborted — работа не закончена):"]
    for kid in kids:
        lines.append("- task_id=%s  «%s»" % (kid["task_id"], kid["description"][:80]))
    lines += ["",
              "Разбуди их через task_append (инструмент рантайма) по task_id; "
              "тех, чью работу ты уже сделал, не буди. "
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
                log("%s: сессия занята ходом — срываю его перезапуском области, "
                    "чтобы «Продолжай» встал в начало (попытка %s)" % (
                        area["title"], attempt))
                restart_area(area["slug"])
                time.sleep(6)
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
        if age < PROBE_NO_TURN_TIMEOUT:
            return True      # ещё рано — подождём следующего прохода
        _state["probe_turn"].pop(slug, None)
        save_cooldowns()
        log("%s: «Продолжай» без перезапуска не доставился (нового хода нет %d с) — "
            "ОТКАТ: перезапускаю область и повторяю" % (area["title"], int(age)))
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
        log("%s: пауза до %s (%s)" % (
            area["title"], _fmt(int(float(bo["until"]) * 1000)), bo.get("why", "ожидание")))
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
    rc, why = pull_account(switch, area)
    if rc != 0:
        log("%s: %s — ждём, повторов не гоним" % (area["title"], why))
        return False
    log("%s: аккаунт получен (%s)" % (area["title"], why))
    _state["pulls"].setdefault(slug, []).append(time.time())
    _state["pulls"][slug] = [t for t in _state["pulls"][slug] if time.time() - t < 86400]
    # Счётчик «активационной лавины»: 5+ аккаунтов на проект за 3 минуты ⇒
    # аларм и остановка (см. проверку выше).
    _state.setdefault("burst", {})[slug] = burst + [time.time()]

    _state["last_heal"][slug] = time.time()
    if last and gap < 900:
        log("%s: прошлый аккаунт прожил всего %d мин — активность тяжёлая, "
            "пул Кармана горит быстро" % (area["title"], int(gap // 60)))
    # Помним, какое поколение токена поставили и когда: это точный якорь
    # «текущий аккаунт» (profile.json переписывает сам десктоп при входе).
    _state["install"][slug] = {"generation": store_generation(area["dir"]),
                                "at": int(time.time() * 1000)}
    save_cooldowns()

    # 1) активировать бонусные очки нового аккаунта — до перезапуска,
    #    чтобы агент начал работать уже с очками (claim идемпотентен по суткам).
    res = claim_bonus(switch, area["dir"])
    log("%s: бонусные очки — %s" % (area["title"], res))

    # 2) перезапуск области под новым аккаунтом
    # 2) Смена аккаунта БЕЗ перезапуска десктопа: токен и состояние уже в
    #    области (поколение выше, чем у приложения — десктоп подхватит его на
    #    следующем запросе). Перезапуск оставляем ЗАПАСНЫМ путём: если пробный
    #    ход упадёт, значит десктоп остался на старом токене.
    _state["activated"][slug] = int(time.time() * 1000)
    save_cooldowns()
    before = latest_turn(area["dir"])
    before_ms = before[2] if before else 0
    res = say_continue(area)
    log("%s: «Продолжай» без перезапуска — %s" % (area["title"], res))
    _state["probe_turn"][slug] = {"before_ms": before_ms, "at": time.time()}
    save_cooldowns()
    return True

    return True


# ────────────────────────────────────────────────────────── режимы

def show_status() -> int:
    areas = all_areas()
    print("МиниГен — авто-лечение: %s" % datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    print("%-13s %-9s %-8s %-13s %-13s %s" % (
        "область", "присмотр", "аккаунт", "установлен", "последний отказ", "вердикт"))
    for area in areas:
        watch = "автомат" if area["autoheal"] else "вручную"
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
