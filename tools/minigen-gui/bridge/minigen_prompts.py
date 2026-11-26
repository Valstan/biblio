"""«Хранилище промтов» «МиниГена»: где лежат промты и планы.

Заказ владельца (24.11, промт #94 в разделе `MiniGen` библиотеки КАРМАНА):
«встроить в МиниГен структуру Хранилище промтов, причём выбор должен быть, где
промты хранятся: в базе МиниГена или на внешнем ресурсе, доступ на который можно
настроить… в моём случае это сайт Карман со своим хранилищем промтов, которым
пользуются мои проекты… чтобы отсылать рабочим областям проектов эти промты
прямо из хранилища автоматически, если есть такое задание на самостоятельное
автоматическое программирование без участия живого оператора».

Два провайдера хранилища:

- `LocalPromptStore` — **в локальной базе «МиниГена»** (SQLite,
  `%LOCALAPPDATA%\\minigen-gui\\minigen.db`): работает всегда и без сети;
- `KarmanPromptStore` — **внешний ресурс** (Карман со своим хранилищем):
  читает разделы секретом канала (тот приходит в программу сам из
  vault-комнаты), архивирует своим токеном раздела (`PROMPTS_TOKEN_<Раздел>`,
  он тоже лежит в vault-комнате, если выдан грант).

⚠️ Промты лежат в **универсальной базе** программы (`minigen_db.py`): данные
хранятся **видами** (`prompt`, `plan`), а не таблицами — чтобы база переживала
смену версии программы и её можно было выгрузить в один файл и залить в
другую копию «МиниГена» (промт #92). Старая база хранилища (`prompts.db`)
переносится в новую один раз (`migrate_legacy_store`), а её файл остаётся на
месте — запасная копия.

Выбор места — **настройка окна** (`promptStore`: `local` | `karman`). Данные
каждого хранилища живут у своего хозяина: переключение места не переносит
промты, а меняет, откуда программа их берёт.

⚠️ Секреты — не в репозитории и не в журнале: адрес и токены берутся из
vault-комнаты или из файлов ключей, наружу уходит только **источник**.

Запись промта — общая форма (`PromptRecord`):

    id, project (раздел), title, body, kind ('prompt' | 'plan'), archived,
    createdAt, source ('local' | 'karman'), auto (пометка владельца)

Правила раздачи (их проверяет приёмка, они не украшение):

- **раздел промта = проект области**: промт раздела `MatricaRMZ` идёт в область
  `matricarmz`; иначе — отказ с объяснением (задание уедет не туда);
- **без живого оператора** раздаётся только то, что владелец сам пометил как
  «автоматическое» (`auto`), и тоже только в область своего проекта;
- **архив = сделано**: архивирует тот, кто выполнил (программа архивирует по
  команде владельца, а не сама);
- **план из хранилища — задание, а не приговор**: проект уточняет план у себя.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import minigen_db
import minigen_karman as karman
import minigen_state as state

#: Дом программы на машине: тут настройки, база и журнал.
GUI_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
#: **Универсальная база «МиниГена»** — промты, планы и всё, что программа
#: складывает в базу. Схема не привязана к версии программы (промт #92), база
#: выгружается в один файл и заливается в другую копию «МиниГена».
LOCAL_DB = state.DB_FILE
#: Старая база хранилища промтов (таблица на каждую сущность — привязана к
#: версии программы). Из неё данные переносятся один раз; файл остаётся на
#: месте как запасная копия.
LEGACY_DB = GUI_HOME / "prompts.db"

SOURCE_LOCAL = "local"
SOURCE_KARMAN = "karman"
KIND_PROMPT = "prompt"
KIND_PLAN = "plan"
#: Виды служебных отметок (пометка «раздавать автоматически» и журнал
#: отправки) — в универсальной базе это тоже записи, просто свои виды.
KIND_MARK = "prompt-mark"
KIND_DISPATCH = "prompt-dispatch"

#: Что делать, когда канал архива закрыт: сказать, **кто** и **что** может
#: открыть. Иначе «канал не настроен» — это не причина, а молчание.
#:
#: ⚠️ Живой случай 26.11 (промт #100): канал раздела отвечал 503 (выдача не
#: принята), а раздел при этом читался по каналу библиотеки.
ARCHIVE_HINT = ("Архив ставит только канал раздела: его токен должен быть выдан "
                "сервису (выдача в комнату-получателя — её принимает получатель). "
                "Сами промты при этом читаются — по каналу библиотеки.")


def archive_reason(reason: str) -> str:
    """Причина + подсказка «что делать» — **двумя предложениями**.

    ⚠️ Склейка через пробел читалась как «не настроен Архив ставит только
    канал раздела…» — два утверждения в одном предложении.
    """
    base = (reason or "").strip()
    if base and not base.endswith((".", "!", "?", ":", ";")):
        base += "."
    return ("%s %s" % (base, ARCHIVE_HINT)).strip()

#: Сколько символов промта уходит в область (текст задания).
MAX_TASK_TEXT = 20_000
#: Кэш чтения внешнего ресурса (секунды): окно спрашивает хранилище каждые
#: 5 секунд, разделов двадцать — без кэша это двадцать запросов за кадр.
CACHE_TTL = 60.0
#: Кэш внешнего ресурса в памяти процесса (значения секретов здесь нет).
#:
#: ⚠️ Списки разделов и промты кэшируются **по разделу**: окно спрашивает
#: «все разделы» (и то же самое делает автораздача по каждой области), а
#: разделы пересекаются — без общего кэша один и тот же раздел читался бы
#: двадцать раз за кадр.
_cache: dict[str, Any] = {"sections": [], "sections_at": 0.0,
                           "sections_code": 0,
                           "by_section": {}, "archived_by_section": {}}


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_stamp(value: Any) -> str:
    """Метка времени — строкой ISO (так же, как у внешнего ресурса).

    ⚠️ С **миллисекундами**: записи сортируются по этой метке, и без них два
    промта, сохранённые в одну секунду, вставали в случайном порядке (план
    мог обогнать промт, с которым он идёт в паре — живой случай 26.11).
    """
    try:
        ms = int(value or 0)
    except (TypeError, ValueError):
        return str(value or "")
    if ms <= 0:
        return ""
    stamp = time.gmtime(ms / 1000.0)
    return "%s.%03dZ" % (time.strftime("%Y-%m-%dT%H:%M:%S", stamp), ms % 1000)


# ───────────────────────────────────────────────────────────── вид записи

def detect_kind(title: str, body: str) -> str:
    """Промт это или план (канон КАРМАНА: план — с заголовком «План: …»)."""
    head = (title or "").strip().lower()
    if head.startswith("план:"):
        return KIND_PLAN
    for line in (body or "").splitlines():
        text = line.strip().lower().lstrip("#").strip()
        if not text:
            continue
        if text.startswith("план:"):
            return KIND_PLAN
        break
    return KIND_PROMPT


def normalize_record(raw: dict[str, Any], source: str) -> dict[str, Any]:
    """Запись хранилища → единый вид (одинаково для обоих провайдеров)."""
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    created = raw.get("createdAt") or raw.get("created_at") or ""
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in (KIND_PROMPT, KIND_PLAN):
        kind = detect_kind(title, body)
    return {
        "id": str(raw.get("id") or ""),
        "project": str(raw.get("project") or "").strip(),
        "title": title,
        "body": body,
        "kind": kind,
        "archived": bool(raw.get("archived")),
        "createdAt": str(created),
        "source": source,
    }


# ──────────────────────────────────────────────────────── база «МиниГена»

def legacy_ensure_schema(con: sqlite3.Connection) -> None:
    """Старая схема хранилища — нужна, чтобы прочитать базу при переносе."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        " id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,"
        " body TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'prompt',"
        " archived INTEGER NOT NULL DEFAULT 0,"
        " created_at INTEGER NOT NULL DEFAULT 0)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS marks ("
        " source TEXT NOT NULL, project TEXT NOT NULL, id TEXT NOT NULL,"
        " auto INTEGER NOT NULL DEFAULT 0, at INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (source, project, id))")
    con.execute(
        "CREATE TABLE IF NOT EXISTS dispatched ("
        " source TEXT NOT NULL, project TEXT NOT NULL, id TEXT NOT NULL,"
        " area TEXT NOT NULL DEFAULT '', at INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (source, project, id))")
    con.commit()


def legacy_connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Открыть **старую** базу хранилища — только на чтение.

    Старая база нужна для переноса (одноразового) и для запасного чтения,
    если перенос по какой-то причине не прошёл.
    """
    target = Path(db_path or LEGACY_DB)
    if not target.is_file():
        raise sqlite3.Error("старой базы нет: %s" % target)
    uri = target.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _legacy_tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("select name from sqlite_master where type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def _payload_of(raw: Any) -> dict[str, Any]:
    """Содержимое записи — словарём (битое содержимое не должно ронять чтение)."""
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {"value": payload}


def _mark_key(source: str, project: str, prompt_id: str) -> str:
    """Ключ служебной отметки (пометка «раздавать», журнал отправки)."""
    return "%s|%s|%s" % (str(source or ""), str(project or ""),
                         str(prompt_id or ""))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Открыть **универсальную** базу «МиниГена» (схема создаётся сама)."""
    return minigen_db.connect(Path(db_path) if db_path else LOCAL_DB)


# ─────────────────────────────────────────────── отметки владельца (marks)

def get_mark(source: str, project: str, prompt_id: str,
             db_path: Path | None = None) -> bool:
    """Помечен ли промт владельцем как «раздавать автоматически»."""
    con = connect(db_path)
    try:
        rec = minigen_db.get_record(con, KIND_MARK,
                                    _mark_key(source, project, prompt_id))
        return bool((rec or {}).get("payload", {}).get("auto"))
    finally:
        con.close()


def set_mark(source: str, project: str, prompt_id: str, auto: bool,
             db_path: Path | None = None) -> dict[str, Any]:
    """Пометить (или снять пометку) — только владелец может."""
    con = connect(db_path)
    try:
        minigen_db.put_record(con, KIND_MARK,
                              _mark_key(source, project, prompt_id),
                              {"auto": bool(auto), "at": now_ms(),
                               "source": str(source or ""),
                               "project": str(project or ""),
                               "id": str(prompt_id or "")})
        return {"ok": True, "auto": bool(auto)}
    finally:
        con.close()


def already_dispatched(source: str, project: str, prompt_id: str,
                       db_path: Path | None = None) -> bool:
    """Раздавали ли этот промт уже — чтобы не раздать дважды."""
    con = connect(db_path)
    try:
        rec = minigen_db.get_record(con, KIND_DISPATCH,
                                    _mark_key(source, project, prompt_id))
        return rec is not None
    finally:
        con.close()


def mark_dispatched(source: str, project: str, prompt_id: str, area: str,
                     db_path: Path | None = None) -> None:
    con = connect(db_path)
    try:
        minigen_db.put_record(con, KIND_DISPATCH,
                              _mark_key(source, project, prompt_id),
                              {"area": area or "", "at": now_ms(),
                               "source": str(source or ""),
                               "project": str(project or ""),
                               "id": str(prompt_id or "")})
    finally:
        con.close()


# ───────────────────────────────────────────────── локальное хранилище

def migrate_legacy_store(legacy_path: Path | None = None,
                         universal_path: Path | None = None) -> dict[str, Any]:
    """Перенести старую базу хранилища в универсальную базу программы.

    Старая база привязана к версии программы: в ней **таблица на каждую
    сущность**. Универсальная база хранит то же содержимое **видами**
    (`prompt`, `plan`, `prompt-mark`, `prompt-dispatch`) — и потому её можно
    выгрузить в один файл и залить в другую копию «МиниГена» (промт #92).

    Перенос **безопасен и повторяем**: он идёт при каждом старте программы, но
    переносит только то, чего в новой базе ещё нет. Старый файл при этом **не
    трогается** — он остаётся запасной копией.
    """
    source = Path(legacy_path or LEGACY_DB)
    target = Path(universal_path or LOCAL_DB)
    out: dict[str, Any] = {"ok": True, "migrated": 0, "why": "",
                            "legacy": str(source), "target": str(target)}
    if not source.is_file():
        out["why"] = "старой базы нет — переносить нечего"
        return out
    try:
        if source.resolve() == target.resolve():
            out["why"] = "старой базы нет — переносить нечего"
            return out
    except OSError:
        pass
    try:
        con = minigen_db.connect(target)
    except sqlite3.Error as exc:
        return {"ok": False, "migrated": 0,
                "why": "новая база не открылась: %s" % exc}
    try:
        marker = "legacy_prompts_migrated"
        try:
            old = legacy_connect(source)
        except sqlite3.Error as exc:
            return {"ok": False, "migrated": 0,
                    "why": "старая база не открылась: %s" % exc}
        try:
            tables = _legacy_tables(old)
            moved = 0
            total = 0
            if "records" in tables:
                for row in old.execute(
                        "select id, project, title, body, kind, archived,"
                        " created_at from records").fetchall():
                    kind = str(row["kind"] or KIND_PROMPT)
                    if kind not in (KIND_PROMPT, KIND_PLAN):
                        kind = KIND_PROMPT
                    ident = str(row["id"])
                    total += 1
                    # уже есть в новой базе — не переносим (перенос идёт при
                    # каждом старте и должен быть безопасным)
                    if minigen_db.get_record(con, kind, ident) is not None:
                        continue
                    payload = {
                        "project": str(row["project"] or ""),
                        "title": str(row["title"] or ""),
                        "body": str(row["body"] or ""),
                        "archived": bool(row["archived"]),
                        "kind": kind,
                        "createdAt": iso_stamp(row["created_at"]),
                    }
                    minigen_db.put_record(con, kind, ident, payload,
                                          created_at=payload["createdAt"])
                    moved += 1
            if "marks" in tables:
                for row in old.execute(
                        "select source, project, id, auto, at from marks").fetchall():
                    ident = _mark_key(row["source"], row["project"], row["id"])
                    total += 1
                    if minigen_db.get_record(con, KIND_MARK, ident) is not None:
                        continue
                    minigen_db.put_record(
                        con, KIND_MARK, ident,
                        {"auto": bool(row["auto"]), "at": int(row["at"] or 0),
                         "source": str(row["source"] or ""),
                         "project": str(row["project"] or ""),
                         "id": str(row["id"] or "")})
                    moved += 1
            if "dispatched" in tables:
                for row in old.execute(
                        "select source, project, id, area, at"
                        " from dispatched").fetchall():
                    ident = _mark_key(row["source"], row["project"], row["id"])
                    total += 1
                    if minigen_db.get_record(con, KIND_DISPATCH, ident) is not None:
                        continue
                    minigen_db.put_record(
                        con, KIND_DISPATCH, ident,
                        {"area": str(row["area"] or ""), "at": int(row["at"] or 0),
                         "source": str(row["source"] or ""),
                         "project": str(row["project"] or ""),
                         "id": str(row["id"] or "")})
                    moved += 1
        finally:
            old.close()
        con.execute("insert into schema_meta(key,value) values(?,?)"
                    " on conflict(key) do update set value=excluded.value",
                    (marker, "%s|%d" % (minigen_db.now_iso(), total)))
        con.commit()
        out["migrated"] = moved
        out["total"] = total
        out["why"] = ("перенесено записей из старой базы: %d (в старой базе: %d)"
                      % (moved, total))
    except sqlite3.Error as exc:
        return {"ok": False, "migrated": 0, "why": "перенос не прошёл: %s" % exc}
    finally:
        con.close()
    return out


def migration_state(db_path: Path | None = None) -> dict[str, Any]:
    """Состояние переноса из старой базы — по отметке в `schema_meta`.

    Отметка хранит время переноса и число перенесённых записей.
    """
    out: dict[str, Any] = {"migrated": False, "at": "", "count": 0}
    try:
        con = minigen_db.connect(Path(db_path) if db_path else LOCAL_DB)
    except sqlite3.Error:
        return out
    try:
        row = con.execute("select value from schema_meta where key=?",
                          ("legacy_prompts_migrated",)).fetchone()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    if not row:
        return out
    raw = str(row["value"] or "")
    parts = raw.split("|")
    out["migrated"] = True
    out["at"] = parts[0] if parts else ""
    try:
        out["count"] = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        out["count"] = 0
    return out


class LocalPromptStore:
    """Промты в универсальной базе «МиниГена» — работает всегда и без сети.

    ⚠️ Хранилище **переехало** в универсальную базу программы: там данные
    лежат видами, а не таблицами, — чтобы базу можно было выгрузить в один
    файл и залить в другую копию «МиниГена» (промт #92). Старая база
    (`prompts.db`) переносится один раз, а её файл остаётся на месте.
    """

    kind = SOURCE_LOCAL
    label = "в базе «МиниГена»"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or LOCAL_DB)
        self.legacy_path = Path(LEGACY_DB)
        self._legacy_cache: list[dict[str, Any]] | None = None
        # ⚠️ Конструктор **ничего не делает с диском**: ни базу не открывает,
        # ни старую не трогает. Перенос из старой базы — отдельное действие
        # (`migrate_legacy_store`), его зовёт мост при старте программы.
        # Иначе любой, кто собрал хранилище (даже проверка в тестах), задел бы
        # настоящую базу на машине владельца.

    def _is_program_db(self) -> bool:
        """Это настоящая база программы (а не подменённый путь)?"""
        try:
            return self.db_path.resolve() == Path(state.DB_FILE).resolve()
        except OSError:
            return False

    def location(self) -> str:
        return str(self.db_path)

    def _legacy_records(self) -> list[dict[str, Any]]:
        """Записи из старой базы — запасной путь, если перенос не прошёл.

        ⚠️ Старая база — **собственность программы**: её читает только
        настоящая база программы. Подменённый путь (проверка, чужой каталог)
        чужой файл не открывает.
        """
        if self._legacy_cache is not None:
            return self._legacy_cache
        out: list[dict[str, Any]] = []
        if self._is_program_db() and self.legacy_path.is_file():
            try:
                old = legacy_connect(self.legacy_path)
            except sqlite3.Error:
                old = None
            if old is not None:
                try:
                    if "records" in _legacy_tables(old):
                        for row in old.execute(
                                "select id, project, title, body, kind, archived,"
                                " created_at from records").fetchall():
                            out.append({
                                "id": str(row["id"]),
                                "project": str(row["project"] or ""),
                                "title": str(row["title"] or ""),
                                "body": str(row["body"] or ""),
                                "kind": str(row["kind"] or KIND_PROMPT),
                                "archived": bool(row["archived"]),
                                "createdAt": iso_stamp(row["created_at"]),
                            })
                except sqlite3.Error:
                    pass
                finally:
                    old.close()
        self._legacy_cache = out
        return out

    def _records(self) -> list[dict[str, Any]]:
        """Записи хранилища: из универсальной базы и то, что осталось в старой.

        Старая база — **запасной путь**: если перенос по какой-то причине не
        прошёл, промты у владельца всё равно видны (и не теряются).
        """
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "select id, key, payload, created_at from records"
                " where kind in (?,?) and deleted_at is null",
                (KIND_PROMPT, KIND_PLAN)).fetchall()
        finally:
            con.close()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            payload = _payload_of(row["payload"])
            ident = str(row["key"])
            seen.add(ident)
            out.append({
                "id": ident,
                "project": str(payload.get("project") or ""),
                "title": str(payload.get("title") or ""),
                "body": str(payload.get("body") or ""),
                "kind": str(payload.get("kind") or KIND_PROMPT),
                "archived": bool(payload.get("archived")),
                "createdAt": str(payload.get("createdAt")
                                 or iso_stamp(row["created_at"])),
                # порядок вставки — чтобы сортировка была **однозначной**, даже
                # когда две записи сохранились в одну миллисекунду
                "_order": int(row["id"] or 0),
            })
        for item in self._legacy_records():
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            item["_order"] = 0
            out.append(item)
        out.sort(key=lambda item: (str(item.get("createdAt") or ""),
                                    int(item.get("_order") or 0)), reverse=True)
        return out

    def sections(self) -> list[str]:
        names = {item["project"] for item in self._records() if item["project"]}
        return sorted(names)

    def own_section(self, slug: str) -> str:
        """Свой раздел области по её слагу.

        В **локальной** базе разделы те, что владелец разложил сам, — но
        правило то же: если в базе есть раздел, отвечающий слагу области, то
        он и есть её раздел (и раздел **другой** области сюда не берётся).
        Нет такого — пусто (путь «как раньше»).
        """
        want = str(slug or "").strip().lower()
        if not want:
            return ""
        for name in self.sections():
            if str(name).strip().lower() == want:
                return str(name).strip()
        return ""

    def list_prompts(self, project: str = "", include_archived: bool = False,
                     only_archived: bool = False) -> list[dict[str, Any]]:
        out = []
        want = str(project or "").strip().lower()
        for item in self._records():
            # ⚠️ Имя раздела в библиотеке каноническое (`MatricaRMZ`), а
            # область называется слагом (`matricarmz`) ⇒ сравнение без
            # учёта регистра, иначе раздел не найдётся никогда.
            if want and item["project"].strip().lower() != want:
                continue
            if only_archived:
                if not item["archived"]:
                    continue
            elif not include_archived and item["archived"]:
                continue
            out.append(normalize_record(item, SOURCE_LOCAL))
        return out

    def save(self, project: str, title: str, body: str) -> dict[str, Any]:
        name = (project or "").strip()
        if not name:
            return {"ok": False, "error": "без раздела промт некуда положить"}
        if not (body or "").strip():
            return {"ok": False, "error": "промт пуст"}
        prompt_id = uuid.uuid4().hex[:8]
        kind = detect_kind(title or "", body)
        payload = {"project": name, "title": (title or "").strip()[:200],
                   "body": body, "archived": False, "kind": kind,
                   "createdAt": iso_stamp(now_ms())}
        con = connect(self.db_path)
        try:
            minigen_db.put_record(con, kind, prompt_id, payload,
                                  created_at=payload["createdAt"])
        finally:
            con.close()
        return {"ok": True, "id": prompt_id, "project": name}

    def archive(self, prompt_id: str, project: str = "",
                archived: bool = True) -> dict[str, Any]:
        """Архивировать (или вернуть из архива) — «сделано»."""
        con = connect(self.db_path)
        try:
            rec = (minigen_db.get_record(con, KIND_PROMPT, prompt_id)
                   or minigen_db.get_record(con, KIND_PLAN, prompt_id))
            if rec is None:
                return {"ok": False, "error": "такого промта в базе нет"}
            payload = dict(rec.get("payload") or {})
            payload["archived"] = bool(archived)
            minigen_db.put_record(con, str(rec.get("kind") or KIND_PROMPT),
                                  prompt_id, payload,
                                  version=int(rec.get("version") or 1),
                                  created_at=rec.get("created_at"),
                                  updated_at=rec.get("updated_at"))
        finally:
            con.close()
        return {"ok": True, "archived": bool(archived)}

    def status(self) -> dict[str, Any]:
        try:
            con = connect(self.db_path)
        except sqlite3.Error as exc:
            return {"ok": False, "reason": "база не открылась: %s" % exc}
        con.close()
        count = 0
        for item in self._records():
            if not item["archived"]:
                count += 1
        out = {"ok": True,
               "reason": "в базе «МиниГена» живых промтов: %d" % count,
               "sections": self.sections()}
        state_of_migration = migration_state(self.db_path)
        if state_of_migration.get("migrated") and state_of_migration.get("count"):
            out["reason"] += " (перенесено из старой базы: %d)" % int(
                state_of_migration["count"])
        return out


# ──────────────────────────────────────────── внешний ресурс (Карман)

class KarmanPromptStore:
    """Промты на внешнем ресурсе — Карман со своим хранилищем промтов.

    Чтение — секретом канала (он приходит в программу сам из vault-комнаты).
    Архивирование — **токеном раздела** (`PROMPTS_TOKEN_<Раздел>`): если
    гранта нет, раздел не отдаётся (`503`) ⇒ честное сообщение, а не тишина.
    """

    kind = SOURCE_KARMAN
    label = "на внешнем ресурсе"

    def location(self) -> str:
        base, source = karman.read_base()
        if not base:
            return ""
        host = base.rstrip("/").split("://", 1)[-1]
        return "%s (адрес из %s)" % (host, source or "—")

    def _sections_fetch(self) -> tuple[int, list[str]]:
        """(код, разделы) — с кэшем на минуту.

        ⚠️ Окно спрашивает хранилище **раз в 5 секунд**, а разделов двадцать:
        без кэша сервис получает двадцать запросов за кадр. Кэш на минуту —
        компромисс: новый промт виден с задержкой до минуты, зато сервис не
        засыпается запросами.
        """
        now = time.time()
        if now - float(_cache.get("sections_at") or 0) < CACHE_TTL:
            return int(_cache.get("sections_code") or 200), list(
                _cache.get("sections") or [])
        code, names = karman.list_sections()
        if code == 200:
            _cache.update({"sections": list(names), "sections_at": now,
                           "sections_code": code})
        return code, list(names)

    def sections(self) -> list[str]:
        return self._sections_fetch()[1]

    def own_section(self, slug: str) -> str:
        """Свой раздел области по её слагу — каноническое имя из библиотеки.

        ⚠️ Имя раздела в библиотеке **каноническое** (`MiniGen`,
        `MatricaRMZ`), а у области — **слаг** (`minigen`, `matricarmz`) ⇒
        ищем без учёта регистра.

        Пусто — раздела по слагу нет (список недоступен или такого раздела
        нет): тогда остаётся запасной путь области (поле `karman`).
        """
        want = str(slug or "").strip().lower()
        if not want:
            return ""
        for section in self.sections():
            if str(section).strip().lower() == want:
                return str(section).strip()
        return ""

    def canonical_section(self, name: str) -> str:
        """Каноническое имя раздела (регистр не важен).

        ⚠️ Имя раздела в библиотеке **каноническое** (`MiniGen`, `MatricaRMZ`),
        а у области — **слаг** (`minigen`, `matricarmz`) ⇒ читать надо по
        каноническому имени. Иначе сервис отвечает «пусто» (имя не совпало),
        а это выглядит как «промтов нет» — тишина вместо правды (живой случай
        26.11, промт #94: по «minigen» приходило 0 записей, по «MiniGen» — 11).

        Пустая строка — **такого раздела в библиотеке нет** (не «промтов
        нет»). Если список разделов недоступен — возвращаем имя как есть:
        тогда правду скажет сам сервис.
        """
        want = (name or "").strip()
        if not want:
            return ""
        sections = self.sections()
        if not sections:
            return want
        for section in sections:
            if str(section).strip().lower() == want.lower():
                return str(section).strip()
        return ""

    def _read_section(self, project: str, archived: bool = False
                      ) -> list[dict[str, Any]]:
        """Промты одного раздела (канал Мозга) — по каноническому имени."""
        name = self.canonical_section(project)
        if not name:
            raise UnknownSection("раздела «%s» в библиотеке нет"
                                  % (str(project or "—").strip() or "—"))
        query = "&archived=1" if archived else ""
        # ⚠️ Имя раздела — в адрес **экранированным**: кириллица в имени иначе
        # роняет запрос (UnicodeEncodeError) — а это молчаливый отказ целой
        # вкладки (живой случай 26.11).
        code, payload = karman.call(
            "GET", "/api/prompts?project=%s%s"
            % (urllib.parse.quote(name, safe=""), query))
        if code != 200:
            raise KarmanUnavailable(karman.explain(code))
        out = []
        for item in payload.get("prompts") or []:
            if not isinstance(item, dict):
                continue
            archived_at = bool(item.get("archivedAt"))
            out.append(normalize_record({
                "id": item.get("id"),
                "project": item.get("project") or name,
                "title": item.get("title"),
                "body": item.get("body"),
                "createdAt": item.get("createdAt"),
                "archived": archived_at,
            }, SOURCE_KARMAN))
        return out

    def _read_section_cached(self, project: str, archived: bool = False
                             ) -> list[dict[str, Any]]:
        """То же, но с кэшем по разделу (окно спрашивает раз в 5 секунд)."""
        name = self.canonical_section(project)
        if not name:
            raise UnknownSection("раздела «%s» в библиотеке нет"
                                  % (str(project or "—").strip() or "—"))
        bucket = "archived_by_section" if archived else "by_section"
        cache = _cache.setdefault(bucket, {})
        hit = cache.get(name)
        now = time.time()
        if hit and now - float(hit[0]) < CACHE_TTL:
            return list(hit[1])
        items = self._read_section(project, archived=archived)
        cache[name] = (now, list(items))
        return items

    def list_prompts(self, project: str = "", include_archived: bool = False,
                     only_archived: bool = False
                     ) -> list[dict[str, Any]]:
        """Промты раздела (или всех разделов — с кэшем на минуту).

        ⚠️ Окно спрашивает хранилище каждые 5 секунд, а разделов двадцать:
        без кэша это двадцать запросов к сервису за кадр. Кэш на минуту —
        компромисс: промт появится с задержкой до минуты, зато сервис не
        засыпается запросами.
        """
        if project:
            items = self._read_section_cached(project)
            if include_archived or only_archived:
                items = items + self._read_section_cached(project, archived=True)
        else:
            items = []
            for section in self.sections():
                try:
                    items.extend(self._read_section_cached(section))
                except (KarmanUnavailable, UnknownSection):
                    continue
            if include_archived or only_archived:
                for section in self.sections():
                    try:
                        items.extend(
                            self._read_section_cached(section, archived=True))
                    except (KarmanUnavailable, UnknownSection):
                        continue
        if only_archived:
            return [item for item in items if item.get("archived")]
        if not include_archived:
            return [item for item in items if not item.get("archived")]
        return items

    def save(self, project: str, title: str, body: str) -> dict[str, Any]:
        name = (project or "").strip()
        if not name:
            return {"ok": False, "error": "без раздела промт некуда положить"}
        code, payload = karman.publish(name, (title or "").strip()[:200], body)
        if code not in (200, 201):
            return {"ok": False,
                    "error": "промт не записан: %s" % karman.explain(code)}
        return {"ok": True, "id": str(payload.get("id") or ""), "project": name}

    def archive(self, prompt_id: str, project: str = "",
                archived: bool = True) -> dict[str, Any]:
        """Архивировать в разделе (или вернуть из архива) — токеном раздела.

        ⚠️ Раздел в запросе — **каноническое** имя (`MiniGen`, а не слаг
        `minigen`): сервис фильтрует по `prompt.project` точным равенством,
        и «промт не найден в разделе» из-за имени — это молчание вместо
        правды. И токен берётся по тому же каноническому имени.
        """
        name = (project or "").strip()
        if not name:
            return {"ok": False, "error": "не указан раздел"}
        canonical = self.canonical_section(name) or name
        secret_name = section_secret_name(canonical)
        token, source = karman.vault_secret(secret_name)
        if not token:
            return {"ok": False,
                    "error": "токена раздела нет (%s) — архивировать нечем;"
                             " попроси грант в комнату программы"
                             % secret_name}
        try:
            pid = int(prompt_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "плохой id промта"}
        code, payload = karman.call("POST", "/api/prompts/section",
                                    {"project": canonical, "id": pid,
                                     "archived": bool(archived)},
                                    token=token)
        if code != 200:
            reason = karman.explain(code)
            if code == 503:
                # ⚠️ «Канал не настроен» — не причина, а молчание: сказать,
                # **что делать**. Архивирует только канал раздела (токен
                # раздела), а он 503, пока сервис не видит этот токен.
                reason = archive_reason(reason)
            return {"ok": False,
                    "error": "раздел не принял: %s" % reason}
        return {"ok": True, "archived": bool(archived), "token_source": source}

    def status(self) -> dict[str, Any]:
        secret, source = karman.read_secret()
        base, base_source = karman.read_base()
        if not secret or not base:
            return {"ok": False,
                    "reason": "секрет канала не задан — внешний ресурс недоступен",
                    "sections": []}
        code, names = self._sections_fetch()
        if code != 200:
            return {"ok": False, "reason": karman.explain(code), "sections": []}
        return {"ok": True, "sections": list(names),
                "reason": "канал живой, разделов: %d (секрет из %s)"
                          % (len(names), source or "—")}


class KarmanUnavailable(RuntimeError):
    """Внешний ресурс не отдал промты — честное сообщение, а не тишина."""


class UnknownSection(RuntimeError):
    """Такого раздела в библиотеке нет — молчать нельзя.

    Отличается от `KarmanUnavailable`: ресурс **жив**, но раздела в нём нет.
    Пустой список промтов в этом случае — тишина, а она обманывает: владелец
    решит, что заданий нет, а их просто нет в этой библиотеке.
    """


def section_secret_name(section: str) -> str:
    """Имя секрета раздела: ``PROMPTS_TOKEN_<Раздел>`` (канон КАРМАНА).

    ⚠️ Правило — **как у сервиса** (``karman/lib/prompts/section-auth.ts``,
    ``sectionSecretName``): раздел в верхнем регистре, а всё, что не буква и
    не цифра, ⇒ один ``_``. Своя копия правила: разойдёмся с сервером — положим
    токен не под тем именем, и канал молча не откроется (503).

        MiniGen      → PROMPTS_TOKEN_MINIGEN
        Matrica RMZ  → PROMPTS_TOKEN_MATRICA_RMZ
    """
    cleaned = re.sub(r"[^A-Z0-9]+", "_", str(section or "").strip().upper())
    return "PROMPTS_TOKEN_%s" % cleaned


def store_for(settings: dict[str, Any]) -> LocalPromptStore | KarmanPromptStore:
    """Провайдер хранилища по настройке окна."""
    kind = str((settings or {}).get("promptStore") or SOURCE_LOCAL).strip().lower()
    if kind == SOURCE_KARMAN:
        return KarmanPromptStore()
    return LocalPromptStore()


# ──────────────────────────────────────────────── раздача в рабочие области

def area_sections(area: dict[str, Any], store: Any = None) -> list[str]:
    """Разделы, которые подходят области: её слаг и её раздел в КАРМАНЕ.

    Слаг — это имя проекта (`matricarmz`), а раздел в библиотеке промтов —
    каноническое имя (`MatricaRMZ`). Сравнение — без учёта регистра.

    ⚠️ **Свой раздел — только свой.** У области `minigen` поле `karman` — это
    `biblio`: по реестру это имя проекта в КАРМАНе (учёт расхода MiniMax и
    комната vault'а), а **не** раздел библиотеки промтов. Взять `biblio` отсюда
    — значит забрать работу дома утилит в область программы (живой случай
    26.11: раздел `MiniGen` программы и раздел `biblio` дома — разные).

    Поэтому: если внешний ресурс знает **свой** раздел области (каноническое
    имя по слагу: `minigen` → `MiniGen`), берётся только он. Поле `karman`
    остаётся запасным путём для областей, у которых слаг на раздел не похож
    (`sabantuy` → `SabantuyMalmyzh`).
    """
    out: list[str] = []
    for name in (str(area.get("slug") or ""), str(area.get("karman") or "")):
        clean = name.strip()
        if clean and clean.lower() not in [item.lower() for item in out]:
            out.append(clean)
    if store is not None:
        try:
            own = str(store.own_section(str(area.get("slug") or "")) or "")
        except Exception:  # noqa: BLE001
            own = ""
        if own:
            return [own]
    return out


def section_matches(section: str, area: dict[str, Any], store: Any = None) -> bool:
    """Раздел промта = проект области? (иначе задание уедет не туда)."""
    want = (section or "").strip().lower()
    if not want:
        return False
    return want in [item.lower() for item in area_sections(area, store)]


def build_task_text(record: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
    """Текст задания для области: промт и (если есть рядом) план."""
    title = (record.get("title") or "").strip()
    body = (record.get("body") or "").strip()
    project = (record.get("project") or "").strip()
    head = "Задание из «Хранилища промтов»"
    if project:
        head += " (раздел «%s»)" % project
    parts = [head]
    if title:
        parts.append("# %s" % title)
    if body:
        parts.append(body)
    if plan:
        plan_body = (plan.get("body") or "").strip()
        if plan_body and plan_body not in body:
            parts.append(plan_body)
    text = "\n\n".join(parts).strip()
    return text[:MAX_TASK_TEXT]


def find_plan(records: list[dict[str, Any]], record: dict[str, Any]
              ) -> dict[str, Any] | None:
    """План рядом с промтом (тот же раздел, вид «план»).

    ⚠️ План выбирается **ближайший по времени** к промту: в разделе может
    лежать несколько промтов и несколько планов, и «первый попавшийся» план
    мог принадлежать другому заданию (живой случай 26.11).
    """
    if record.get("kind") == KIND_PLAN:
        return None
    project = (record.get("project") or "").strip()
    want = str(record.get("createdAt") or "")
    best: dict[str, Any] | None = None
    best_gap = -1
    for item in records:
        if item.get("kind") != KIND_PLAN:
            continue
        if (item.get("project") or "").strip() != project:
            continue
        if item.get("id") == record.get("id"):
            continue
        stamp = str(item.get("createdAt") or "")
        gap = _time_gap(want, stamp)
        if best is None or gap < best_gap:
            best = item
            best_gap = gap
    return best


def _time_gap(left: str, right: str) -> int:
    """Расстояние между двумя метками времени (мс). Метки не разобрались — 0."""
    fmt = "%Y-%m-%dT%H:%M:%S"
    try:
        text = (left or "").strip().split(".")[0]
        other = (right or "").strip().split(".")[0]
        if not text or not other:
            return 0
        a = datetime.datetime.strptime(text, fmt)
        b = datetime.datetime.strptime(other, fmt)
        return abs(int((a - b).total_seconds() * 1000))
    except (ValueError, TypeError):
        return 0


def area_busy(mods: dict[str, Any], area: dict[str, Any]) -> bool:
    """Занята ли область живой сессией.

    ⚠️ План промта #94: автораздача идёт «когда появилось новое задание **и
    есть свободная область проекта**». Без этой проверки задание уходит в
    область, где уже идёт работа, — лишние очки и сбитая нитка.
    """
    try:
        verdicts = state.session_verdicts(mods, area) or []
    except Exception:  # noqa: BLE001
        return False
    for verdict in verdicts:
        if str(verdict.get("verdict") or "") in ("работает", "ход завис"):
            return True
    return False


def dispatch_to_area(mods: dict[str, Any], area: dict[str, Any],
                     record: dict[str, Any], plan: dict[str, Any] | None = None,
                     store: Any = None) -> dict[str, Any]:
    """Отдать промт в область: открыть там сессию с текстом задания.

    Механизм — тот же, что у Управляющего (`minimax-sessionwatch.py --new`):
    отдельный механизм не изобретаем.
    """
    watcher = mods.get("sessionwatch")
    if watcher is None:
        return {"ok": False, "error": "сторож сессий недоступен"}
    if not section_matches(str(record.get("project") or ""), area, store):
        return {"ok": False,
                "error": "раздел промта «%s» ≠ проект области «%s»: задание "
                         "уедет не туда" % (record.get("project") or "—",
                                           area.get("slug") or "—")}
    text = build_task_text(record, plan)
    if not text:
        return {"ok": False, "error": "промт пуст"}
    res = watcher.new_session(area, text)
    return {"ok": bool(res.get("ok")), "why": res.get("why") or "",
            "area": area.get("slug") or ""}


def auto_dispatch_scan(mods: dict[str, Any], store: Any,
                        settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Раздать помеченные промты в области их проекта — без живого оператора.

    Раздаётся **только** то, что владелец пометил как «автоматическое»
    (`auto`), и только в область своего проекта. Занятая область ждёт
    следующего прохода (план промта #94: «есть **свободная** область
    проекта»).

    ⚠️ Каждая причина отказа — **в ответе**, а не молчание: владелец должен
    видеть, почему промт не ушёл (живой случай 26.11: раздел не читался, а
    молчание выглядело как «заданий нет»).
    """
    out: list[dict[str, Any]] = []
    watcher = mods.get("sessionwatch")
    if watcher is None:
        return out
    core = mods.get("core")
    if core is None:
        return out
    try:
        areas = core.areas()
    except Exception:  # noqa: BLE001
        return out
    for area in areas:
        slug = str(area.get("slug") or "")
        sections = area_sections(area, store)
        if not sections:
            continue
        if area_busy(mods, area):
            out.append({"id": "", "project": "", "area": slug, "ok": False,
                        "note": True,
                        "why": "область занята живой сессией — задание ждёт"})
            continue
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for section in sections:
            try:
                found = store.list_prompts(section)
            except Exception as exc:  # noqa: BLE001
                out.append({"id": "", "project": section, "area": slug,
                            "ok": False, "note": True,
                            "why": "раздел «%s» не прочитан: %s"
                                   % (section, exc)})
                continue
            for record in found:
                # Раздел может совпасть со слагом и с полем `karman` — один и
                # тот же промт не должен попасть в список дважды.
                if record.get("id") in seen:
                    continue
                seen.add(str(record.get("id") or ""))
                records.append(record)
        for record in records:
            if record.get("kind") == KIND_PLAN:
                continue
            if not get_mark(record.get("source", SOURCE_LOCAL),
                            record.get("project", ""), record.get("id", "")):
                continue
            if already_dispatched(record.get("source", SOURCE_LOCAL),
                                  record.get("project", ""),
                                  record.get("id", "")):
                continue
            # План идёт вместе со **своим** промтом (ближайший по времени).
            plan = find_plan(records, record)
            res = dispatch_to_area(mods, area, record, plan, store)
            if res.get("ok"):
                mark_dispatched(record.get("source", SOURCE_LOCAL),
                                record.get("project", ""),
                                record.get("id", ""), slug)
            out.append({"id": record.get("id"),
                        "project": record.get("project"),
                        "area": slug,
                        "ok": bool(res.get("ok")),
                        "why": res.get("why") or res.get("error") or ""})
    return out
