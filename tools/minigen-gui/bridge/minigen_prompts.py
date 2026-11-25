"""«Хранилище промтов» «МиниГена»: где лежат промты и планы.

Заказ владельца (24.11, промт #94 в разделе `MiniGen` библиотеки КАРМАНА):
«встроить в МиниГен структуру Хранилище промтов, причём выбор должен быть, где
промты хранятся: в базе МиниГена или на внешнем ресурсе, доступ на который можно
настроить… в моём случае это сайт Карман со своим хранилищем промтов, которым
пользуются мои проекты… чтобы отсылать рабочим областям проектов эти промты
прямо из хранилища автоматически, если есть такое задание на самостоятельное
автоматическое программирование без участия живого оператора».

Два провайдера хранилища:

- `LocalPromptStore` — **в базе «МиниГена»** (SQLite, `%LOCALAPPDATA%\\
  minigen-gui\\prompts.db`): работает всегда и без сети;
- `KarmanPromptStore` — **внешний ресурс** (Карман со своим хранилищем):
  читает разделы секретом канала (тот приходит в программу сам из
  vault-комнаты), архивирует своим токеном раздела (`PROMPTS_TOKEN_<Раздел>`,
  он тоже лежит в vault-комнате, если выдан грант).

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

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import minigen_karman as karman

#: База «МиниГена» с промтами и служебными отметками.
GUI_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
LOCAL_DB = GUI_HOME / "prompts.db"

SOURCE_LOCAL = "local"
SOURCE_KARMAN = "karman"
KIND_PROMPT = "prompt"
KIND_PLAN = "plan"

#: Сколько символов промта уходит в область (текст задания).
MAX_TASK_TEXT = 20_000
#: Кэш чтения внешнего ресурса (секунды): окно спрашивает хранилище каждые
#: 5 секунд, разделов двадцать — без кэша это двадцать запросов за кадр.
CACHE_TTL = 60.0
#: Кэш внешнего ресурса в памяти процесса (значения секретов здесь нет).
_cache: dict[str, Any] = {"at": 0.0, "records": [], "sections": [],
                           "sections_at": 0.0}


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_stamp(value: Any) -> str:
    """Метка времени — строкой ISO (так же, как у внешнего ресурса)."""
    try:
        ms = int(value or 0)
    except (TypeError, ValueError):
        return str(value or "")
    if ms <= 0:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


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

def ensure_schema(con: sqlite3.Connection) -> None:
    """Таблицы хранилища: записи, отметки владельца, журнал раздачи."""
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


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Открыть базу хранилища (схема создаётся при первом обращении)."""
    target = Path(db_path or LOCAL_DB)
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    ensure_schema(con)
    return con


# ─────────────────────────────────────────────── отметки владельца (marks)

def get_mark(source: str, project: str, prompt_id: str,
             db_path: Path | None = None) -> bool:
    """Помечен ли промт владельцем как «раздавать автоматически»."""
    con = connect(db_path)
    try:
        row = con.execute(
            "select auto from marks where source=? and project=? and id=?",
            (source, project, prompt_id)).fetchone()
        return bool(row and row["auto"])
    finally:
        con.close()


def set_mark(source: str, project: str, prompt_id: str, auto: bool,
             db_path: Path | None = None) -> dict[str, Any]:
    """Пометить (или снять пометку) — только владелец может."""
    con = connect(db_path)
    try:
        con.execute(
            "insert into marks (source, project, id, auto, at) values (?,?,?,?,?)"
            " on conflict(source, project, id) do update set auto=excluded.auto,"
            " at=excluded.at",
            (source, project, prompt_id, 1 if auto else 0, now_ms()))
        con.commit()
        return {"ok": True, "auto": bool(auto)}
    finally:
        con.close()


def already_dispatched(source: str, project: str, prompt_id: str,
                       db_path: Path | None = None) -> bool:
    """Раздавали ли этот промт уже — чтобы не раздать дважды."""
    con = connect(db_path)
    try:
        row = con.execute(
            "select 1 from dispatched where source=? and project=? and id=?",
            (source, project, prompt_id)).fetchone()
        return bool(row)
    finally:
        con.close()


def mark_dispatched(source: str, project: str, prompt_id: str, area: str,
                     db_path: Path | None = None) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "insert into dispatched (source, project, id, area, at) values (?,?,?,?,?)"
            " on conflict(source, project, id) do update set area=excluded.area,"
            " at=excluded.at",
            (source, project, prompt_id, area or "", now_ms()))
        con.commit()
    finally:
        con.close()


# ───────────────────────────────────────────────── локальное хранилище

class LocalPromptStore:
    """Промты в базе «МиниГена» — работает всегда и без сети."""

    kind = SOURCE_LOCAL
    label = "в базе «МиниГена»"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or LOCAL_DB)

    def location(self) -> str:
        return str(self.db_path)

    def sections(self) -> list[str]:
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "select distinct project from records order by project").fetchall()
            return [str(row["project"]) for row in rows if row["project"]]
        finally:
            con.close()

    def list_prompts(self, project: str = "", include_archived: bool = False
                     ) -> list[dict[str, Any]]:
        con = connect(self.db_path)
        try:
            sql = ("select id, project, title, body, kind, archived, created_at"
                   " from records")
            params: list[Any] = []
            if project:
                # ⚠️ Имя раздела в библиотеке каноническое (`MatricaRMZ`), а
                # область называется слагом (`matricarmz`) ⇒ сравнение без
                # учёта регистра, иначе раздел не найдётся никогда.
                sql += " where project=? collate nocase"
                params.append(project)
            if not include_archived:
                sql += " and archived=0" if project else " where archived=0"
            sql += " order by created_at desc, id desc"
            rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
        out = []
        for row in rows:
            out.append(normalize_record({
                "id": row["id"], "project": row["project"], "title": row["title"],
                "body": row["body"], "kind": row["kind"],
                "archived": bool(row["archived"]),
                "createdAt": iso_stamp(row["created_at"]),
            }, SOURCE_LOCAL))
        return out

    def save(self, project: str, title: str, body: str) -> dict[str, Any]:
        name = (project or "").strip()
        if not name:
            return {"ok": False, "error": "без раздела промт некуда положить"}
        if not (body or "").strip():
            return {"ok": False, "error": "промт пуст"}
        prompt_id = uuid.uuid4().hex[:8]
        con = connect(self.db_path)
        try:
            con.execute(
                "insert into records (id, project, title, body, kind, archived,"
                " created_at) values (?,?,?,?,?,0,?)",
                (prompt_id, name, (title or "").strip()[:200], body,
                 detect_kind(title or "", body), now_ms()))
            con.commit()
        finally:
            con.close()
        return {"ok": True, "id": prompt_id, "project": name}

    def archive(self, prompt_id: str, project: str = "") -> dict[str, Any]:
        con = connect(self.db_path)
        try:
            cur = con.execute("update records set archived=1 where id=?",
                               (prompt_id,))
            con.commit()
            if not cur.rowcount:
                return {"ok": False, "error": "такого промта в базе нет"}
        finally:
            con.close()
        return {"ok": True, "archived": True}

    def status(self) -> dict[str, Any]:
        try:
            con = connect(self.db_path)
        except sqlite3.Error as exc:
            return {"ok": False, "reason": "база не открылась: %s" % exc}
        try:
            row = con.execute("select count(*) as n from records"
                              " where archived=0").fetchone()
            count = int(row["n"]) if row else 0
        finally:
            con.close()
        return {"ok": True, "reason": "в базе «МиниГена» живых промтов: %d" % count,
                "sections": self.sections()}


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

    def sections(self) -> list[str]:
        code, names = karman.list_sections()
        if code != 200:
            return []
        _cache.update({"sections": list(names), "sections_at": time.time()})
        return list(names)

    def _read_section(self, project: str) -> list[dict[str, Any]]:
        """Живые промты одного раздела (канал читает раздел по имени)."""
        code, payload = karman.call("GET", "/api/prompts?project=%s" % project)
        if code != 200:
            raise KarmanUnavailable(karman.explain(code))
        out = []
        for item in payload.get("prompts") or []:
            if not isinstance(item, dict):
                continue
            archived = bool(item.get("archivedAt"))
            out.append(normalize_record({
                "id": item.get("id"),
                "project": item.get("project") or project,
                "title": item.get("title"),
                "body": item.get("body"),
                "createdAt": item.get("createdAt"),
                "archived": archived,
            }, SOURCE_KARMAN))
        return out

    def list_prompts(self, project: str = "", include_archived: bool = False
                     ) -> list[dict[str, Any]]:
        """Промты раздела (или всех разделов — с кэшем на минуту).

        ⚠️ Окно спрашивает хранилище каждые 5 секунд, а разделов двадцать:
        без кэша это двадцать запросов к сервису за кадр. Кэш на минуту —
        компромисс: промт появится с задержкой до минуты, зато сервис не
        засыпается запросами.
        """
        if project:
            items = self._read_section(project)
        else:
            now = time.time()
            if now - float(_cache.get("at") or 0) < CACHE_TTL:
                items = list(_cache.get("records") or [])
            else:
                items = []
                for section in self.sections():
                    try:
                        items.extend(self._read_section(section))
                    except KarmanUnavailable:
                        continue
                _cache.update({"at": now, "records": list(items)})
        if include_archived:
            return items
        return [item for item in items if not item.get("archived")]

    def save(self, project: str, title: str, body: str) -> dict[str, Any]:
        name = (project or "").strip()
        if not name:
            return {"ok": False, "error": "без раздела промт некуда положить"}
        code, payload = karman.publish(name, (title or "").strip()[:200], body)
        if code not in (200, 201):
            return {"ok": False,
                    "error": "промт не записан: %s" % karman.explain(code)}
        return {"ok": True, "id": str(payload.get("id") or ""), "project": name}

    def archive(self, prompt_id: str, project: str = "") -> dict[str, Any]:
        """Архивировать в разделе — токеном раздела."""
        name = (project or "").strip()
        if not name:
            return {"ok": False, "error": "не указан раздел"}
        token, source = karman.vault_secret(section_secret_name(name))
        if not token:
            return {"ok": False,
                    "error": "токена раздела нет (%s) — архивировать нечем;"
                             " попроси грант в комнату программы"
                             % section_secret_name(name)}
        try:
            pid = int(prompt_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "плохой id промта"}
        code, payload = karman.call("POST", "/api/prompts/section",
                                    {"project": name, "id": pid, "archived": True},
                                    token=token)
        if code != 200:
            return {"ok": False,
                    "error": "раздел не принял: %s" % karman.explain(code)}
        return {"ok": True, "archived": True, "token_source": source}

    def status(self) -> dict[str, Any]:
        secret, source = karman.read_secret()
        base, base_source = karman.read_base()
        if not secret or not base:
            return {"ok": False,
                    "reason": "секрет канала не задан — внешний ресурс недоступен",
                    "sections": []}
        code, names = karman.list_sections()
        if code != 200:
            return {"ok": False, "reason": karman.explain(code), "sections": []}
        return {"ok": True, "sections": list(names),
                "reason": "канал живой, разделов: %d (секрет из %s)"
                          % (len(names), source or "—")}


class KarmanUnavailable(RuntimeError):
    """Внешний ресурс не отдал промты — честное сообщение, а не тишина."""


def section_secret_name(section: str) -> str:
    """Имя секрета раздела: `PROMPTS_TOKEN_<Раздел>` (канон КАРМАНА)."""
    return "PROMPTS_TOKEN_%s" % (section or "").strip().upper()


def store_for(settings: dict[str, Any]) -> LocalPromptStore | KarmanPromptStore:
    """Провайдер хранилища по настройке окна."""
    kind = str((settings or {}).get("promptStore") or SOURCE_LOCAL).strip().lower()
    if kind == SOURCE_KARMAN:
        return KarmanPromptStore()
    return LocalPromptStore()


# ──────────────────────────────────────────────── раздача в рабочие области

def area_sections(area: dict[str, Any]) -> list[str]:
    """Разделы, которые подходят области: её слаг и её раздел в КАРМАНЕ.

    Слаг — это имя проекта (`matricarmz`), а раздел в библиотеке промтов —
    каноническое имя (`MatricaRMZ`). Сравнение — без учёта регистра.
    """
    out: list[str] = []
    for name in (str(area.get("slug") or ""), str(area.get("karman") or "")):
        clean = name.strip()
        if clean and clean.lower() not in [item.lower() for item in out]:
            out.append(clean)
    return out


def section_matches(section: str, area: dict[str, Any]) -> bool:
    """Раздел промта = проект области? (иначе задание уедет не туда)."""
    want = (section or "").strip().lower()
    if not want:
        return False
    return want in [item.lower() for item in area_sections(area)]


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
    """План рядом с промтом (тот же раздел, вид «план»)."""
    if record.get("kind") == KIND_PLAN:
        return None
    project = (record.get("project") or "").strip()
    for item in records:
        if item.get("kind") != KIND_PLAN:
            continue
        if (item.get("project") or "").strip() != project:
            continue
        if item.get("id") == record.get("id"):
            continue
        return item
    return None


def dispatch_to_area(mods: dict[str, Any], area: dict[str, Any],
                     record: dict[str, Any], plan: dict[str, Any] | None = None
                     ) -> dict[str, Any]:
    """Отдать промт в область: открыть там сессию с текстом задания.

    Механизм — тот же, что у Управляющего (`minimax-sessionwatch.py --new`):
    отдельный механизм не изобретаем.
    """
    watcher = mods.get("sessionwatch")
    if watcher is None:
        return {"ok": False, "error": "сторож сессий недоступен"}
    if not section_matches(str(record.get("project") or ""), area):
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
    следующего прохода.
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
        sections = area_sections(area)
        if not sections:
            continue
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for section in sections:
            try:
                found = store.list_prompts(section)
            except Exception:  # noqa: BLE001
                continue
            for record in found:
                # Раздел может совпасть со слагом и с полем `karman` — один и
                # тот же промт не должен попасть в список дважды.
                if record.get("id") in seen:
                    continue
                seen.add(str(record.get("id") or ""))
                records.append(record)
        plan = None
        for record in records:
            if record.get("kind") == KIND_PLAN:
                plan = find_plan(records, record)
                break
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
            res = dispatch_to_area(mods, area, record, plan)
            if res.get("ok"):
                mark_dispatched(record.get("source", SOURCE_LOCAL),
                                record.get("project", ""),
                                record.get("id", ""), area.get("slug") or "")
            out.append({"id": record.get("id"),
                        "project": record.get("project"),
                        "area": area.get("slug"),
                        "ok": bool(res.get("ok")),
                        "why": res.get("why") or res.get("error") or ""})
    return out
