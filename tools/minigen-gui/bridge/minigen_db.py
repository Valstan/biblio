"""Локальная база «МиниГена» — универсальный контейнер данных.

Зачем этот модуль. У программы должна быть **своя база**: простая, локальная,
на машине владельца — и такая, чтобы её можно было выгрузить в один файл,
унести на флешке на другой компьютер и там залить в другую копию «МиниГена»
**без потерь** (заказ владельца 24.11, промт КАРМАНА #92).

Ключевое решение — **структура не привязана к версии программы**:

- данные лежат в **общем контейнере** `records`: вид (`kind`), устойчивый ключ
  (`key`), содержимое (`payload`, JSON), версия записи, метки времени;
- версия программы **не знает** версии базы — она знает, какие `kind` понимает;
- новая возможность программы = новый `kind` (или новое поле в `payload`),
  а **не** новая таблица. Старая копия программы, увидев незнакомый `kind`,
  обязана его не трогать и не потерять при следующей выгрузке;
- потому чужое и будущее не выбрасывается, а кладётся в **«неразобранное»**
  (`unparsed`): запись целиком, с пометкой её вида, — чтобы данные не пропали
  и при следующем обновлении программы разложились по местам.

Формат выгрузки — **JSON** (`.minigen.json`), а не бинарный SQLite: он
переживёт смену версии SQLite, читается глазами и годится для переноса между
машинами. Саму базу можно положить рядом — как справочную копию.

Правила модуля:

- **правда в данных**: запись, которую не поняли, не выбрасывают;
- **конфликт решает оператор**: молчаливых перезаписей нет (см. `plan_import`);
- **путь только локальный**: база лежит рядом с `settings.json`, выгрузка
  уезжает с машины только по явному действию оператора;
- **смена версии программы не ломает базу**: схема хранилища меняется только
  вперёд и только добавлением, а чужая (более новая) версия не понижается.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import minigen_state as state

#: Версия схемы **хранилища**. Это не версия программы: она меняется, только
#: когда меняется сам контейнер (и всегда — вперёд, только добавлением).
SCHEMA_VERSION = 1

#: Формат выгрузки (файла, который уезжает на другой компьютер).
FORMAT = "minigen-db"
FORMAT_VERSION = 1
EXPORT_SUFFIX = ".minigen.json"

#: Вид «неразобранного»: чужое и будущее, чего эта версия программы не знает.
KIND_UNPARSED = "unparsed"

#: Виды данных, которые эта версия программы понимает.
#:
#: Ключ — `kind` в контейнере, значение — что это для человека. Видов, которых
#: здесь нет, **не теряют**: они уезжают в «неразобранное» (см. импорт), а
#: когда программа их заучит — `promote_unparsed` разложит их по местам.
KINDS: dict[str, dict[str, str]] = {
    "prompt": {"title": "Промт",
               "hint": "задание из хранилища промтов"},
    "plan": {"title": "План",
             "hint": "план рядом с промтом"},
    "prompt-mark": {"title": "Пометка промта",
                    "hint": "владелец пометил промт «раздавать автоматически»"},
    "prompt-dispatch": {"title": "Отправка промта",
                        "hint": "промт уже отдан в рабочую область"},
}

#: Решения оператора по конфликту (что взять, когда в базе уже есть запись).
DECISIONS = ("file", "keep", "copy")


# ────────────────────────────────────────────────────────────── мелочи вида

def known_kinds() -> list[str]:
    """Виды, которые эта версия программы понимает."""
    return sorted(KINDS)


def kind_title(kind: str) -> str:
    """Название вида для человека («Промт», «План», …)."""
    item = KINDS.get(str(kind))
    if item:
        return str(item.get("title") or kind)
    return str(kind)


def record_id(kind: str, key: str) -> str:
    """Устойчивый идентификатор записи в отчётах и решениях оператора."""
    return "%s|%s" % (str(kind), str(key))


def now_iso() -> str:
    """Метка времени — ISO-8601 UTC (одинаково на всех машинах)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _program_info() -> dict[str, Any]:
    """Кто выгрузил (программа и её версия) — для честного отчёта."""
    try:
        info = state.program_version()
    except Exception:  # noqa: BLE001 — версия не должна ломать выгрузку
        info = {}
    out: dict[str, Any] = {"name": "МиниГен"}
    if isinstance(info, dict):
        for field in ("commit", "version", "running_from", "installed_at"):
            if info.get(field):
                out[field] = info.get(field)
    return out


# ──────────────────────────────────────────────────────────────── пути

def db_file() -> Path:
    """Файл базы программы (рядом с `settings.json`)."""
    return Path(state.DB_FILE)


def export_dir() -> Path:
    """Папка выгрузок по умолчанию (внутри дома программы)."""
    return Path(state.EXPORT_DIR)


def default_export_path() -> Path:
    """Имя файла выгрузки по умолчанию — с меткой времени."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return export_dir() / ("minigen-%s%s" % (stamp, EXPORT_SUFFIX))


# ────────────────────────────────────────────────────── схема и миграции

def ensure_schema(con: sqlite3.Connection) -> None:
    """Каркас контейнера. Только добавление: чужая база не ломается."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL,"
        " key TEXT NOT NULL,"
        " payload TEXT NOT NULL DEFAULT '{}',"
        " version INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL DEFAULT '',"
        " updated_at TEXT NOT NULL DEFAULT '',"
        " deleted_at TEXT)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS records_kind_key"
                " ON records(kind, key)")
    con.execute("CREATE INDEX IF NOT EXISTS records_kind ON records(kind)")
    con.execute("CREATE INDEX IF NOT EXISTS records_updated_at"
                " ON records(updated_at)")
    con.execute("CREATE TABLE IF NOT EXISTS schema_meta ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.commit()


def schema_version(con: sqlite3.Connection) -> int:
    """Версия схемы хранилища (0 — база только что создана)."""
    row = con.execute("select value from schema_meta where key='schema_version'"
                      ).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def migrate(con: sqlite3.Connection) -> list[str]:
    """Привести схему хранилища к текущей версии.

    ⚠️ Только **вперёд**: база из более новой программы открывается как есть
    (её версия выше нашей ⇒ контейнер она знает лучше нас, и понижать её
    нельзя). Это и есть «смена версии программы не ломает старую базу».
    """
    ensure_schema(con)
    current = schema_version(con)
    applied: list[str] = []
    if current < SCHEMA_VERSION:
        con.execute("insert into schema_meta(key,value) values('schema_version',?)"
                    " on conflict(key) do update set value=excluded.value",
                    (str(SCHEMA_VERSION),))
        con.commit()
        applied.append("схема хранилища %d → %d" % (current, SCHEMA_VERSION))
    return applied


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Открыть базу (создать, если её нет)."""
    target = Path(path or db_file())
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target), timeout=15)
    con.row_factory = sqlite3.Row
    # Уроки Матрицы РМЗ: за базой живут и окно, и демоны ⇒ WAL, терпение к
    # занятой базе и включённые внешние ключи.
    for pragma in ("journal_mode=WAL", "foreign_keys=ON", "busy_timeout=10000",
                   "synchronous=NORMAL"):
        try:
            con.execute("PRAGMA %s" % pragma)
        except sqlite3.Error:
            pass
    ensure_schema(con)
    migrate(con)
    return con


# ────────────────────────────────────────────────────────── записи

def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["payload"]
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        payload = {"_raw": raw}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return {
        "kind": str(row["kind"]),
        "key": str(row["key"]),
        "payload": payload,
        "version": int(row["version"] or 1),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "deleted_at": str(row["deleted_at"]) if row["deleted_at"] else None,
    }


def get_record(con: sqlite3.Connection, kind: str, key: str,
               include_deleted: bool = False) -> dict[str, Any] | None:
    """Запись по виду и ключу (нет — `None`)."""
    row = con.execute("select * from records where kind=? and key=?",
                      (str(kind), str(key))).fetchone()
    if row is None:
        return None
    if row["deleted_at"] and not include_deleted:
        return None
    return _row_to_record(row)


def put_record(con: sqlite3.Connection, kind: str, key: str,
               payload: dict[str, Any], version: int = 1,
               created_at: str | None = None, updated_at: str | None = None,
               deleted_at: str | None = None) -> dict[str, Any]:
    """Положить запись (создать или обновить).

    Ключ записи — устойчивый: та же пара «вид + ключ» перезаписывается, а не
    плодит копии. Удаление — **мягкое** (пометка `deleted_at`), поэтому
    запись видна в выгрузке и её видно в отчёте.
    """
    kind = str(kind or "").strip()
    key = str(key or "").strip()
    if not kind or not key:
        raise ValueError("записи без вида или ключа не бывает")
    body = json.dumps(payload if isinstance(payload, dict) else {"value": payload},
                      ensure_ascii=False, sort_keys=True, default=str)
    now = now_iso()
    old = con.execute("select created_at from records where kind=? and key=?",
                      (kind, key)).fetchone()
    created = (str(old["created_at"]) if old and old["created_at"] else "") \
        or str(created_at or now)
    con.execute(
        "insert into records(kind,key,payload,version,created_at,updated_at,"
        "deleted_at) values(?,?,?,?,?,?,?)"
        " on conflict(kind,key) do update set payload=excluded.payload,"
        " version=excluded.version, updated_at=excluded.updated_at,"
        " deleted_at=excluded.deleted_at",
        (kind, key, body, int(version or 1), created,
         str(updated_at or now), deleted_at))
    con.commit()
    return get_record(con, kind, key, include_deleted=True) or {}


def list_records(con: sqlite3.Connection, kind: str | None = None,
                 include_deleted: bool = False,
                 limit: int | None = None) -> list[dict[str, Any]]:
    """Записи (по виду или все)."""
    sql = "select * from records"
    where: list[str] = []
    params: list[Any] = []
    if kind:
        where.append("kind=?")
        params.append(str(kind))
    if not include_deleted:
        where.append("deleted_at is null")
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by kind, key"
    if limit:
        sql += " limit %d" % int(limit)
    return [_row_to_record(row) for row in con.execute(sql, params).fetchall()]


def delete_record(con: sqlite3.Connection, kind: str, key: str) -> bool:
    """Мягко удалить запись (пометка `deleted_at`). Запись не исчезает."""
    cur = con.execute("update records set deleted_at=? where kind=? and key=?",
                      (now_iso(), str(kind), str(key)))
    con.commit()
    return bool(cur.rowcount)


def counts_by_kind(con: sqlite3.Connection,
                   include_deleted: bool = False) -> dict[str, int]:
    """Сколько записей каждого вида."""
    sql = "select kind, count(*) as n from records"
    if not include_deleted:
        sql += " where deleted_at is null"
    sql += " group by kind order by kind"
    return {str(row["kind"]): int(row["n"])
            for row in con.execute(sql).fetchall()}


def unparsed_records(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """«Неразобранное» — чужое и будущее, что эта версия не знает."""
    out = []
    for rec in list_records(con, kind=KIND_UNPARSED):
        payload = rec.get("payload") or {}
        if payload.get("unparsed"):
            out.append(rec)
    return out


def promote_unparsed(con: sqlite3.Connection) -> int:
    """Переложить «неразобранное», вид которого эта версия уже знает.

    Это и есть «при следующем обновлении программы данные не пропали»:
    новая версия знает новый вид — и разложила чужие записи по местам.
    """
    moved = 0
    for rec in unparsed_records(con):
        payload = rec.get("payload") or {}
        origin = payload.get("origin") or {}
        kind = str(origin.get("kind") or "")
        if kind not in KINDS:
            continue
        item = payload.get("record") or {}
        put_record(con, kind, str(origin.get("key") or rec["key"]),
                   item.get("payload") if isinstance(item.get("payload"), dict)
                   else {"value": item.get("payload")},
                   version=int(item.get("version") or 1),
                   created_at=item.get("created_at"),
                   updated_at=item.get("updated_at"),
                   deleted_at=item.get("deleted_at"))
        con.execute("delete from records where kind=? and key=?",
                    (KIND_UNPARSED, rec["key"]))
        con.commit()
        moved += 1
    return moved


def status(con: sqlite3.Connection | None = None,
           path: Path | None = None) -> dict[str, Any]:
    """Состояние базы — для окна и для отчёта."""
    own = con is None
    target = Path(path or db_file())
    try:
        if own:
            con = connect(target)
        counts = counts_by_kind(con)
        total = sum(counts.values())
        kinds_present = sorted(counts)
        return {
            "ok": True,
            "path": str(target),
            "schema_version": schema_version(con),
            "total": total,
            "counts_by_kind": counts,
            "kinds_present": kinds_present,
            "kinds_known": known_kinds(),
            "kind_titles": {kind: str(item.get("title") or kind)
                            for kind, item in KINDS.items()},
            "kinds_unknown": [k for k in kinds_present if k not in KINDS],
            "unparsed": int(counts.get(KIND_UNPARSED, 0)),
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
        }
    except sqlite3.Error as exc:
        return {"ok": False, "path": str(target),
                "error": "база не открылась: %s" % exc}
    finally:
        if own and con is not None:
            con.close()


# ──────────────────────────────────────────────────────────── выгрузка

def export_db(path: str | Path | None = None,
              db_path: Path | None = None) -> dict[str, Any]:
    """Выгрузить базу в **один файл** — его можно унести на другой компьютер.

    Формат — JSON: переживёт смену версии SQLite и читается глазами. Внутри —
    манифест (версия схемы, сколько записей каждого вида) и сами записи с
    контрольными суммами: файл видно, что он не поехал.
    """
    con = connect(db_path)
    try:
        records = list_records(con, include_deleted=True)
        counts = counts_by_kind(con, include_deleted=True)
        items: list[dict[str, Any]] = []
        checks: dict[str, str] = {}
        for rec in records:
            item = {
                "kind": rec["kind"],
                "key": rec["key"],
                "payload": rec["payload"],
                "version": rec["version"],
                "created_at": rec["created_at"],
                "updated_at": rec["updated_at"],
            }
            if rec["deleted_at"]:
                item["deleted_at"] = rec["deleted_at"]
            items.append(item)
            checks[record_id(rec["kind"], rec["key"])] = _checksum(item)
        body = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "schema_version": schema_version(con),
            "exported_at": now_iso(),
            "program": _program_info(),
            "manifest": {
                "total": len(items),
                "counts_by_kind": counts,
                "checksums": checks,
                "checksum": _checksum(items),
            },
            "records": items,
        }
        target = Path(path) if path else default_export_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(str(tmp), str(target))
    finally:
        con.close()
    return {
        "ok": True,
        "path": str(target),
        "total": len(items),
        "counts_by_kind": counts,
        "checksum": body["manifest"]["checksum"],
        "exported_at": body["exported_at"],
        "schema_version": body["schema_version"],
    }


def read_export(path: str | Path) -> dict[str, Any]:
    """Прочитать файл выгрузки и проверить его (формат, версия, суммы)."""
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "path": str(target),
                "error": "файла нет: %s" % target}
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "path": str(target),
                "error": "файл не читается: %s" % exc}
    try:
        body = json.loads(raw)
    except ValueError as exc:
        return {"ok": False, "path": str(target),
                "error": "файл не разобран — это не выгрузка базы: %s" % exc}
    if not isinstance(body, dict):
        return {"ok": False, "path": str(target),
                "error": "в файле не выгрузка базы"}
    fmt = str(body.get("format") or "")
    if fmt != FORMAT:
        return {"ok": False, "path": str(target),
                "error": "это не выгрузка базы «МиниГена» (формат: %s)"
                         % (fmt or "—")}
    try:
        file_version = int(body.get("format_version") or 0)
    except (TypeError, ValueError):
        file_version = 0
    if file_version > FORMAT_VERSION:
        return {"ok": False, "path": str(target),
                "error": "файл выгружен более новой программой (формат %d) — "
                         "эта версия его не поймёт" % file_version}
    records = body.get("records")
    if not isinstance(records, list):
        return {"ok": False, "path": str(target),
                "error": "в файле нет списка записей"}
    checks = ((body.get("manifest") or {}).get("checksums") or {})
    broken: list[str] = []
    for item in records:
        if not isinstance(item, dict):
            broken.append("?")
            continue
        ident = record_id(item.get("kind"), item.get("key"))
        want = str(checks.get(ident) or "")
        if want and want != _checksum(item):
            broken.append(ident)
    return {"ok": True, "path": str(target), "body": body, "broken": broken}


# ────────────────────────────────────────────────────────────── импорт

def _target(kind: str, key: str) -> tuple[str, str]:
    """Куда кладём запись: известный вид — как есть, чужой — в «неразобранное»."""
    if str(kind) in KINDS:
        return str(kind), str(key)
    return KIND_UNPARSED, "%s|%s" % (str(kind), str(key))


def _unparsed_payload(kind: str, key: str, item: dict[str, Any]) -> dict[str, Any]:
    """Чужой вид — целиком в «неразобранное», с пометкой, откуда он."""
    return {
        "unparsed": True,
        "origin": {
            "kind": str(kind),
            "key": str(key),
            "version": int(item.get("version") or 1),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "source": "import",
        },
        "record": item,
        "imported_at": now_iso(),
    }


def _same(existing: dict[str, Any], item: dict[str, Any],
          kind: str) -> bool:
    """Такая же запись (переносить незачем)?"""
    if str(kind) not in KINDS:
        payload = existing.get("payload") or {}
        return payload.get("record") == item
    left = existing.get("payload")
    right = item.get("payload")
    if not isinstance(right, dict):
        right = {"value": right}
    return left == right and int(existing.get("version") or 1) == int(
        item.get("version") or 1)


def _decision_for(decisions: dict[str, Any], kind: str, key: str) -> str:
    """Решение оператора по записи: точечное, по виду или общее."""
    for ident in (record_id(kind, key), "kind:%s" % kind, "default"):
        value = str(decisions.get(ident) or "").strip().lower()
        if value in DECISIONS:
            return value
        if value == "skip":
            return "keep"
    return ""


def plan_import(path: str | Path,
                db_path: Path | None = None) -> dict[str, Any]:
    """Разбор файла выгрузки: что переносится, что конфликтует, о чём спросить.

    Ничего не меняет — только план и вопросы оператору. Это и есть «помощник
    импорта»: он спрашивает, **куда и как** переносить спорные данные.
    """
    read = read_export(path)
    if not read.get("ok"):
        return read
    body = read["body"]
    manifest = body.get("manifest") or {}
    file_schema = 0
    try:
        file_schema = int(body.get("schema_version") or 0)
    except (TypeError, ValueError):
        file_schema = 0
    con = connect(db_path)
    try:
        actions: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        unknown: list[str] = []
        for item in body.get("records") or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            key = str(item.get("key") or "")
            known = kind in KINDS
            if not known:
                unknown.append(kind)
            t_kind, t_key = _target(kind, key)
            existing = get_record(con, t_kind, t_key, include_deleted=True)
            action = {"id": record_id(kind, key), "kind": kind, "key": key,
                      "known": known, "target": record_id(t_kind, t_key)}
            if existing is None:
                action["action"] = "insert"
            elif _same(existing, item, kind):
                action["action"] = "same"
            else:
                action["action"] = "conflict"
                conflicts.append({
                    "id": record_id(kind, key),
                    "kind": kind,
                    "key": key,
                    "title": kind_title(kind),
                    "known": known,
                    "theirs": item,
                    "ours": existing,
                    "question": "Запись «%s / %s» уже есть в вашей базе и отличается "
                                "от файла. Что взять: из файла или оставить своё?"
                                % (kind_title(kind), key),
                })
            actions.append(action)
    finally:
        con.close()
    questions = [item["question"] for item in conflicts]
    for kind in sorted(set(unknown)):
        questions.append(
            "В файле есть вид данных «%s», которого эта версия «МиниГена» не "
            "знает. Он сохранится как есть (в неразобранном), чтобы данные не "
            "пропали. Оставить так?" % kind)
    counts = {"insert": 0, "same": 0, "conflict": 0}
    for action in actions:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    return {
        "ok": True,
        "path": str(read["path"]),
        "file": {
            "format": FORMAT,
            "format_version": int(body.get("format_version") or 0),
            "schema_version": file_schema,
            "exported_at": str(body.get("exported_at") or ""),
            "program": body.get("program") or {},
            "total": len(body.get("records") or []),
            "counts_by_kind": manifest.get("counts_by_kind") or {},
        },
        "version": {
            "ours": SCHEMA_VERSION,
            "theirs": file_schema,
            "same": file_schema == SCHEMA_VERSION,
            "note": ("схема совпадает" if file_schema == SCHEMA_VERSION else
                     "в файле схема %d, у нас %d — переносим как есть"
                     % (file_schema, SCHEMA_VERSION)),
        },
        "known_kinds": known_kinds(),
        "unknown_kinds": sorted(set(unknown)),
        "counts": counts,
        "actions": actions,
        "conflicts": conflicts,
        "questions": questions,
        "checksums_ok": not read.get("broken"),
        "broken": read.get("broken") or [],
        "_body": body,
    }


def _store(con: sqlite3.Connection, kind: str, key: str, item: dict[str, Any]) -> None:
    """Положить запись из файла в базу (видknown — как есть, чужой — в «неразобранное»)."""
    t_kind, t_key = _target(kind, key)
    payload = item.get("payload")
    if not isinstance(payload, dict):
        payload = {"value": payload}
    if t_kind == KIND_UNPARSED:
        payload = _unparsed_payload(kind, key, item)
    put_record(con, t_kind, t_key, payload,
               version=int(item.get("version") or 1),
               created_at=item.get("created_at"),
               updated_at=item.get("updated_at"),
               deleted_at=item.get("deleted_at"))


def apply_import(path: str | Path,
                 decisions: dict[str, Any] | None = None,
                 db_path: Path | None = None) -> dict[str, Any]:
    """Залить данные из файла в базу.

    ⚠️ Конфликт без решения оператора **не трогается**: молчаливых
    перезаписей нет (правило цикла «сомнение — спросить»). Решение оператора:
    `file` — взять из файла, `keep` — оставить своё, `copy` — положить рядом
    отдельной записью.
    """
    plan = plan_import(path, db_path)
    if not plan.get("ok"):
        return plan
    body = plan.get("_body") or {}
    by_id = {record_id(str(item.get("kind") or ""), str(item.get("key") or "")): item
             for item in (body.get("records") or []) if isinstance(item, dict)}
    choices = decisions if isinstance(decisions, dict) else {}
    report: dict[str, Any] = {
        "ok": True,
        "path": str(plan.get("path") or ""),
        "inserted": 0,
        "same": 0,
        "overwritten": 0,
        "kept": 0,
        "copied": 0,
        "unparsed": 0,
        "unresolved": [],
        "questions": list(plan.get("questions") or []),
        "version": plan.get("version") or {},
        "unknown_kinds": list(plan.get("unknown_kinds") or []),
        "checksums_ok": bool(plan.get("checksums_ok")),
    }
    con = connect(db_path)
    try:
        for action in plan.get("actions") or []:
            kind = str(action.get("kind") or "")
            key = str(action.get("key") or "")
            item = by_id.get(str(action.get("id"))) or {}
            what = str(action.get("action") or "")
            if what == "same":
                report["same"] += 1
                continue
            if what == "insert":
                _store(con, kind, key, item)
                if kind in KINDS:
                    report["inserted"] += 1
                else:
                    report["unparsed"] += 1
                continue
            # конфликт — решает оператор
            decision = _decision_for(choices, kind, key)
            if decision == "file":
                _store(con, kind, key, item)
                report["overwritten"] += 1
            elif decision == "copy":
                _store(con, kind, "%s-импорт-%d" % (key, report["copied"] + 1),
                       item)
                report["copied"] += 1
            elif decision == "keep":
                report["kept"] += 1
            else:
                report["unresolved"].append(action.get("id"))
    finally:
        con.close()
    report["total"] = (report["inserted"] + report["same"] + report["overwritten"]
                       + report["kept"] + report["copied"] + report["unparsed"])
    report["why"] = _report_text(report)
    return report


def _report_text(report: dict[str, Any]) -> str:
    """Отчёт одной строкой — для окна и для чата."""
    parts: list[str] = []
    if report.get("inserted"):
        parts.append("перенесено новых: %d" % report["inserted"])
    if report.get("unparsed"):
        parts.append("сохранено как неразобранное: %d" % report["unparsed"])
    if report.get("same"):
        parts.append("совпало с базой: %d" % report["same"])
    if report.get("overwritten"):
        parts.append("заменено по решению оператора: %d" % report["overwritten"])
    if report.get("copied"):
        parts.append("положено рядом отдельной записью: %d" % report["copied"])
    if report.get("kept"):
        parts.append("оставлено своё: %d" % report["kept"])
    if report.get("unresolved"):
        parts.append("не тронуто (ждёт решения оператора): %d"
                     % len(report["unresolved"]))
    if not parts:
        return "переносить нечего"
    return "; ".join(parts)
