"""Сбор состояния «МиниГена» — слой 2, единый источник правды.

Зачем этот модуль. До сих пор состояние жило в трёх местах: веб-панель
(`mcode-panel.js`) собирала его сама, три демона — каждый по-своему, и
файлы состояния на диске. Новая программа обязана показывать ОДНО и то же
состояние — и в окне, и в чате с встроенной нейросетью. Значит, сбор живёт
в одном месте, а GUI и агент читают его одним кодом.

Правила модуля:

- **ничего не чинит**: модуль только читает и собирает. Действия — в
  `minigen_bridge.py`, и вызывают те же функции демонов;
- **боевой код — один**: модули демонов подключаются из живой папки
  (`C:\\Users\\valstan\\bin`), а не из копии в репозитории. Копия в
  репозитории — эталон, из неё раскатывают (`minigen_rollout.py`);
- **сеть не дёргается без надобности**: очки и состояние вендора — по
  кнопке и по кэшу с TTL, список областей собирается только по диску.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# Эталон демонов в репозитории: tools/minimax-project-areas.
# ⚠️ 23.11: тут была ошибка, и её нашла встроенная нейросеть (PR #72).
# Ошибка была не в «сколько parent'ов», а в том, что папка не была указана:
# `HERE.parent.parent` — это `tools/`, а нужен `tools/minimax-project-areas`.
# Первая правка (три parent'а) увела на корень репозитория — мимо.
# Теперь путь проверяется тестом, а не «на глаз».
REPO_TOOLS = HERE.parent.parent / "minimax-project-areas"
# Корень репозитория: `tools/minigen-gui/bridge` → `tools/minigen-gui` →
# `tools` → корень. Нужен, чтобы окно, запущенное из дерева (разработка),
# показывало коммит рабочего дерева, а не «неизвестна».
# ⚠️ 24.11: этой константы не было, и `program_version()` падал с NameError
# (его ловили) ⇒ в окне разработки «Версия: неизвестна».
REPO = HERE.parent.parent.parent
LIVE_BIN = Path(os.environ.get("MINIGEN_BIN", r"C:\Users\valstan\bin"))
AREAS_ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT",
                                  str(Path.home() / ".minimax-areas")))
GUI_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
SETTINGS_FILE = GUI_HOME / "settings.json"
#: Локальная база «МиниГена» — свои данные программы (промты, планы и всё,
#: что программа складывает в базу). Рядом с настройками, но **вместо них не
#: вместо**: настройки читаются на каждом старте, база — данные (промт #92).
#: Схема базы не привязана к версии программы: новый вид данных — это новая
#: запись в общем контейнере, а не новая таблица.
DB_FILE = GUI_HOME / "minigen.db"
#: Папка для выгрузок базы (по умолчанию) — внутри дома программы, наружу
#: файл уходит только по явному действию оператора.
EXPORT_DIR = GUI_HOME / "exports"

DEFAULT_SETTINGS: dict[str, Any] = {
    # как часто окно перечитывает состояние, мс
    "refreshMs": 5000,
    # автообновление журнала во вкладке «Журнал»
    "journalTail": 200,
    # поднимать ли демонов, если их не жилёт (старая панель архивирована —
    # демонов держит сама программа)
    "manageDaemons": True,
    # область, чей конфиг провайдера использует чат
    "modelArea": "minigen",
    # модель по умолчанию (пусто = взять defaultModel из config.yaml)
    "model": "",
    # окно контекста модели (0 = взять родное значение области/модели)
    "modelContext": 0,
    # уровень эффорта модели (пусто = взять родное значение области/модели)
    "modelEffort": "",
    # разложить выбранные настройки по рабочим областям (их родные дефолты)
    "modelApplyToAreas": False,
    # окно чата: подтверждать опасные действия
    "confirmDangerous": True,
    # сколько строк журнала хранить для статистики
    "statsWindow": 5000,
    # где исходники программы (для правок и PR): программа может стоять
    # отдельно от репозитория — тогда путь задаёт установщик
    "codeRepo": "",
    # реестр агентов «МиниГена» (репозиторий на GitHub — источник правды:
    # по нему агента восстанавливают на любой машине)
    "agentsRegistry": "Valstan/minigen-agents",
    # где живут рабочие репозитории агентов
    "agentsWorkspace": r"D:\valstan\REPO",
    # где лежат промты: в базе «МиниГена» или на внешнем ресурсе (Карман)
    "promptStore": "local",
    # раздавать ли промты рабочим областям без живого оператора
    "promptAutoDispatch": False,
    # совет специалистов: обсуждать ли новое задание перед работой
    "councilEnabled": True,
    # сколько ролей совета реально отвечают на задачу (совет дорог)
    "councilMaxRoles": 3,
    # сколько раундов задачи может занять совет специалистов (дефолт — из
    # живых проб 26.11; решение — за владельцем)
    "councilRounds": 12,
    # гарантированный запас шагов для работы после обсуждения совета
    "councilWorkFloor": 12,
    # режим копии: "" (не выбран — определяется по состоянию машины),
    # "own" (свой репозиторий на GitHub), "shared" (папка в общем
    # репозитории), "local" (только локально, без сети)
    "copyMode": "",
    # репозиторий копии (в режиме «свой репозиторий»): Valstan/minigen-<id>
    "copyRepo": "",
    # где лежит токен копии (в сторе на машине, вне репозитория) — само
    # значение в настройках не хранится, только путь
    "copyTokenPath": "",
    # отпечаток токена (идентификатор): секрета не раскрывает
    "copyTokenId": "",
    # папка копии в общем репозитории: copies/<идентификатор копии>/
    "copyTokenFolder": "",
    # раздел КАРМАНА, куда уходят заявки на токен
    "copyRequestsSection": "MiniGen-tokens",
}


# ─────────────────────────────────────────────────────── подключение демонов

def daemon_dir() -> Path:
    """Папка с живыми демонами: сначала боевая, потом эталон из репозитория."""
    env = os.environ.get("MINIGEN_DAEMON_DIR")
    if env:
        return Path(env)
    if (LIVE_BIN / "minimax-sessionwatch.py").is_file():
        return LIVE_BIN
    return REPO_TOOLS


def _load_module(name: str, path: Path):
    """Подключить модуль демона по пути (имя файла ≠ имя модуля)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("не удалось подключить %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_daemons() -> dict[str, Any]:
    """Подключить ядро и демонов. Один раз на процесс."""
    cached = getattr(load_daemons, "_cache", None)
    if cached:
        return cached
    base = daemon_dir()
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    mods: dict[str, Any] = {"dir": base}
    core_path = base / "minigen_core.py"
    if not core_path.is_file():
        # Понятное сообщение вместо трассы FileNotFoundError: иначе ошибку
        # видно только в логе моста, а на экране — пустое окно.
        raise FileNotFoundError(
            "не найдено ядро «МиниГена»: %s (искали в %s и в %s)"
            % (core_path, LIVE_BIN, REPO_TOOLS))
    mods["core"] = _load_module("minigen_core", core_path)
    for attr, file in (
        ("switch", "minimax-switch.py"),
        ("sessionwatch", "minimax-sessionwatch.py"),
        ("netwatch", "minimax-netwatch.py"),
        ("autheal", "minimax-autheal.py"),
    ):
        path = base / file
        if path.is_file():
            mods[attr] = _load_module("minigen_%s" % attr, path)
    load_daemons._cache = mods          # type: ignore[attr-defined]
    return mods


def jsonable(value: Any) -> Any:
    """Привести значение к тому, что можно отдать в JSON (Path → str)."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ───────────────────────────────────────────────────────────── настройки

def read_settings() -> dict[str, Any]:
    """Настройки программы. Файла нет ⇒ дефолты (работать должны)."""
    out = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return out


def write_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Записать настройки (только известные ключи) и вернуть результат."""
    cur = read_settings()
    for key, value in (patch or {}).items():
        if key in DEFAULT_SETTINGS:
            cur[key] = value
    try:
        GUI_HOME.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)          # атомарно: читатель не увидит половину
    except OSError:
        pass
    return cur


# ────────────────────────────────────────────────────────────────── время

# Формат метки времени в боевом журнале: «[2026-11-24 16:29:01] ТЕГ: текст».
JOURNAL_TS_FMT = "%Y-%m-%d %H:%M:%S"


def msk_stamp(ms: int) -> str:
    """Метка времени по Москве — в формате журнала.

    ⚠️ У ядра `fmt_ms(ms)` — **один** аргумент: формат зашит в модуль
    времени. Мост раньше звал `fmt_ms(ms, формат)` ⇒ `TypeError` ⇒ команда
    `state.snapshot` падала, а мост отдавал «успешный» пустой ответ — и
    окно показывало «0 областей», «демоны 0/0», «надзирателя нет», хотя
    демоны живы (живой случай 24.11). Теперь формат задаём здесь, а время
    берём из общего модуля времени — того же, что у ядра и демонов.
    """
    try:
        import minigen_time as mtime
        return mtime.fmt_ms(ms, JOURNAL_TS_FMT)
    except Exception:  # noqa: BLE001
        pass
    # Запасной путь — та же Москва (UTC+3), но без модуля времени.
    from datetime import datetime, timedelta, timezone
    msk = timezone(timedelta(hours=3), "MSK")
    return datetime.fromtimestamp(int(ms or 0) / 1000, msk).strftime(JOURNAL_TS_FMT)


# ──────────────────────────────────────────────────────────────── области

def read_projects() -> dict[str, Any]:
    """Конфиг областей. Читаем с повтором: файл пишется на лету (гонка)."""
    core = load_daemons()["core"]
    last_err = ""
    for _ in range(5):
        try:
            text = (core.CONFIG).read_text(encoding="utf-8")
            return json.loads(text)
        except ValueError as exc:
            last_err = str(exc)
            time.sleep(0.2)
        except OSError as exc:
            last_err = str(exc)
            break
    return {"projects": [], "_error": last_err}


def write_projects(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Записать конфиг областей АТОМАРНО (иначе читатель поймает половину).

    Атомарность на Windows не бесплатна: `os.replace` поверх файла, который
    сейчас открыт другим процессом (читатель в Python держит файл без
    FILE_SHARE_DELETE), отказывает с PermissionError. Поэтому
    переименование повторяем: окно у читателя — миллисекунды. Если и это
    не помогло — пишем на место: действие владельца важнее атомарности, а
    читатель при сбое разбора перечитывает файл.

    ⚠️ **Перед записью — локальная копия** (`…json.bak`). Конфиг областей —
    **чисто локальная вещь компьютера** (канон владельца 27.11): рабочие
    области агентов и их состав живут на машине, и в GitHub этому файлу не
    место ⇒ копии в репозитории больше нет, и **единственная** страховка —
    рядом на диске. Без неё побитый файл = потерянные области и потерянный
    `authTemplate` (путь к области с авторизацией).

    Правила копии (проверены приёмкой):

    - текущий файл **читается** ⇒ в копию уходит **предыдущая годная версия**;
    - текущий файл **бит** ⇒ хорошая копия **не трогается** (битое в копию не
      годится);
    - копии **не было** ⇒ после записи она появляется (иначе после первой
      записи страховки нет вовсе).
    """
    core = load_daemons()["core"]
    try:
        cfg = dict(cfg)
        cfg.pop("_error", None)
        core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
        backup = Path(str(core.CONFIG) + ".bak")
        prev_valid = _copy_if_valid(core.CONFIG, backup)
        tmp = core.CONFIG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        last = ""
        for _ in range(30):
            try:
                os.replace(str(tmp), str(core.CONFIG))
                if not prev_valid and not backup.is_file():
                    _copy_if_valid(core.CONFIG, backup)
                return True, ""
            except PermissionError as exc:
                last = str(exc)
                time.sleep(0.1)
            except OSError as exc:
                return False, str(exc)
        # Запасной путь: пишем на место — действие важнее атомарности.
        try:
            tmp.unlink()
        except OSError:
            pass
        core.CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        if not prev_valid and not backup.is_file():
            _copy_if_valid(core.CONFIG, backup)
        return True, "записано на место (атомарная замена не удалась: %s)" % last
    except OSError as exc:
        return False, str(exc)


def enforce_autoheal() -> list[str]:
    """Привести области к автомату аккаунтов (промт КАРМАНА #182).

    Ручного режима в «МиниГене» больше нет: колонка «Режим» убрана из окна
    вместе с переключателем («Автомат / Вручную»), и **выключить автомат
    нечем**. Значит в сохранённом состоянии области не должно остаться
    области с выключенным автоматом — приводим их к автомату (решение
    владельца: при обновлении автомат включается у всех областей
    принудительно).

    Возвращает список областей, которые переведены в автомат (пусто — все
    уже в автомате, и тогда файл не трогаем вовсе).
    """
    cfg = read_projects()
    projects = cfg.get("projects") or []
    fixed: list[str] = []
    for project in projects:
        if not project.get("autoheal"):
            project["autoheal"] = True
            fixed.append(str(project.get("slug") or ""))
    if not fixed:
        return []
    ok, _err = write_projects(cfg)
    if not ok:
        return []
    return fixed


def _copy_if_valid(src: Path, dst: Path) -> bool:
    """Скопировать файл в `dst`, **если он читается** (валидный JSON).

    True — скопировали (и `dst` теперь годная копия). False — файла нет, он
    бит или копия не записалась ⇒ вызывающий решает сам.
    """
    try:
        text = src.read_text(encoding="utf-8")
        json.loads(text)
    except (OSError, ValueError):
        return False
    try:
        dst.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def local_account(mods: dict[str, Any], area_dir: Path) -> dict[str, Any]:
    """Состояние входа области — по локальным файлам, БЕЗ запроса вендору.

    Вендорный опрос (очки) делается отдельно и по кнопке: 19 запросов —
    это десятки секунд, а список областей должен открываться мгновенно.
    """
    switch = mods.get("switch")
    out: dict[str, Any] = {"email": "", "expires_at": None, "app_status": "",
                           "has_record": False, "has_refresh": False, "state": "нет входа"}
    if switch is None:
        return out
    try:
        store = switch.read_store(area_dir)
        records = store.get("records") or {}
        rec = None
        for value in records.values():
            if isinstance(value, dict):
                rec = value
                break
        meta = switch.read_meta(area_dir)
        app = switch.read_app_state(area_dir)
    except Exception:  # noqa: BLE001
        return out
    status = str(app.get("status") or "")
    out["app_status"] = status
    out["email"] = str(meta.get("email") or (rec or {}).get("email") or "")
    out["has_record"] = bool(rec)
    if rec:
        out["has_refresh"] = bool(rec.get("refreshToken"))
        out["expires_at"] = rec.get("expiresAtMs")
    if status == "authorizing":
        out["state"] = "вход в процессе"
    elif status == "lease-expired":
        out["state"] = "код входа протух"
    elif status and status != "authenticated":
        out["state"] = "вышел из аккаунта"
    elif rec:
        out["state"] = "есть вход"
    return out


def driver_of_area(mods: dict[str, Any], area: dict[str, Any]) -> dict[str, Any]:
    """Кто водит сессиями в области сейчас (промт КАРМАНА #174).

    Правило «один водитель на проект»: либо «Управляющий», либо автомат
    управления сессиями, либо никто. Переключатель проекта (`driver`) важнее
    правила; правило по умолчанию — «если в области работает Управляющий, водит
    он, иначе автомат».

    ⚠️ Ядро на машине может быть старше программы (без `driver_of`) — тогда
    честно говорим «не знаю», а не выдумываем водителя.
    """
    core = mods["core"]
    fn = getattr(core, "driver_of", None)
    if fn is None:
        return {"driver": "", "title": "—",
                "why": "ядро без правила водителя (обновите демоны)",
                "source": ""}
    try:
        out = fn(area) or {}
    except Exception:  # noqa: BLE001 — водитель не должен ломать окно
        return {"driver": "", "title": "—", "why": "водителя не определили",
                "source": ""}
    return {"driver": str(out.get("driver") or ""),
            "title": str(out.get("title") or ""),
            "why": str(out.get("why") or ""),
            "source": str(out.get("source") or "")}


# ────────────────────────────────────────────────────────── активность области
#
# Промт КАРМАНА #208: у области должен быть **счётчик активности** — сколько
# раз она получала аккаунты и обновления аккаунтов MiniMax из Кармана.
#
# Считаем **по журналу автомата аккаунтов** — это единственное место, где
# видно, кому и когда Карман отдал аккаунт. Ничего не придумывается на глаз:
# счётчик — это посчитанные следы.
#
# Два события (слова владельца — «получение аккаунтов и обновления аккаунтов»):
#
# - **«Карман отдал: id=…»** — Карман выдал области аккаунт;
# - **«аккаунт получен»** — автомат поставил этот аккаунт в область (обновил
#   вход).
#
# ⚠️ «Карман отдал негодный токен — пробую следующий» — это **отказ**, а не
# выдача: поэтому маркер с двоеточием («Карман отдал:»), а не без него.

ACTIVITY_GRANTED_MARK = "карман отдал:"
ACTIVITY_UPDATED_MARK = "аккаунт получен"

#: Кэш счёта: пересчитываем, только когда журнал изменился (подпись — время
#: и размер файла). Журнал на машине — мегабайты, а окно спрашивает список
#: областей каждые несколько секунд.
_ACTIVITY_CACHE: dict[str, Any] = {"key": None, "data": {}}


def _journal_signature(path: Path) -> tuple[str, int, int]:
    """Подпись журнала (путь + время изменения + размер) — для кэша счёта."""
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, 0)


def activity_counters(mods: dict[str, Any],
                       areas: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Счётчики активности областей — по журналу автомата аккаунтов.

    Возвращает словарь `slug → {granted, updated, total, last}`:

    - `granted` — сколько раз Карман отдал области аккаунт;
    - `updated` — сколько раз автомат обновил вход области;
    - `total` — сумма (её и показывает окно);
    - `last` — когда было последнее такое событие.

    Пустой счётчик у области, вовсе не получавшей аккаунтов, — это **ноль**,
    а не «нет данных»: так владелец видит, что агент простаивал.
    """
    core = mods["core"]
    path = core.JOURNAL
    signature = _journal_signature(path)
    if _ACTIVITY_CACHE.get("key") == signature:
        return dict(_ACTIVITY_CACHE.get("data") or {})
    areas = list(areas if areas is not None else core.areas())
    counters: dict[str, dict[str, Any]] = {
        str(area["slug"]): {"granted": 0, "updated": 0, "total": 0, "last": ""}
        for area in areas
    }
    # заголовок строки журнала — название области («Память: …»)
    by_title: dict[str, str] = {}
    # раздел КАРМАНА → области (может быть несколько — тогда по разделу не
    # определяем: счётчик не должен достаться не той области)
    by_karman: dict[str, list[str]] = {}
    for area in areas:
        slug = str(area["slug"])
        by_title[str(area.get("title") or slug).strip().lower()] = slug
        by_karman.setdefault(str(area.get("karman") or slug).strip().lower(),
                              []).append(slug)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        raw = []
    for line in raw:
        ts, rest = split_journal_line(line)
        low = rest.lower()
        granted = ACTIVITY_GRANTED_MARK in low
        updated = ACTIVITY_UPDATED_MARK in low
        if not granted and not updated:
            continue
        slug = _journal_area_slug(rest, by_title, by_karman)
        if not slug:
            continue
        rec = counters.get(slug)
        if rec is None:
            continue
        if granted:
            rec["granted"] += 1
        if updated:
            rec["updated"] += 1
        rec["total"] = rec["granted"] + rec["updated"]
        if ts and ts > str(rec.get("last") or ""):
            rec["last"] = ts
    _ACTIVITY_CACHE["key"] = signature
    _ACTIVITY_CACHE["data"] = counters
    return dict(counters)


def _journal_area_slug(text: str, by_title: dict[str, str],
                       by_karman: dict[str, list[str]]) -> str:
    """К какой области относится строка журнала.

    Порядок:

    1. **заголовок строки** («Память: …») — название области: самое точное;
    2. **раздел КАРМАНА** («(проект memory)») — только если он указывает на
       одну область (у нескольких областей раздел бывает общий);
    3. **имя каталога** («p-brain») — запасной путь.
    """
    head = text.split(":", 1)[0].strip().lower()
    if head and head in by_title:
        return by_title[head]
    m = re.search(r"\(проект\s+([^)]+)\)", text, re.IGNORECASE)
    if m:
        names = by_karman.get(m.group(1).strip().lower()) or []
        if len(names) == 1:
            return names[0]
    m = AREA_RE.search(text)
    if m:
        return m.group(1)
    return ""


def areas_state(mods: dict[str, Any], include_sessions: bool = True,
                only_running: bool = False) -> list[dict[str, Any]]:
    """Список областей с их живым состоянием."""
    core = mods["core"]
    running = core.running_areas() or set()
    all_areas = core.areas()
    # счётчик активности — по журналу автомата аккаунтов (промт КАРМАНА #208)
    activity = activity_counters(mods, all_areas)
    out: list[dict[str, Any]] = []
    for area in all_areas:
        dir_name = area["dir"].name
        is_running = dir_name in running
        if only_running and not is_running:
            continue
        item: dict[str, Any] = {
            "slug": area["slug"],
            "title": area["title"],
            "folder": area.get("folder") or "",
            "karman": area.get("karman") or area["slug"],
            # ⚠️ Промт КАРМАНА #182: автомат аккаунтов всегда включён. Берём
            # правило из ядра, но с запасным значением `True` — на машине может
            # стоять старое ядро (без константы), а ручного режима больше нет
            # ни в какой версии.
            "autoheal": bool(getattr(core, "AUTOHEAL_ALWAYS", True)),
            "dir": str(area["dir"]),
            "running": is_running,
            "exists": area["dir"].is_dir(),
            "account": local_account(mods, area["dir"]),
            # ⚠️ Промт КАРМАНА #208: счётчик активности области — сколько раз
            # она получала аккаунты и обновления аккаунтов из Кармана. Считает
            # автомат аккаунтов (по журналу), а не человек на глаз.
            "activity": dict(activity.get(str(area["slug"]))
                             or {"granted": 0, "updated": 0, "total": 0, "last": ""}),
            # водитель сессий проекта (промт КАРМАНА #174)
            "driver": str(area.get("driver") or ""),
            "driverNow": driver_of_area(mods, area),
        }
        if include_sessions and is_running and area["dir"].is_dir():
            item["sessions"] = session_verdicts(mods, area)
        else:
            item["sessions"] = []
        out.append(item)
    return out


def session_verdicts(mods: dict[str, Any], area: dict[str, Any]) -> list[dict[str, Any]]:
    """Вердикты по сессиям области — та же классификация, что у сторожа."""
    watcher = mods.get("sessionwatch")
    if watcher is None:
        return []
    try:
        return jsonable(watcher.survey(area, {}))
    except Exception:  # noqa: BLE001
        return []


def area_by_slug(mods: dict[str, Any], slug: str) -> dict[str, Any]:
    for area in mods["core"].areas():
        if area["slug"] == slug:
            return area
    return {}


# ─────────────────────────────────────────────────────────────── сеть

def network_state(mods: dict[str, Any], probe: bool = False) -> dict[str, Any]:
    """Состояние сети: пути, вендор, прокси- переменные.

    `probe=True` — живой опрос (идёт в сеть, занимает секунды). Без пробы
    берём последний результат сетевого сторожа из его файла состояния.
    """
    core = mods["core"]
    netwatch = mods.get("netwatch")
    out: dict[str, Any] = {"paths": [], "vendor": {}, "proxy": {},
                            "stable": False, "last_run": {}, "probed": False}
    state = core.read_state(core.NET_STATE)
    last = state.get("last_run") or {}
    out["last_run"] = last
    out["stable"] = bool(last.get("stable"))
    cfg = core.net_config()
    if probe and netwatch is not None:
        try:
            out["paths"] = jsonable([netwatch.probe_path(p, cfg)
                                     for p in (cfg.get("paths") or [])])
        except Exception:  # noqa: BLE001
            out["paths"] = []
        try:
            out["vendor"] = jsonable(netwatch.probe_vendor())
        except Exception:  # noqa: BLE001
            out["vendor"] = {}
        try:
            out["proxy"] = jsonable(netwatch.proxy_state())
        except Exception:  # noqa: BLE001
            out["proxy"] = {}
        out["probed"] = True
        return out
    # без пробы — то, что уже измерил сторож
    for path in last.get("paths") or []:
        out["paths"].append(path)
    out["vendor"] = last.get("vendor") or {}
    out["proxy"] = last.get("proxy") or {}
    return out


def program_version() -> dict[str, Any]:
    """Какая версия программы запущена.

    Урок 23.11: «программа, живущая в рабочем дереве репозитория, не имеет
    версии» — она меняется вместе с веткой. Установленная копия версию
    имеет: при установке рядом с программой пишется `version.json`
    (коммит исходников + время установки). Если файла нет (запуск из
    репозитория, разработка) — показываем коммит рабочего дерева.
    """
    out: dict[str, Any] = {"commit": "", "installed_at": 0, "source": "",
                           "installed": False, "running_from": str(HERE.parent)}
    bridge_dir = HERE
    for parent in bridge_dir.parents:
        version_file = parent / "version.json"
        if version_file.is_file():
            try:
                data = json.loads(version_file.read_text(encoding="utf-8"))
                out.update({k: v for k, v in data.items() if k in out})
                out["installed"] = True
                out["running_from"] = str(parent)
                return out
            except (OSError, ValueError):
                break
    # запуск из репозитория (разработка): показываем коммит дерева
    try:
        res = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        commit = (res.stdout or "").strip()
        if commit:
            out["commit"] = commit
            out["source"] = "рабочее дерево (разработка)"
    except Exception:  # noqa: BLE001
        pass
    return out


def daemons_status(mods: dict[str, Any]) -> dict[str, Any]:
    """Кто из демонов жив.

    С 23.11 (Ф6) старая панель и её супервизор архивированы — демонов держит
    сама программа «МиниГен». Поэтому смотрим на процессы демонов, а не на
    порт 8098.
    """
    try:
        import minigen_daemons as daemons
        return daemons.status()
    except Exception:  # noqa: BLE001
        return {"daemons": [], "alive_count": 0, "total": 0}


# ────────────────────────────────────────────────────────────── журнал

JOURNAL_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(?P<rest>.*)$")
# строка без скобок: «2026-11-23 15:22:58 — p-minigen: клетка 7 …»
PLAIN_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*(?:—|-|–)?\s*(?P<rest>.*)$")
TS_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")


def journal_path(mods: dict[str, Any]) -> Path:
    return mods["core"].JOURNAL


def split_journal_line(line: str) -> tuple[str, str]:
    """Разобрать строку журнала на (время, текст).

    Формат в боевом журнале нестрогий: есть `[время] ТЕГ: текст`, есть
    `[время] текст`, а есть `время — текст` (без скобок). Разбираем все
    три, иначе фильтр по времени работает по половине журнала.
    """
    m = JOURNAL_RE.match(line)
    if m:
        return m.group("ts"), m.group("rest")
    m = PLAIN_TS_RE.match(line)
    if m:
        return m.group("ts"), m.group("rest")
    m = TS_RE.match(line)
    if m:
        return m.group("ts"), line[m.end():].strip()
    return "", line



def read_journal(mods: dict[str, Any], limit: int = 200, contains: str = "",
                 tag: str = "", since: str = "") -> list[dict[str, Any]]:
    """Хвост журнала «МиниГена» с фильтрами.

    Формат строки в боевом журнале нестрогий: есть `[время] ТЕГ: текст`,
    есть `[время] текст`, а есть `время текст` (без скобок). Разбираем
    оба вида, чтобы фильтр по времени работал.
    """
    path = journal_path(mods)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    needle = (contains or "").strip().lower()
    for line in raw[-max(limit * 20, 2000):]:
        ts, rest = split_journal_line(line)
        if since and ts and ts < since:
            continue
        if needle and needle not in line.lower():
            continue
        if tag:
            head = rest.split(":", 1)[0].strip().upper()
            if head != tag.upper() and tag.upper() not in rest.upper():
                continue
        out.append({"ts": ts, "text": rest or line})
    return out[-max(limit, 1):]



# ───────────────────────────────────────────────────────────── статистика

# Причины событий — по словам в журнале. Порядок важен: первая подошедшая
# причина и есть ответ (отказ по очкам важнее «перезапуска»).
REASON_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("отказ по очкам", ("50110", "insufficient balance", "отказ по очкам",
                        "очки кончились", "нехватка баланса")),
    ("обрыв сети", ("упала по сети", "connection error", "fetch failed",
                     "сеть недоступна", "обрыв связи", "econnreset", "таймаут")),
    ("нестабильность вендора", ("вендор недоступен", "model service is unstable",
                                "нестабильн", "перегрузк")),
    ("компактизация контекста", ("скомпактил", "компакт", "контекст заполнен",
                                 "ротация", "ротир", "сжат рантаймом",
                                 "сжала рантаймом", "сжал контекст")),
    ("потеря входа", ("потерян вход", "нет входа", "вышел из аккаунта",
                      "login lost", "lease-expired")),
    ("ручной перезапуск", ("перезапуск", "перезапустил", "restart", "старт десктопа",
                            "запустил десктоп")),
]

# Событие = причина + признак действия. Без признака действия причина не
# считается событием: строка «сеть недоступна» в отчёте — это не отказ.
ACTION_MARKERS = ("перезапуст", "ротац", "продолжай", "лечу", "лечение",
                   "лечит", "вылеч", "оживил", "восстановлен", "сменил",
                   "переоткрыл", "открыл новую", "перезаход")
# Сетевые события считаем отдельно: это работа сетевого сторожа, а не
# перезапуск области.
NETWORK_MARKERS = ("сеть:", "netwatch", "каскад", "прокси", "туннел", "happ")

AREA_RE = re.compile(r"p-([A-Za-z0-9_-]+)")


def classify_reason(text: str) -> str:
    low = text.lower()
    for reason, needles in REASON_RULES:
        if any(n in low for n in needles):
            return reason
    return "прочее"


def _event_kind(text: str) -> str:
    """Что именно произошло: перезапуск, ротация, оживление, лечение.

    Основы слов — как в журнале: «перезапускаю», «ротирую», «лечу»,
    «Продолжай». Точные формы («перезапуст», «ротац») в тексте не встречаются
    и молча ничего не находят.
    """
    low = text.lower()
    if "ротац" in low or "ротир" in low or "компакт" in low:
        return "ротация"
    if "лечу" in low or "лечение" in low or "лечит" in low or "вылеч" in low:
        return "лечение"
    if "продолжай" in low:
        return "оживление"
    if any(m in low for m in ("перезапуск", "переоткрыл", "сменил",
                              "запустил десктоп", "старт десктопа")):
        return "перезапуск"
    if "оживил" in low or "восстановлен" in low:
        return "оживление"
    return "прочее"


def stats_summary(mods: dict[str, Any], days: int = 7) -> dict[str, Any]:
    """Сводка работы: события по причинам, по областям и по видам.

    Перезапуски считаются **по журналу**: журнал — единственное место, где
    записано, что сторож делал. Состояние сессий — из живой классификации.
    """
    since = time.time() - max(days, 1) * 86400
    # Граница окна — **по Москве** (заказ владельца 24.11): журнал пишется в
    # московском времени, значит и фильтр должен быть московским, а не
    # «локальным» (на другой машине окно статистики поехало бы на часы).
    # Формат задаёт мост (у ядра он зашит), время — общее модуль времени.
    since_ts = msk_stamp(int(since * 1000))
    lines = read_journal(mods, limit=20000, since=since_ts)
    slugs = [a["slug"] for a in mods["core"].areas()]
    per_area: dict[str, dict[str, Any]] = {}
    reasons: dict[str, int] = {}
    kinds: dict[str, int] = {}
    events: list[dict[str, Any]] = []
    total = 0
    network_events = 0
    for line in lines:
        text = line["text"]
        low = text.lower()
        if any(m in low for m in NETWORK_MARKERS) and "сеть" in low:
            network_events += 1
            continue
        reason = classify_reason(text)
        if reason == "прочее":
            continue
        kind = _event_kind(text)
        if kind == "прочее":
            continue
        total += 1
        reasons[reason] = reasons.get(reason, 0) + 1
        kinds[kind] = kinds.get(kind, 0) + 1
        slug = ""
        m = AREA_RE.search(text)
        if m:
            slug = m.group(1)
        if not slug:
            for area in slugs:
                if area and re.search(r"\b%s\b" % re.escape(area), text):
                    slug = area
                    break
        if not slug:
            slug = "—"
        rec = per_area.setdefault(slug, {"slug": slug, "total": 0, "reasons": {},
                                         "last": line["ts"], "running": False})
        rec["total"] += 1
        rec["reasons"][reason] = rec["reasons"].get(reason, 0) + 1
        if line["ts"] > (rec["last"] or ""):
            rec["last"] = line["ts"]
        events.append({"ts": line["ts"], "slug": slug, "kind": kind,
                       "reason": reason, "text": text[:200]})
    running = mods["core"].running_areas() or set()
    for area in mods["core"].areas():
        rec = per_area.get(area["slug"])
        if rec is not None:
            rec["running"] = area["dir"].name in running
    events.reverse()
    return {
        "at": int(time.time() * 1000),
        "days": days,
        "restarts": total,
        "network_events": network_events,
        "by_reason": reasons,
        "by_kind": kinds,
        "by_area": sorted(per_area.values(), key=lambda r: -r["total"]),
        "events": events[-120:],
        "areas_total": len(mods["core"].areas()),
        "areas_running": len([a for a in mods["core"].areas()
                               if a["dir"].name in running]),
    }



# ──────────────────────────────────────────────────────────── снимок целиком

def snapshot(mods: dict[str, Any], include_sessions: bool = True) -> dict[str, Any]:
    """Снимок состояния «МиниГена» — то, что показывает окно."""
    core = mods["core"]
    projects = read_projects()
    return {
        "at": int(time.time() * 1000),
        "areas": areas_state(mods, include_sessions=include_sessions),
        "network": network_state(mods, probe=False),
        "daemons": daemons_status(mods),
        "stats": stats_summary(mods, days=1),
        "config_error": projects.get("_error", ""),
        "journal_path": str(core.JOURNAL),
        "areas_root": str(AREAS_ROOT),
        "daemon_dir": str(mods.get("dir")),
    }
