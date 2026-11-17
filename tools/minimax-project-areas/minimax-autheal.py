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
ERROR_WINDOW = 86400    # окно отказа по очкам (сутки): сломанный аккаунт сам не
                        # восстановится до ночного сброса, а главная защита от
                        # повторного лечения — время установки ТЕКУЩЕГО аккаунта
BALANCE_MARK = "insufficient balance"
BALANCE_CODE = 50110    # код рантайма на отказ провайдера «insufficient balance (1008)»
PROBE_TIMEOUT = 150     # секунд на живой проверку (T2)
MIN_TOKEN_LIFE_MIN = 30 # минимальный остаток жизни access-токена, который мы ставим
MAX_PULL_TRIES = 3      # сколько аккаунтов пробуем взять, пока не найдём живой токен
LOST_LOGIN_STATES = ("logout_pending", "none", "lease-expired", "logged_out", "signed_out")

_state = {"last_heal": {}, "install": {}, "probe": {}}
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
    except (OSError, ValueError, TypeError):
        _state["last_heal"] = {}
        _state["install"] = {}
        _state["probe"] = {}


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


def pull_account(switch, area: dict):
    """Взять аккаунт у Кармана и посадить его в область.

    **Страховка от протухшего токена:** 17.11 Карман отдал аккаунт, у которого
    access жил 9 минут — десктоп его отверг и стёр запись входа, область осталась
    незалогиненной. Поэтому токен с коротким остатком мы **не оставляем**:
    берём следующий (до трёх попыток).

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
            if rc == 3:
                # КАРМАН отдал негодный токен (протухший или не принят вендором):
                # не ставим его, берём следующий аккаунт.
                log("%s: Карман отдал негодный токен — беру следующий" % area["title"])
                continue
            return rc, "аккаунт не получен (пул Кармана пуст или отказ)"
        rec = (switch.read_store(area["dir"]).get("records") or {}).get(switch.store_key(area["dir"])) or {}
        exp = int(rec.get("expiresAtMs") or 0)
        life = (exp - time.time() * 1000) / 60000.0
        if life < MIN_TOKEN_LIFE_MIN:
            log("%s: токен протухает (%.0f мин) — не оставляем его, берём следующий" % (
                area["title"], life))
            continue
        # Токен должен ещё и приниматься вендором: если Карман отдал «не тот»
        # токен (или мёртвый), панель вендора ответит 401 — такой аккаунт тоже
        # не оставляем (17.11: id=25 протух через 9 минут и стёр вход десктопа).
        tok = rec.get("accessToken") or ""
        status, _payload = switch.panel(tok)
        if status != 200:
            log("%s: вендор не принимает выданный токен (HTTP %s) — берём следующий" % (
                area["title"], status))
            continue
        return 0, "аккаунт %s, токен жив ещё %.0f мин, вендор отвечает 200" % (
            rec.get("email"), life)
    return 4, "Карман %d раз подряд отдал негодный токен (протухший или не принят) — нужен живой аккаунт" % MAX_PULL_TRIES


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
    install = last_install_ts(area["dir"])
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


def _continue_once(area_dir: Path, folder: str, logfile: Path):
    """Одна попытка отправить «Продолжай» (фоном).

    Возвращает ("ok", описание) — ход пошёл; или ("busy", хвост лога) — сессия
    занята («Session already has an active Turn»); или ("dead", хвост лога) —
    умер по другой причине.
    """
    env = dict(os.environ)
    env["MINIMAX_DATA_DIR"] = str(area_dir)
    env["MAVIS_DATA_DIR"] = str(area_dir)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [str(MCODE), "exec", "--continue", "--cwd", folder, "Продолжай"]
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=logfile, stderr=logfile,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    except Exception as exc:  # noqa: BLE001
        return "dead", "не запустилось: %s" % exc
    time.sleep(3)
    if proc.poll() is None:
        return "ok", "отправлено (ход идёт в фоне, pid %s, лог %s)" % (
            proc.pid, getattr(logfile, "name", logfile))    # Процесс умер сразу — читаем, что он написал.
    try:
        tail = "\n".join(logfile.read_text(encoding="utf-8", errors="replace").splitlines()[-3:])
    except OSError:
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
        proc = subprocess.Popen([str(MCODE), "exec", "--cwd", folder, "Продолжай"],
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
    restart_area(slug)
    log("%s: область перезапущена с новым аккаунтом" % area["title"])

    # 3) «Продолжай» — чтобы работа над проектом возобновилась (в начало, не в очередь)
    res = say_continue(area)
    log("%s: «Продолжай» — %s" % (area["title"], res))
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
        for area in areas:
            try:
                diag = diagnose(area)
            except Exception as exc:  # noqa: BLE001
                log("%s: диагностика не удалась: %s" % (area["title"], exc))
                continue
            if diag["verdict"] == "нет области":
                continue
            if args.dry_run:
                log("%s: %s — %s" % (area["title"], diag["verdict"], diag["reason"]))
                continue
            try:
                heal(area, switch, force=args.force)
            except Exception as exc:  # noqa: BLE001
                log("%s: лечение сорвалось: %s" % (area["title"], exc))
        if args.once or args.dry_run:
            break
        time.sleep(max(30, args.interval))
    return 0


if __name__ == "__main__":
    sys.exit(main())
