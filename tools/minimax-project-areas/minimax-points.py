#!/usr/bin/env python3
"""Watchdog очков: расход токенов по КАЖДОМУ аккаунту + проба аккаунта.

Зачем этот модуль. Реактивное лечение (уже есть в `minimax-autheal.py`) ловит
отказ провайдера **после** того, как он случился: `error_code = 50110`
«insufficient balance (1008)». Ход при этом уже упал, и площадка работы теряется.
Этот модуль даёт **два опережающих** сигнала:

1. **Учёт расхода по аккаунту.** Локальная база рантайма области
   (`v2/sqlite/runtime-state.sqlite`, таблица `local_runtime_token_usage`) пишет
   по каждому запросу к модели: input/output/reasoning/cache_read. Из этого
   считается **биллинговый расход** = `input + output`.

   ⚠️ Почему именно `input + output`, а не `totalTokens` из `raw`: cache_read
   бывает в 30–240 раз больше биллинговых при той же самой ёмкости аккаунта
   (замер по 24 областям за 26.11: 50.3M input + 13.2M output против 4.59
   миллиарда cache_read). Если бы кэш тарифицировался по номиналу, аккаунты
   умирали бы за десятки тысяч токенов — а они живут миллионы. Значит кэш не
   тарифицируется, и «счёт очков» — это `input + output`.

   ⚠️ Считаем расход **области**, а не сессии: один аккаунт из пула КАРМАНА
   со временем стоит в нескольких областях (проверено: 101 аккаунт из 153
   побывал больше чем в одной области), и очки у него общие. Счёт по сессии
   дал бы перерасход.

2. **Проба аккаунта.** Прямой запрос к модели (`POST {baseURL}/messages`,
   `max_tokens=1`, промпт «ping») токеном **той же области**. Ответ вендора
   разбирается на три случая: жив / кончились очки / недействительный вход.
   Проба стоит ~10 токенов и не трогает сессии области.

⚠️ **Главное ограничение (проверено на живых данных).** Остаток очков вендор
**не отдаёт**: панель `signin/status` показывает дневной **грант** (400/1000),
а не остаток, — аккаунт, уже умерший от «insufficient balance», продолжает
показывать «1000 очков». Поэтому порог по номиналу — **приближение**, а не
гарантия: настоящий остаток аккаунта может быть нулевым уже при установке.

Отсюда и устройство: проба ловит «мёртвый на входе» аккаунт, а порог по
расходу ловит «вот-вот кончится». Страховкой остаётся реактивный путь
лечащего (50110).

Замеры, на которых стоят дефолты (журнал установок × базы 24 областей,
26.11.2026, биллинговые токены `input+output` до момента отказа 50110):

| номинал очков | умерли (n) | median | max | выжили: median | max |
|---|---|---|---|---|---|
| 1000 | 13 | 1 219 720 | 3 297 047 | 1 221 044 | 6 154 192 |
| 400  | 11 |   743 456 | 2 276 205 |   891 480 | 3 617 266 |

Видно две вещи: (1) медиана умерших почти совпадает с медианой выживших —
распределения перекрываются, порог по токенам **не гарантирует** выживание,
а лишь сдвигает момент; (2) 400 и 1000 очков умирают при одном расходе —
номинал очков ёмкость аккаунта **не задаёт**. Отсюда дефолты взяты
**консервативно**: половина медианы выживших.

Состояние — `%LOCALAPPDATA%\\mcode-web\\points-watchdog.json` (рядом с
состоянием лечащего): помнит, какой аккаунт и когда стоит в каждой области,
и накопленную статистику отказов.

Запуск:
    python minimax-points.py status            # расход и пороги по областям
    python minimax-points.py status --json
    python minimax-points.py probe p-minigen    # проба аккаунта области
    python minimax-points.py learn              # выученная статистика отказов
    python minimax-points.py set p-minigen 400000   # ручной порог области
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import minigen_time as mtime  # noqa: E402

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
STATE_FILE = LOCAL / "mcode-web" / "points-watchdog.json"

#: Порог по расходу (биллинговые токены) для каждого номинала очков.
#: Взят по замерам смертей (медиана умерших — 1.219M при 1000 очках и 743k
#: при 400): ниже медианы, но не ниже p25 — иначе аккаунт сжигается впустую.
#: Точный выбор — за владельцем: `minimax-points.py set <область> <порог>`.
DEFAULT_BUDGET = {1000: 800_000, 400: 600_000}
#: Номинал неизвестен (панель не ответила) — порог по умолчанию.
FALLBACK_BUDGET = 700_000
#: Остаток очков аккаунта — по данным сайта MiniMax. Это тот же эндпоинт,
#: который дёргает веб-консоль MiniMax (`GET /minimax-cloud/api/v1/credit/details`):
#: леджер дневных грантов с выданным / потраченным / остатком. Остаток =
#: сумма `remaining_amount` по непросроченным грантам.
#:
#: ⚠️ Очки **накапливаются**: каждый дневной грант (400 или 1000) живёт ~30 суток
#: (проверено: `expire_at_ms - granted_at_ms` ≈ 29.8 сут на всех грантах) и
#: остатки складываются. Поэтому «сегодняшняя клетка» из `signin/status` — это
#: НЕ остаток: у бывшего аккаунта p-orca остаток 4779.70 при дневном гранте 400.
#: Тратится остаток по очереди, с самых старых грантов.
BALANCE_URL = "https://agent.minimax.io/minimax-cloud/api/v1/credit/details"
#: Кэш остатка: панель отвечает небыстро, а лечащий ходит по областям каждые
#: 10 с. Двух минут кэша хватает: остаток меняется медленно.
BALANCE_TTL_SEC = 120
#: Остаток ниже этого — аккаунт меняем.
#:
#: ⚠️ Владелец предлагал 10 очков («когда очков станет меньше 10»). По замеру это
#: слишком туго: один очко ≈ 837 биллинговых токенов, то есть 10 очков — это
#: ~8 тыс. токенов запаса, а **один запрос** стоит медианой 1 178, а на хвосте
#: (p99 по 133 657 запросам) — 206 138 токенов ≈ 246 очков, максимум — 845 тыс.
#: ≈ 1 009 очков. При пороге 10 аккаунт умирал бы прямо посреди хода.
#: Поэтому дефолт — 300 очков (≈250 тыс. токенов запаса, перекрывает p99).
POINTS_THRESHOLD = 300.0
#: Порог не может быть нулевым и не может превышать дефолт класса.
MIN_BUDGET = 50_000
#: Час сброса суточных очков у вендора (MSK). Очки начисляются на сутки,
#: а не «от установки аккаунта»: аккаунт, переживший сброс, начинает сутки
#: заново. Поэтому окно расхода начинается с последнего сброса.
RESET_HOUR = 3

#: Кэш значений панели: номинал очков меняется раз в сутки, ходить за ним
#: на каждый проход лечащего нельзя (панель отвечает небыстро).
POINTS_TTL_SEC = 3600
#: Кэш расхода: пересчитывать агрегат по базе на каждом проходе незачем.
BURN_TTL_SEC = 30
#: Область, к модели которой не обращались дольше этого, считается
#: простаивающей: её аккаунт подменять незачем (очки не растут).
IDLE_GAP_SEC = 600

#: Отказы провайдера, которые означают «очки кончились». Набор взят из рантайма
#: MiniMax Code (функция `isTerminalRateLimitError`): он по такому же списку
#: решает, что лимит исчерпан и повторять запрос бессмысленно.
QUOTA_PATTERNS = re.compile(
    r"insufficient[_ ]balance|FreeUsageLimitError|GoUsageLimitError|"
    r"Monthly usage limit reached|available balance|insufficient_quota|"
    r"out of budget|quota exceeded|usage limit|余额不足|额度不足",
    re.IGNORECASE)

_core_mod = None
_switch_mod = None


# ─────────────────────────────────────────────────────── общие модули

def _core():
    """Ядро «МиниГена» — мягкая зависимость (как в лечащем)."""
    global _core_mod
    if _core_mod is None:
        try:
            import minigen_core as core_mod
            _core_mod = core_mod
        except Exception:  # noqa: BLE001
            _core_mod = False
    return _core_mod or None


def _switch():
    """`minimax-switch.py` — единственная реализация механики стора входа."""
    global _switch_mod
    if _switch_mod is None:
        candidates = [HERE / "minimax-switch.py"]
        core = _core()
        if core is not None:
            candidates.append(Path(core.BIN) / "minimax-switch.py")
        for path in candidates:
            if path.is_file():
                try:
                    spec = importlib.util.spec_from_file_location("minimax_switch", str(path))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    _switch_mod = mod
                except Exception:  # noqa: BLE001
                    _switch_mod = False
                break
        else:
            _switch_mod = False
    return _switch_mod or None


def log(msg: str) -> None:
    print("[%s] %s" % (mtime.fmt_now(), msg), flush=True)


# ─────────────────────────────────────────────────────────── состояние

def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("areas", {})
    data.setdefault("deaths", [])
    return data


def save_state(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


# ──────────────────────────────────────────────────── база рантайма

def runtime_db(area_dir: Path) -> Path:
    core = _core()
    if core is not None:
        try:
            return Path(core.area_db(area_dir))
        except Exception:  # noqa: BLE001
            pass
    return area_dir / "v2" / "sqlite" / "runtime-state.sqlite"


def burn_since(area_dir: Path, since_ms: int) -> dict:
    """Биллинговый расход области с момента `since_ms`.

    Возвращает {"billable": int, "input": int, "output": int, "cache": int,
    "requests": int, "last_ms": int}. Пусто — если базы нет или она недоступна.
    `last_ms` — время последнего запроса к модели: по нему видно, работает ли
    область сейчас (простаивающую область подменять незачем — очки не растут).
    """
    db = runtime_db(area_dir)
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True, timeout=10)
    except sqlite3.Error:
        return {}
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(cache_read_tokens),0), COUNT(*), COALESCE(MAX(ts),0) "
            "FROM local_runtime_token_usage WHERE ts >= ?", (int(since_ms),)).fetchone()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    if not row:
        return {}
    inp, out, cache, reqs, last_ms = row
    return {"billable": int(inp) + int(out), "input": int(inp), "output": int(out),
            "cache": int(cache), "requests": int(reqs), "last_ms": int(last_ms or 0)}


# ──────────────────────────────────────────────────────── аккаунт области

def store_record(area_dir: Path) -> dict:
    """Живая запись входа области из стора (без вывода секретов)."""
    switch = _switch()
    if switch is None:
        return {}
    try:
        store = switch.read_store(area_dir)
        rec = (store.get("records") or {}).get(switch.store_key(area_dir)) or {}
    except Exception:  # noqa: BLE001
        return {}
    return rec if isinstance(rec, dict) else {}


def installed_at(area_dir: Path, slug: str, email: str, generation) -> int:
    """Когда текущий аккаунт встал в область (мс).

    Порядок источников:
      1. наше состояние — если email/поколение совпали с записанными, время
         установки известно точно;
      2. время изменения стора входа — стор переписывается при каждой
         установке, это ближайшая доступная метка времени;
      3. `profile.json` — его переписывает десктоп при входе, поэтому он
         последний в списке.
    """
    state = load_state()
    rec = (state.get("areas") or {}).get(slug) or {}
    if rec and rec.get("email") == email and rec.get("generation") == generation:
        return int(rec.get("installed_at") or 0)
    store_path = area_dir / "auth" / "prod" / "en" / "mcode-public" / "auth.json"
    try:
        return int(store_path.stat().st_mtime * 1000)
    except OSError:
        pass
    try:
        meta = json.loads((area_dir / "profile.json").read_text(encoding="utf-8"))
        ts = str(meta.get("updated_at") or "")
        if ts:
            return int(time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S")) * 1000)
    except (OSError, ValueError):
        pass
    return 0


def day_start_ms(now: float = None) -> int:
    """Начало текущих «очковых суток» (мс).

    У вендора суточные очки обнуляются в 03:00 (MSK). Значит расход,
    который аккаунт набрал вчера, сегодня очками не оплачивается — и в
    порог он входить не должен.
    """
    now = now if now is not None else time.time()
    local = time.localtime(now)
    start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, RESET_HOUR, 0, 0,
                          0, 0, -1))
    if now < start:
        # до сегодняшнего сброса ещё не дошли — сутки начались вчера в 03:00
        prev = time.localtime(now - 24 * 3600)
        start = time.mktime((prev.tm_year, prev.tm_mon, prev.tm_mday, RESET_HOUR, 0, 0,
                             0, 0, -1))
    return int(start * 1000)


def account_installed_at(area_dir: Path, slug: str = "") -> int:
    """Когда текущий аккаунт встал в область (мс). 0 — неизвестно."""
    rec = store_record(area_dir)
    email = str(rec.get("email") or "")
    if not email:
        return 0
    return installed_at(area_dir, slug or area_dir.name, email, rec.get("generation"))


def remember_account(slug: str, email: str, generation, installed_ms: int) -> None:
    """Запомнить, какой аккаунт и когда стоит в области."""
    state = load_state()
    areas = state.setdefault("areas", {})
    rec = areas.get(slug) or {}
    if rec.get("email") == email and rec.get("generation") == generation:
        return
    areas[slug] = {"email": email, "generation": generation,
                   "installed_at": int(installed_ms), "seen_at": int(time.time() * 1000)}
    save_state(state)


def points_today(area_dir: Path, email: str, force: bool = False, network: bool = True) -> int:
    """Номинал сегодняшних очков аккаунта (панель вендора).

    ⚠️ Это **дневной грант**, а не остаток: аккаунт, уже израсходовавший
    очки, показывает те же 400/1000. Поэтому значение годится только для
    выбора порога, а не для решения «осталось ли что-то».

    `network=False` — только кэш, без похода в панель. Так режим без сети:
    в горячем пути лечащего (раз в 10 с на область) ходить в панель нельзя —
    панель отвечает небыстро, и один её запрос встанет в очередь всем.
    """
    state = load_state()
    cache = (state.get("areas") or {}).get("points")
    cache = cache if isinstance(cache, dict) else {}
    cached = cache.get(email)
    now = time.time()
    if (not force and isinstance(cached, dict)
            and cached.get("email") == email
            and now - float(cached.get("at") or 0) < POINTS_TTL_SEC):
        return int(cached.get("points") or 0)
    if not network:
        return int(cached.get("points") or 0) if isinstance(cached, dict) else 0
    switch = _switch()
    if switch is None:
        return int(cached.get("points") or 0) if isinstance(cached, dict) else 0
    token = (store_record(area_dir) or {}).get("accessToken") or ""
    if not token:
        return int(cached.get("points") or 0) if isinstance(cached, dict) else 0
    try:
        status, payload = switch.panel(token)
    except Exception:  # noqa: BLE001
        return int(cached.get("points") or 0) if isinstance(cached, dict) else 0
    if status != 200:
        return int(cached.get("points") or 0) if isinstance(cached, dict) else 0
    cell = switch.today_cell(payload) if hasattr(switch, "today_cell") else {}
    points = int(cell.get("points") or 0)
    remember_points(email, points)
    return points


def remember_points(email: str, points: int) -> None:
    """Запомнить номинал очков аккаунта (панель спросила — мы знаем)."""
    if not email:
        return
    state = load_state()
    areas = state.setdefault("areas", {})
    cache = areas.get("points")
    cache = cache if isinstance(cache, dict) else {}
    cache[email] = {"points": int(points or 0), "at": time.time(), "email": email}
    areas["points"] = cache
    save_state(state)


def remaining_points(area_dir: Path, email: str, force: bool = False) -> float:
    """Остаток очков аккаунта по данным сайта MiniMax.

    Возвращает сумму остатков по грантам. `None` — остаток узнать не удалось
    (токен протух, сеть или сайт недоступны): тогда вердикт опирается на
    расход токенов.

    ⚠️ Кэш на `BALANCE_TTL_SEC`: лечащий ходит по областям каждые 10 с, а
    панель отвечает небыстро.
    """
    if not email:
        return None
    state = load_state()
    cache = (state.get("areas") or {}).get("balance")
    cache = cache if isinstance(cache, dict) else {}
    cached = cache.get(email)
    now = time.time()
    if (not force and isinstance(cached, dict)
            and cached.get("email") == email
            and now - float(cached.get("at") or 0) < BALANCE_TTL_SEC):
        value = cached.get("remaining")
        return float(value) if value is not None else None
    token = (store_record(area_dir) or {}).get("accessToken") or ""
    if not token:
        return None
    req = urllib.request.Request(
        BALANCE_URL, method="GET",
        headers={"Accept": "application/json", "Authorization": "Bearer " + token})
    try:
        with _opener().open(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    details = payload.get("details")
    if not isinstance(details, list):
        return None
    total = 0.0
    for row in details:
        if not isinstance(row, dict):
            continue
        try:
            total += float(row.get("remaining_amount") or 0)
        except (TypeError, ValueError):
            continue
    areas = state.setdefault("areas", {})
    cache = areas.get("balance")
    cache = cache if isinstance(cache, dict) else {}
    cache[email] = {"remaining": total, "at": now, "email": email,
                    "grants": len(details)}
    areas["balance"] = cache
    save_state(state)
    return total


# ─────────────────────────────────────────────────────────────── порог

def budget_for(points: int, deaths: list = None) -> int:
    """Порог расхода для аккаунта с номиналом `points` очков.

    База — дефолт класса (половина медианы выживших). Если накоплена своя
    статистика отказов по этому классу, порог **понижается** до p25 отказов:
    значит по факту аккаунты этого класса умирают раньше дефолта.

    Отказы с 1–2 запросами в статистику не идут: это «мёртвый на входе»
    аккаунт (очки кончились до нас), его ловит проба, а не порог.
    """
    base = DEFAULT_BUDGET.get(int(points or 0), FALLBACK_BUDGET)
    rows = [d for d in (deaths or [])
            if int(d.get("points") or 0) == int(points or 0)
            and int(d.get("requests") or 0) >= 20
            and int(d.get("burn") or 0) > 0]
    if rows:
        burns = sorted(int(d["burn"]) for d in rows)
        p25 = burns[max(0, len(burns) // 4)]
        base = min(base, p25)
    return max(MIN_BUDGET, int(base))


def learn_death(email: str, points: int, burn: int, requests: int) -> None:
    """Запомнить расход аккаунта в момент отказа по очкам."""
    if not email:
        return
    state = load_state()
    deaths = state.setdefault("deaths", [])
    deaths.append({"email": email, "points": int(points or 0), "burn": int(burn or 0),
                   "requests": int(requests or 0), "at": int(time.time() * 1000)})
    # История не растёт вечно: держим последние 200 отказов.
    if len(deaths) > 200:
        del deaths[:-200]
    save_state(state)


def learn_current(area_dir: Path, slug: str = "") -> dict:
    """Запомнить расход ТЕКУЩЕГО аккаунта как «отказ по очкам».

    Вызывается, когда лечащий увидел отказ 50110: порог должен учиться на
    своих данных, а не на чужой медиане.

    ⚠️ Повторно за тот же отказ не пишем: диагностика ходит каждые 10 с, и без
    этого накопление ушло бы в тысячу одинаковых записей.
    """
    rec = store_record(area_dir)
    email = str(rec.get("email") or "")
    if not email:
        return {}
    slug = slug or area_dir.name
    since = installed_at(area_dir, slug, email, rec.get("generation"))
    since = max(since, day_start_ms())
    burn = burn_since(area_dir, since)
    points = points_today(area_dir, email, network=False)
    state = load_state()
    for d in (state.get("deaths") or []):
        if str(d.get("email")) == email and int(d.get("requests") or 0) == int(
                burn.get("requests") or 0):
            return {}          # этот отказ уже записан
    learn_death(email, points, int(burn.get("billable") or 0),
                int(burn.get("requests") or 0))
    return {"email": email, "points": points,
            "burn": int(burn.get("billable") or 0),
            "requests": int(burn.get("requests") or 0)}


# ──────────────────────────────────────────────────────────────── проба

def provider_of(area_dir: Path) -> tuple:
    """(baseURL, модель) области по её родному конфигу."""
    try:
        import yaml
        cfg = yaml.safe_load((area_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return "", ""
    providers = cfg.get("provider") or {}
    if not isinstance(providers, dict) or not providers:
        return "", ""
    pid = "minimax" if "minimax" in providers else list(providers)[0]
    pconf = providers.get(pid) or {}
    options = pconf.get("options") or {}
    base = str(options.get("baseURL") or "").rstrip("/")
    models = list((pconf.get("models") or {}).keys())
    ref = str(cfg.get("defaultModel") or "")
    model = ref.split("/", 1)[1] if "/" in ref else ""
    if model not in models:
        model = models[0] if models else ""
    return base, model


def _opener():
    # Прямое соединение, без системного прокси: вендор доступен напрямую
    # (та же мысль, что в minimax-switch.py — иначе ложные «очки кончились»).
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def classify_response(code: int, body: str) -> str:
    """Как трактовать ответ вендора: ok / exhausted / invalid / unknown.

    ⚠️ Сетевой/неизвестный ответ — это `unknown`, а НЕ «кончились очки»:
    ложный отказ по очкам лечится сменой аккаунта, а это жжёт пул (урок
    21.11: «fetch failed» = мёртвый порт прокси, аккаунт при этом цел).
    """
    if code == 200:
        return "ok"
    if code in (401, 403):
        return "invalid"
    if code == 402:
        return "exhausted"
    if QUOTA_PATTERNS.search(body or ""):
        return "exhausted"
    if code == 429:
        # 429 без слов о балансе — rate limit, а не кончившиеся очки.
        return "unknown"
    return "unknown"


def probe(area_dir: Path, timeout: int = 30) -> tuple:
    """Проба аккаунта области: один дешёвый запрос к модели.

    Возвращает (статус, пояснение). Статусы:
      * ``ok``        — вендор ответил, аккаунт жив;
      * ``exhausted`` — вендор отказал по лимиту (очки кончились);
      * ``invalid``   — вендор не принимает вход (токен мёртв/отозван);
      * ``unknown``   — сеть/вендор недоступны, отказа по очкам **нет**.
    """
    base, model = provider_of(area_dir)
    rec = store_record(area_dir)
    token = rec.get("accessToken") or ""
    email = str(rec.get("email") or "")
    # Сначала остаток очков: если он виден и равен нулю, аккаунт мёртв —
    # ход к модели не нужен (дешевле и точнее, чем ловить отказ провайдера).
    left = remaining_points(area_dir, email, force=True) if email else None
    if left is not None and left <= 0:
        return "exhausted", "на сайте остаток %s очков" % left
    if not base or not model:
        return "unknown", "у области нет конфига провайдера"
    if not token:
        return "invalid", "в области нет access-токена"
    body = json.dumps({"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
    req = urllib.request.Request(
        base + "/messages", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "anthropic-version": "2023-06-01",
                 "Authorization": "Bearer " + token})
    try:
        with _opener().open(req, timeout=timeout) as resp:
            resp.read()
            return "ok", "вендор ответил %s" % resp.status
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            detail = ""
        status = classify_response(exc.code, detail)
        if status == "exhausted":
            return status, "вендор отказал по лимиту (HTTP %s): %s" % (
                exc.code, detail[:200] or "без тела")
        if status == "invalid":
            return status, "вендор не принимает вход (HTTP %s)" % exc.code
        return "unknown", "вендор вернул HTTP %s: %s" % (exc.code, detail[:200] or "без тела")
    except Exception as exc:  # noqa: BLE001
        return "unknown", "вендор недоступен: %s: %s" % (type(exc).__name__, str(exc)[:120])


# ─────────────────────────────────────────────────────────── сводка

def verdict_for(remaining, threshold: float, billable: int, budget: int,
                idle: bool, points: int = 0) -> tuple:
    """Вердикт по аккаунту: (вердикт, пояснение).

    Порядок:
    1. **Остаток очков известен** — он с сайта MiniMax и он точен:
       ниже порога ⇒ аккаунт меняем, выше ⇒ живой (расход тут ни при чём:
       у аккаунта может быть накоплено до 4 800 очков);
    2. остаток узнать не удалось ⇒ запасная эвристика «расход токенов»;
    3. область простаивает ⇒ не трогаем (очки не растут).
    """
    if remaining is not None and remaining <= threshold:
        return "почти без очков", "осталось %s очков (порог %s)" % (remaining, threshold)
    if remaining is not None:
        return "жив", "осталось %s очков, расход %s" % (remaining, billable)
    if idle:
        return "простаивает", "последний запрос давно — расход не растёт"
    if billable >= budget:
        return "почти без очков", (
            "расход %s при пороге %s (номинал %s очков, остаток узнать не удалось)" % (
                billable, budget, points or "?"))
    return "жив", "расход %s из %s (номинал %s очков)" % (billable, budget, points or "?")


def usage(area_dir: Path, slug: str = "", force: bool = False, network: bool = True) -> dict:
    """Сводка по аккаунту области: кто стоит, с какого момента, расход, порог."""
    rec = store_record(area_dir)
    email = str(rec.get("email") or "")
    generation = rec.get("generation")
    out = {"slug": slug or area_dir.name, "email": email, "generation": generation,
           "installed_at": 0, "billable": 0, "requests": 0, "cache": 0, "last_ms": 0,
           "points": 0, "budget": 0, "remaining": None, "threshold": POINTS_THRESHOLD,
           "verdict": "нет данных", "why": ""}
    if not email:
        out["why"] = "в области нет записи входа"
        return out
    since = installed_at(area_dir, out["slug"], email, generation)
    out["installed_at"] = since
    remember_account(out["slug"], email, generation, since)
    # Окно расхода — от установки аккаунта, но не раньше суточного сброса
    # очков: вчерашний расход сегодняшними очками не оплачивается.
    since = max(since, day_start_ms())
    burn = burn_since(area_dir, since)
    out["billable"] = int(burn.get("billable") or 0)
    out["requests"] = int(burn.get("requests") or 0)
    out["cache"] = int(burn.get("cache") or 0)
    out["last_ms"] = int(burn.get("last_ms") or 0)
    points = points_today(area_dir, email, force=force, network=network)
    out["points"] = points
    state = load_state()
    manual = int(((state.get("areas") or {}).get(out["slug"]) or {}).get("budget") or 0)
    budget = manual or budget_for(points, state.get("deaths") or [])
    out["budget"] = budget
    # Порог по остатку очков — свой на область (владелец может менять).
    threshold = float(((state.get("areas") or {}).get(out["slug"]) or {})
                      .get("pointsThreshold") or POINTS_THRESHOLD)
    out["threshold"] = threshold
    # ⚠️ Простаивающую область подменять незачем: очки не растут, а аккаунт
    # сгорит зря. Меняем только область, которая сейчас работает.
    last_ms = out["last_ms"]
    idle = bool(last_ms and (time.time() * 1000 - last_ms) > IDLE_GAP_SEC * 1000)
    if not last_ms:
        idle = True
    # Остаток очков — по данным сайта MiniMax. Это точнее расхода: остаток
    # видим прямо, а расход — эвристика (номинал очков ёмкость не задаёт).
    remaining = remaining_points(area_dir, email, force=force)
    out["remaining"] = remaining
    out["verdict"], out["why"] = verdict_for(
        remaining, threshold, out["billable"], budget, idle, points)
    return out


def areas_list() -> list:
    """Области из ядра (единый источник правды — конфиг проектов)."""
    core = _core()
    if core is None:
        return []
    try:
        return core.areas() or []
    except Exception:  # noqa: BLE001
        return []


def status(only: str = "", force: bool = False, network: bool = True) -> list:
    """Сводка по всем областям (или по одной)."""
    out = []
    for area in areas_list():
        slug = area.get("slug") or ""
        if only and slug not in (only, "p-" + only):
            continue
        out.append(usage(Path(area.get("dir") or ""), slug, force=force, network=network))
    return out


# ──────────────────────────────────────────────────────────────── CLI

def cmd_status(args) -> int:
    rows = status(args.area, force=bool(getattr(args, "force", False)))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("областей нет (ядро «МиниГена» недоступно?)")
        return 1
    print("%-14s %-24s %-11s %-11s %-9s %-8s %s" % (
        "область", "аккаунт", "остаток", "расход", "порог", "запросов", "вердикт"))
    for r in rows:
        left = r.get("remaining")
        print("%-14s %-24s %-11s %-11s %-9s %-8s %s" % (
            r["slug"], (r["email"] or "?")[:24],
            ("%.2f" % left) if left is not None else "?",
            r["billable"], r["budget"], r["requests"], r["verdict"]))
    return 0


def cmd_balance(args) -> int:
    """Остаток очков по областям — прямо с сайта MiniMax.

    Это тот же эндпоинт, что и у веб-консоли MiniMax:
    `GET /minimax-cloud/api/v1/credit/details` — леджер дневных грантов.
    """
    rows = status(args.area, force=True)
    if not rows:
        print("областей нет (ядро «МиниГена» недоступно?)")
        return 1
    print("%-14s %-24s %-11s %s" % ("область", "аккаунт", "остаток", "вердикт"))
    for r in rows:
        left = r.get("remaining")
        print("%-14s %-24s %-11s %s" % (
            r["slug"], (r["email"] or "?")[:24],
            ("%.2f" % left) if left is not None else "не узнать", r["verdict"]))
    return 0


def cmd_refresh(args) -> int:
    """Обновить номинал очков по панели (кэш) — для всех областей или одной.

    Номинал нужен лечащему, чтобы выбрать порог расхода, а ходить в панель
    в его горячем пути нельзя. Поэтому панель спрашивает **здесь** (раз в час
    кэш живёт сам), а лечащий берёт значение из кэша.
    """
    rows = status(args.area, force=True, network=True)
    if not rows:
        print("областей нет (ядро «МиниГена» недоступно?)")
        return 1
    for r in rows:
        print("%-14s %-24s очки=%-6s расход=%-10s порог=%-9s %s" % (
            r["slug"], (r["email"] or "?")[:24], r["points"] or "?", r["billable"],
            r["budget"], r["verdict"]))
    return 0


def cmd_probe(args) -> int:
    area_dir = None
    for area in areas_list():
        if area.get("slug") in (args.area, args.area.replace("p-", "")):
            area_dir = Path(area.get("dir") or "")
            break
    if area_dir is None:
        print("область %s не найдена" % args.area)
        return 1
    status_, why = probe(area_dir)
    print("%s: %s — %s" % (args.area, status_, why))
    return 0 if status_ == "ok" else 1


def cmd_learn(args) -> int:
    state = load_state()
    deaths = state.get("deaths") or []
    if args.json:
        print(json.dumps(deaths, ensure_ascii=False, indent=2))
        return 0
    if not deaths:
        print("статистика отказов пуста")
        return 0
    print("%-11s %-6s %-11s %-8s %s" % ("аккаунт", "очки", "расход", "запросов", "когда"))
    for d in deaths[-20:]:
        print("%-11s %-6s %-11s %-8s %s" % (
            str(d.get("email"))[:11], d.get("points"), d.get("burn"),
            d.get("requests"), mtime.fmt_now()))
    for points in sorted({int(d.get("points") or 0) for d in deaths}):
        rows = [d for d in deaths if int(d.get("points") or 0) == points
                and int(d.get("requests") or 0) >= 20]
        burns = sorted(int(d["burn"]) for d in rows)
        if burns:
            print("номинал %s: отказов %d, p25=%s, median=%s, порог=%s" % (
                points, len(burns), burns[max(0, len(burns) // 4)],
                burns[len(burns) // 2], budget_for(points, deaths)))
    return 0


def cmd_set(args) -> int:
    """Порог остатка очков для области (в очках)."""
    state = load_state()
    areas = state.setdefault("areas", {})
    rec = areas.get(args.area) or {}
    rec["pointsThreshold"] = float(args.value)
    areas[args.area] = rec
    save_state(state)
    print("порог области %s = %s очков" % (args.area, args.value))
    return 0


def cmd_set_budget(args) -> int:
    """Порог расхода для области (в биллинговых токенах) — запасной, если
    остаток очков узнать не удалось."""
    state = load_state()
    areas = state.setdefault("areas", {})
    rec = areas.get(args.area) or {}
    rec["budget"] = int(args.value)
    areas[args.area] = rec
    save_state(state)
    print("порог расхода области %s = %s токенов" % (args.area, args.value))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watchdog очков: расход токенов по аккаунтам и проба аккаунта")
    sub = parser.add_subparsers(dest="cmd")

    p_status = sub.add_parser("status", help="сводка по областям")
    p_status.add_argument("area", nargs="?", default="")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--force", action="store_true",
                          help="обновить номинал очков с панели, не ждать кэш")
    p_status.set_defaults(func=cmd_status)

    p_probe = sub.add_parser("probe", help="проба аккаунта области")
    p_probe.add_argument("area")
    p_probe.set_defaults(func=cmd_probe)

    p_refresh = sub.add_parser("refresh", help="обновить номинал очков по панели (кэш)")
    p_refresh.add_argument("area", nargs="?", default="")
    p_refresh.set_defaults(func=cmd_refresh)

    p_balance = sub.add_parser("balance", help="остаток очков по сайту MiniMax")
    p_balance.add_argument("area", nargs="?", default="")
    p_balance.set_defaults(func=cmd_balance)

    p_learn = sub.add_parser("learn", help="выученная статистика отказов")
    p_learn.add_argument("--json", action="store_true")
    p_learn.set_defaults(func=cmd_learn)

    p_set = sub.add_parser("set", help="порог остатка очков для области (в очках)")
    p_set.add_argument("area")
    p_set.add_argument("value", type=float)
    p_set.set_defaults(func=cmd_set)

    p_setb = sub.add_parser("set-budget",
                             help="порог расхода для области (в токенах, запасной)")
    p_setb.add_argument("area")
    p_setb.add_argument("value", type=int)
    p_setb.set_defaults(func=cmd_set_budget)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
