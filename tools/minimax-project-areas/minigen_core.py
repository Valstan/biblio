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
import time
from datetime import datetime
from pathlib import Path

BIN = Path(r"C:\Users\valstan\bin")
AREAS_ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT", r"C:\Users\valstan\.minimax-areas"))
CONFIG = BIN / "mcode-project-areas.json"
NET_CONFIG = BIN / "minigen-net.json"
MCODE = Path(os.environ.get("APPDATA", "")) / "npm" / "mcode.cmd"
LOCAL = Path(os.environ.get("LOCALAPPDATA", "")) / "mcode-web"
JOURNAL = LOCAL / "journal.log"
NET_STATE = LOCAL / "netwatch-state.json"
SESSION_STATE = LOCAL / "sessionwatch-state.json"

# ────────────────────────────────────────────────────────── журнал и состояние

def log(msg: str) -> None:
    """Строка в общий журнал «МиниГена» (его же читает панель)."""
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
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
            "dir": AREAS_ROOT / ("p-" + slug),
        })
    return out


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
    ~95 % окна). Если порог опустить, сжатие случится **внутри** сессии:
    контекст очистится, а сама сессия (её id и нитка) останется прежней —
    ровно то «очистить и начать заново, не начиная новую сессию».

    Значение — доля окна (0.5 = 50 %) или абсолютное число токенов.

    ⚠️ Правка текстом: в конфиге есть YAML-якоря, пересборка файла их бы
    развернула. Правится только нужная строка внутри блока
    `contextManagement`.
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
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m %H:%M:%S")


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
