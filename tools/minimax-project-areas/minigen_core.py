"""minigen-core — общие куски «МиниГена» (панель + лечащий + сторожа).

Зачем отдельный файл. В «МиниГене» теперь три демона: лечащий аккаунты
(`minimax-autheal.py`), сетевой сторож (`minimax-netwatch.py`) и сторож
сессий (`minimax-sessionwatch.py`). Общее у них — пути, журнал, файл
состояния, чтение конфига областей, разбор базы рантайма и запуск `mcode`.
Здесь оно живёт один раз; каждый демон подключает модуль и не дублирует
механику (тот же принцип, что с `minimax-switch.py` — «единственная
реализация»).

Файлы, которые пишутся сюда (живут на машине, копия долговременная — в
`biblio/tools/minimax-project-areas/`, D-048):

| Что | Где |
|---|---|
| этот модуль | `C:\\Users\\valstan\\bin\\minigen-core.py` |
| сетевой сторож | `C:\\Users\\valstan\\bin\\minimax-netwatch.py` |
| сторож сессий | `C:\\Users\\valstan\\bin\\minimax-sessionwatch.py` |
| журнал (общий) | `%LOCALAPPDATA%\\mcode-web\\journal.log` |
| состояние сети | `%LOCALAPPDATA%\\mcode-web\\netwatch-state.json` |
| состояние сессий | `%LOCALAPPDATA%\\mcode-web\\sessionwatch-state.json` |
| конфиг путей/порогов | `C:\\Users\\valstan\\bin\\minigen-net.json` |

Правила, которые этот модуль соблюдает:
- **ничего не чинит «на всякий случай»**: каждый шаг восстановления сначала
  проверяется пробой и только потом считается успешным;
- **журнал — общий с панелью**: панель читает его с диска, поэтому все
  сообщения сюда пишем в том же формате, что и лечащий;
- **состояние — на диске, не в памяти**: перезапуск демона не должен
  обнулить «уже сделано» (иначе ночной цикл будет повторять действия).
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Каталог ядра — в путь импорта: ядро грузят и как скрипт, и по пути
# (importlib в тестах и в мосте), а `minigen_time` лежит рядом.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import minigen_time as mtime

# Время программы — везде Москва, Россия (UTC+3) (заказ владельца 24.11).
# Одна реализация на всех: ядро, демоны и мост берут её отсюда.
MSK = mtime.MSK
TZ_NAME = mtime.TZ_NAME
TZ_LABEL = mtime.TZ_LABEL
now = mtime.now
now_ms = mtime.now_ms

BIN = Path(r"C:\Users\valstan\bin")
AREAS_ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT", r"C:\Users\valstan\.minimax-areas"))
CONFIG = BIN / "mcode-project-areas.json"
NET_CONFIG = BIN / "minigen-net.json"
MCODE = Path(os.environ.get("APPDATA", "")) / "npm" / "mcode.cmd"
LOCAL = Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web"
JOURNAL = LOCAL / "journal.log"
NET_STATE = LOCAL / "netwatch-state.json"
SESSION_STATE = LOCAL / "sessionwatch-state.json"
# «Ящик отчётов» (заказ владельца 24.11): сторожи и инструменты отчитываются
# сюда, а встроенная нейросеть «Управляющий» читает эти отчёты.
REPORTS = LOCAL / "reports.jsonl"
REPORTS_MAX_BYTES = 512 * 1024      # больше — подрезаем хвост
REPORTS_KEEP_LINES = 800
#: Состояние автомата аккаунтов (лечащего). Сторож сессий читает его,
#: чтобы узнать, что аккаунт уже обновлён (промт КАРМАНА #174: аккаунты и
#: сессии — разные автоматы; «Продолжай» после смены аккаунта отдаёт
#: сторож сессий, а не лечащий).
AUTHEAL_STATE = LOCAL / "autheal-state.json"

# ────────────────────────────────────────────────────────── журнал и состояние

def log(msg: str) -> None:
    """Строка в общий журнал «МиниГена» (его же читает панель).

    Заодно строка попадает в «ящик отчётов» — чтобы нейросеть видела работу
    сторожей, не выспрашивая журнал по частям.
    """
    line = "[%s] %s" % (mtime.fmt_now(), msg)
    print(line, flush=True)
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    report("info", "log", msg)


def report(level: str, kind: str, text: str, area: str = "") -> None:
    """Отчёт о работе программы — в «ящик отчётов».

    Отчёт — это **не** журнал: он короткий, с уровнем (info/warn/error) и
    видом (log/tool/network/heal/session). Их читает встроенная нейросеть
    (инструмент `reports.recent` и автоподача в чат).

    Никогда не бросает: отчёт — дополнительный канал, его поломка не должна
    ломать саму работу.
    """
    record = {"ts": now_ms(), "level": (level or "info")[:16],
              "kind": (kind or "log")[:24], "area": (area or "")[:64],
              "text": (text or "")[:900]}
    try:
        REPORTS.parent.mkdir(parents=True, exist_ok=True)
        with REPORTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return
    _trim_reports()


def reports(limit: int = 50, level: str = "", since_ms: int = 0) -> list:
    """Последние отчёты, **новые — первыми** (как `tail`). Только чтение."""
    try:
        if not REPORTS.is_file():
            return []
        lines = REPORTS.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if level and str(rec.get("level") or "") != level:
            continue
        if since_ms and int(rec.get("ts") or 0) <= int(since_ms):
            continue
        out.append(rec)
        if len(out) >= max(int(limit or 0), 1):
            break
    return out


def _trim_reports() -> None:
    """Не дать «ящику отчётов» расти бесконечно.

    Дешёвая проверка — по размеру файла (чтение целиком на каждой записи
    было бы лишней работой на каждый вызов `log`).
    """
    try:
        if not REPORTS.is_file():
            return
        if REPORTS.stat().st_size < REPORTS_MAX_BYTES:
            return
        lines = REPORTS.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= REPORTS_KEEP_LINES:
            return
        keep = lines[-REPORTS_KEEP_LINES:]
        tmp = REPORTS.with_suffix(".tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(REPORTS))
    except OSError:
        pass


def read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def write_state(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ────────────────────────────────────────────────────────── конфиги

def net_config() -> dict:
    """Конфиг путей и порогов сети. Нет файла ⇒ дефолты (работать должны)."""
    data = read_state(NET_CONFIG)
    if data:
        return data
    return {
        "paths": [
            # ⚠️ `expect_country` для Happ — пусто: страна выхода **не
            # обязательна** (решение владельца 23.11). Happ отдаёт разные
            # серверы (NL/DE/AT/PL/US/CA…), и ожидание одной страны превращало
            # нормальную работу в ругань «страна выхода US вместо ожидаемой
            # NL» на каждом проходе. Пустое значение означает «страна не
            # важна»: сторож тогда проверяет **живость пути** (вендор
            # отвечает ⇒ путь живой) и не запускает каскад. Страна в отчёте
            # остаётся — как информация, не как ожидание.
            {"name": "happ", "proxy": "http://127.0.0.1:10808",
             "role": "основной", "expect_country": ""},
            {"name": "reserve", "proxy": "http://127.0.0.1:11081",
             "role": "резерв", "expect_country": "RU"},
        ],
        "probe_url": "https://agent.minimax.io/minimax-cloud/api/v1/signin/status",
        "geo_url": "https://ipinfo.io/json",
        "stable_probes": 3,
        "stable_gap_sec": 8,
        "interval_sec": 180,
    }


def areas() -> list:
    """Области из конфига проектов: slug, каталог данных, рабочая папка."""
    data = read_state(CONFIG)
    out = []
    for p in (data.get("projects") or []):
        slug = p.get("slug")
        if not slug:
            continue
        out.append({
            "slug": slug,
            "title": p.get("title", slug),
            "folder": p.get("folder", ""),
            "karman": p.get("karman", slug),
            "autoheal": bool(p.get("autoheal")),
            # водитель сессий проекта (промт КАРМАНА #174): "" ⇒ по правилу
            "driver": str(p.get("driver") or "").strip().lower(),
            "dir": AREAS_ROOT / ("p-" + slug),
        })
    return out


# ───────────────────────────────────── водитель сессий (промт КАРМАНА #174)
#
# Правило «один водитель на проект» (G679 + зачитка владельца 26.11, 19:00):
# в каждый момент у проекта ОДИН водитель сессий — либо «Управляющий»
# (карусель или сам владелец), либо автомат управления сессиями, либо никто.
# Автомат аккаунтов (лечащий) водителем НЕ бывает: он меняет аккаунты, а в
# сессии не пишет ни одного сообщения (промт #174, п. 1).
#
# Переключатель — поле `driver` проекта в конфиге областей:
#   "" / «auto»      — по правилу: если в области работает Управляющий,
#                      водит он (автомат только наблюдает), иначе автомат;
#   «manager»        — Управляющий (автомат не действует);
#   «automaton»      — автомат управления сессиями;
#   «none»           — никто (наблюдение без действий).

DRIVER_AUTO = "auto"             # по умолчанию: решает наличие живого Управляющего
DRIVER_MANAGER = "manager"       # Управляющий
DRIVER_AUTOMATON = "automaton"   # автомат управления сессиями
DRIVER_NONE = "none"             # никто
DRIVERS = (DRIVER_AUTO, DRIVER_MANAGER, DRIVER_AUTOMATON, DRIVER_NONE)
DRIVER_TITLES = {
    DRIVER_AUTO: "по правилу",
    DRIVER_MANAGER: "Управляющий",
    DRIVER_AUTOMATON: "автомат",
    DRIVER_NONE: "никто",
}
#: Маркер сессии «Управляющего» в заголовке (заказ владельца 26.11):
#: такую сессию автомат не оживляет, не сбрасывает и не ротирует — как
#: служебные пробы.
MANAGER_MARKERS = ("ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО",)


def is_manager_title(title: str) -> bool:
    """Заголовок сессии Управляющего? («ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — …»)."""
    blob = (title or "").strip().upper()
    return any(marker in blob for marker in MANAGER_MARKERS)


def driver_title(driver: str) -> str:
    """Человеческое имя водителя («Управляющий» / «автомат» / «никто»)."""
    return DRIVER_TITLES.get(driver or "", DRIVER_TITLES[DRIVER_AUTO])


def area_sessions(area_dir: Path) -> list:
    """Неархивные сессии области — для правила водителя и запрета второй."""
    con = connect(area_db(area_dir))
    if not con:
        return []
    out = []
    try:
        for r in rows(con, "select session_id, status, session_kind, title, "
                          "workspace_dir, updated_at_ms from local_runtime_sessions "
                          "where coalesce(archived, 0) = 0"):
            out.append({"session_id": r[0] or "", "status": r[1] or "",
                        "kind": r[2] or "", "title": r[3] or "",
                        "workspace_dir": r[4] or "", "updated_at_ms": r[5] or 0})
    finally:
        con.close()
    return out


def live_sessions(area: dict) -> list:
    """Живые сессии области: рантайм считает их занятыми.

    «Живая» = идёт ход (`accepted` без завершения) **или** сессия в состоянии
    «started». Просто неархивная сессия (ход закончился) живой НЕ считается:
    иначе простой на минутах замораживал бы проект навсегда (D-249 ⑤:
    реально законченную работу продолжать не надо принудительно, а архивировать
    её — не дело автомата).

    Это и есть «порог живой сессии» из промта #174 («уточни, что считать
    живой»): **рантайм занят**, а не «сессия не архивирована».
    """
    area_dir = Path((area or {}).get("dir") or "")
    if not area_dir:
        return []
    busy = set()
    con = connect(area_db(area_dir))
    if con:
        try:
            busy = {str(r[0]) for r in rows(
                con, "select distinct session_id from local_runtime_turn_ingress "
                     "where status = 'accepted' and completed_at_ms is null")}
        finally:
            con.close()
    out = []
    for sess in area_sessions(area_dir):
        if sess["kind"] and sess["kind"] != "conversation":
            continue
        if sess["session_id"] in busy or sess["status"] == "started":
            out.append(sess)
    return out


def live_manager_session(area: dict) -> str:
    """Живая сессия «Управляющего» в области (id или пусто)."""
    for sess in live_sessions(area):
        if is_manager_title(sess["title"]):
            return sess["session_id"]
    return ""


def driver_of(area: dict) -> dict:
    """Кто водит сессиями в области сейчас.

    Возвращает `{"driver": …, "title": …, "why": …, "source": …}`:
    - `driver` — «manager» / «automaton» / «none»;
    - `why` — почему так (словами, для отчёта и окна);
    - `source` — «переключатель» (значение проекта) или «правило» (по умолчанию).
    """
    want = str((area or {}).get("driver") or "").strip().lower()
    if want == DRIVER_MANAGER:
        return {"driver": DRIVER_MANAGER, "title": driver_title(DRIVER_MANAGER),
                "why": "переключатель проекта: Управляющий", "source": "переключатель"}
    if want == DRIVER_AUTOMATON:
        return {"driver": DRIVER_AUTOMATON, "title": driver_title(DRIVER_AUTOMATON),
                "why": "переключатель проекта: автомат", "source": "переключатель"}
    if want == DRIVER_NONE:
        return {"driver": DRIVER_NONE, "title": driver_title(DRIVER_NONE),
                "why": "переключатель проекта: никто", "source": "переключатель"}
    mgr = live_manager_session(area)
    if mgr:
        return {"driver": DRIVER_MANAGER, "title": driver_title(DRIVER_MANAGER),
                "why": "в области работает Управляющий (%s)" % mgr[:12],
                "source": "правило"}
    return {"driver": DRIVER_AUTOMATON, "title": driver_title(DRIVER_AUTOMATON),
            "why": "Управляющего в области нет", "source": "правило"}


_RUNNING_CACHE = {"ts": 0.0, "set": set(), "mod": None}
RUNNING_TTL = 20.0          # сек: столько живёт ответ «какие области запущены»


def running_areas() -> set:
    """Какие области сейчас запущены (живой список из minimax-switch.py).

    Механика одна — `minimax-switch.py`; дублировать её нельзя (17.11:
    две реализации разъехались и лечащий «чинил» несуществующие области).

    ⚠️ Кэш и модуль держим **здесь**, а не внутри `minimax-switch.py`: модуль
    каждый раз грузится заново (`spec_from_file_location` + `exec_module`),
    и его собственный кэш (5 с) обнулялся бы на каждом вызове. Без этого
    кэша сторож сессий (проход каждые ~10 с) порождал новый PowerShell на
    каждый вызов — то есть «слишком много запросов» и мигающие окна (23.11).
    """
    import importlib.util
    now = time.time()
    if now - _RUNNING_CACHE["ts"] < RUNNING_TTL:
        return set(_RUNNING_CACHE["set"])
    try:
        mod = _RUNNING_CACHE["mod"]
        if mod is None:
            spec = importlib.util.spec_from_file_location(
                "minimax_switch", str(BIN / "minimax-switch.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _RUNNING_CACHE["mod"] = mod
        result = mod.running_areas() or set()
        _RUNNING_CACHE["ts"] = now
        _RUNNING_CACHE["set"] = set(result)
        return set(result)
    except Exception:  # noqa: BLE001
        return set()


# ────────────────────────────────────────────────────────── база рантайма

def area_db(area_dir: Path) -> Path:
    return area_dir / "v2" / "sqlite" / "runtime-state.sqlite"


def connect(db: Path):
    """Открыть базу рантайма **только на чтение**.

    ⚠️ База живёт под живым рантаймом: писать в неё нельзя (риск конкуренции
    и порчи состояния). Единственное исключение — архивирование сессии,
    и это делается точечным UPDATE с проверкой, см. `archive_session`.
    """
    if not db.is_file():
        return None
    try:
        return sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True, timeout=10)
    except sqlite3.Error:
        return None


def rows(con, sql, args=()) -> list:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


# ────────────────────────────────────────────────────────── запуск mcode

def area_env(area_dir: Path) -> dict:
    """Окружение для CLI, чтобы он работал с сессиями ИМЕННО этой области.

    Без `MINIMAX_DATA_DIR`/`MAVIS_DATA_DIR` CLI смотрит в общий каталог
    данных и не видит ни сессий, ни авторизации области.
    """
    env = dict(os.environ)
    env["MINIMAX_DATA_DIR"] = str(area_dir)
    env["MAVIS_DATA_DIR"] = str(area_dir)
    return env


def model_preferences() -> dict:
    """Модель и эффорт, выбранные оператором в программе «МиниГен».

    Читаются из её настроек (`%LOCALAPPDATA%\\minigen-gui\\settings.json`).
    Файла нет — пусто: тогда действуют **родные** дефолты области (те, что
    в её `config.yaml`), то есть выбор оператора ничего не ломает.
    """
    out = {"model": "", "effort": ""}
    path = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "minigen-gui" / "settings.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        out["model"] = str(data.get("model") or "")
        out["effort"] = str(data.get("modelEffort") or "")
    return out


def provider_of_area(area_dir: Path) -> str:
    """Провайдер области по её родному конфигу (`defaultModel: minimax/...`)."""
    try:
        text = (area_dir / "config.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "minimax"
    match = re.search(r"(?m)^defaultModel:\s*([A-Za-z0-9_.-]+)/", text)
    return match.group(1) if match else "minimax"


def model_flags(area_dir: Path, model: str = "", effort: str = "") -> list:
    """Флаги `--model/--effort` для запуска рантайма — родными словами.

    Рантайм умеет `--model <провайдер/модель>` и `--effort <уровень>`
    («override ... for this Run only»), поэтому выбор оператора действует и
    на уже открытые сессии, а не только на новые.
    """
    prefs = model_preferences()
    model = model or prefs["model"]
    effort = effort or prefs["effort"]
    out: list = []
    if model:
        out += ["--model", "%s/%s" % (provider_of_area(area_dir), model)]
    if effort:
        out += ["--effort", effort]
    return out


def set_compaction_trigger(area_dir: Path, fraction: float) -> dict:
    """Порог компактизации контекста рантайма — нативно.

    Рантайм сам сжимает контекст сессии, когда он близок к пределу
    (`contextManagement.compactionTriggerAt` в `config.yaml`; по умолчанию
    ~95 % окна). Порог — доля окна, и сжатие случается **внутри** сессии:
    контекст очищается, а сама сессия (её id и нитка) остаётся прежней —
    ровно то «очистить и начать заново, не начиная новую сессию».

    ⚠️ Решение владельца 24.11 (промт #96 в разделе MiniGen): в области
    `minigen` порог — **постоянные 0.9**, а НЕ «опустить на время сброса».
    Сброс сессии на месте лишь приводит конфиг области к этому значению
    (обычно он уже такой ⇒ `changed: false`, «уже так»); очистку делает сам
    рантайм — он сожмёт контекст на 90 % окна.

    Значение — доля окна (0.5 = 50 %) или абсолютное число токенов.

    ⚠️ Правка текстом: в конфиге есть YAML-якоря, пересборка файла их бы
    развернула. Правится только нужная строка внутри блока
    `contextManagement`. Запись атомарная (tmp + replace).
    """
    path = area_dir / "config.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "why": "не читается %s: %s" % (path, exc)}
    line = "  compactionTriggerAt: %s" % (fraction if fraction > 1 else float(fraction))
    block = re.compile(r"(?ms)^contextManagement:[^\n]*\n(?:[ \t]+[^\n]*\n)*")
    inner = re.compile(r"(?m)^[ \t]+compactionTriggerAt:.*$")
    if block.search(text):
        if inner.search(text):
            new = block.sub(lambda m: inner.sub(line, m.group(0), count=1), text, count=1)
        else:
            new = block.sub(lambda m: m.group(0).rstrip("\n") + "\n" + line + "\n", text, count=1)
    else:
        new = text.rstrip("\n") + "\ncontextManagement:\n" + line + "\n"
    if new == text:
        return {"ok": True, "changed": False, "why": "уже так"}
    tmp = path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return {"ok": False, "why": "не записалось: %s" % exc}
    return {"ok": True, "changed": True, "trigger": fraction}


def mcode_run(args: list, area_dir: Path, timeout: int = 300,
              background: bool = False, logfile=None, model: str = "",
              effort: str = ""):
    """Запустить `mcode` в контексте области.

    `background=True` — ход уходит в фоне (как «Продолжай»): ждать его в
    цикле нельзя, цикл должен обходить области по очереди.

    Флаги модели/эффорта вставляются **перед** текстом запроса: текст —
    позиционный аргумент, и флаг после него рантайм может принять за часть
    запроса.
    """
    env = area_env(area_dir)
    cmd = [str(MCODE)] + list(args)
    model_flags_list = model_flags(area_dir, model, effort)
    if model_flags_list and len(cmd) > 1:
        cmd = cmd[:1] + cmd[1:-1] + model_flags_list + cmd[-1:]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if background:
        out = logfile if logfile is not None else subprocess.DEVNULL
        return subprocess.Popen(cmd, env=env, stdout=out, stderr=out,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    try:
        return subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL,
                              creationflags=flags)
    except subprocess.TimeoutExpired:
        return None
    except Exception:  # noqa: BLE001
        return None


def now_ms() -> int:
    return int(time.time() * 1000)


def script_stamp(path: Path) -> float:
    """Время изменения файла скрипта (0 — файла нет).

    Нужен демонам: правка кода на диске не действует, пока процесс не
    перезапущен, а супервизор перезапускает демон только по смерти процесса
    ⇒ демон сам перезапускается, увидев, что его файл изменился (урок 22.11).
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def supervisor_alive(port: int = 8098) -> bool:
    """Жив ли супервизор «МиниГена» (страница управления на 8098)?"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def respawn_self() -> None:
    """Перезапустить себя на свежем коде — когда супервизора нет.

    С супервизором демона поднимает он (по смерти процесса); без него —
    поднимаем себя сами, иначе раскатанный код останется мёртвым.
    """
    import sys
    script = sys.argv[0] if sys.argv else ""
    if not script:
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([sys.executable, script] + sys.argv[1:],
                         cwd=str(Path(script).parent), creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


def fmt_ms(ms: int) -> str:
    """Метка времени → по Москве (везде в программе одно правило)."""
    return mtime.fmt_ms(ms)


# ────────────────────────────────────────────────────────── прокси и окружение

def user_proxy_env() -> dict:
    """Переменные прокси пользователя из реестра (`HKCU\\Environment`).

    ⚠️ Именно из реестра, а не из `os.environ`: демон запущен супервизором и
    унаследовал переменные с того момента, как поднялся; после починки прокси
    его собственное окружение устарело — читать надо источник.
    """
    out = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                if name.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
                    out[name.upper()] = str(value)
    except Exception:  # noqa: BLE001
        pass
    return out


def refresh_env() -> dict:
    """Обновить окружение процесса прокси-переменными из реестра.

    Зачем: `mcode exec` наследует прокси из окружения. Если прокси-переменные
    в реестре починили, а демон держит старые — новые ходы снова упадут с
    «Connection error». Поэтому перед каждой отправкой переменные
    перечитываются.
    """
    env = user_proxy_env()
    for name, value in env.items():
        os.environ[name] = value
    return env
