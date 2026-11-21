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
            {"name": "happ", "proxy": "http://127.0.0.1:10808",
             "role": "основной", "expect_country": "NL"},
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


def running_areas() -> set:
    """Какие области сейчас запущены (живой список из minimax-switch.py).

    Механика одна — `minimax-switch.py`; дублировать её нельзя (17.11:
    две реализации разъехались и лечащий «чинил» несуществующие области).
    """
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("minimax_switch", str(BIN / "minimax-switch.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.running_areas() or set()
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


def mcode_run(args: list, area_dir: Path, timeout: int = 300,
              background: bool = False, logfile=None):
    """Запустить `mcode` в контексте области.

    `background=True` — ход уходит в фоне (как «Продолжай»): ждать его в
    цикле нельзя, цикл должен обходить области по очереди.
    """
    env = area_env(area_dir)
    cmd = [str(MCODE)] + list(args)
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
