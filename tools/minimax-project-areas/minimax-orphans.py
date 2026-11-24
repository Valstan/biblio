# -*- coding: utf-8 -*-
"""Сторож оборванных субагентов: кто не довёл работу из-за обрыва баланса.

Запрос владельца 17.11: «у тебя много агентов, которые так и не завершили
работу из-за заканчивающегося баланса… Ты в итоге потом их информацию собрал и
закончил их работу, или так это всё пропало безрезультатно? … Проверь такую
возможность, чтобы слово "Продолжай" писать и в сессии субагентов, если они не
завершили работу. Если завершили свою работу — то не писать. Найди возможность
определения состояния их сессии: она была оборвана или корректно завершилась.»

ЧТО ВЫЯСНЕНО (факты, 17.11):
1. CLI **не умеет** открывать сессии субагентов: `mcode exec --session <child>`
   → «Sub-agent Sessions are internal and cannot be opened». Значит «Продолжай»
   в ребёнка доставляется **инструментом рантайма** (`task_append` по
   `task_id`): он поднимает ребёнка **с его же контекстом** (проверено живым
   тестом: ребёнок проснулся и ответил).
2. Состояние ребёнка определяется точно: запись фоновой задачи
   (`local_runtime_background_tasks`, kind=subagent, status failed/canceled/
   aborted) + `metadata.childSessionId` + **последний ход ребёнка**:
   `completed` ⇒ работа закончена (не трогаем), `failed`/`aborted` ⇒ работа
   оборвана (будим).
3. Родительские сессии после обрыва продолжают работу (в turn ingress после
   `failed` идут `completed`) — то есть работа родителя не пропала, пропала
   только незавершённая часть дочерних агентов.

Запуск:  python .claude/scripts/minimax_orphans.py [область]
(без аргумента — по всем областям). Печатает готовые `task_id` для
пробуждения через `task_append` (инструмент рантайма, не CLI).
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Время программы — везде Москва, Россия (UTC+3) (заказ владельца 24.11).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import minigen_time as mtime  # noqa: E402

# Каталог областей: по умолчанию — машина владельца, но путь переопределяется
# переменной окружения (как у minimax-autheal.py), чтобы копия работала и там,
# где мини-менеджер лежит в другом месте.
ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT", r"C:\Users\valstan\.minimax-areas"))
f = lambda ms: mtime.fmt_ms(ms)


def child_state(con, session_id):
    """Состояние сессии ребёнка: ('завершён'|'оборван'|'неизвестно', ход, статус)."""
    if not session_id:
        return "неизвестно", None, None
    row = con.execute(
        "select turn_id, status, accepted_at_ms from local_runtime_turn_ingress "
        "where session_id=? order by accepted_at_ms desc limit 1", (session_id,)).fetchone()
    if not row:
        return "неизвестно", None, None
    if row[1] == "completed":
        return "завершён", row[0], row[1]
    return "оборван", row[0], row[1]


def orphans(area_dir: Path):
    """Прерванные субагенты области: список словарей."""
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.Error:
        return []
    out = []
    try:
        rows = con.execute(
            "select rowid, record_json, status, ended_at_ms from local_runtime_background_tasks "
            "where record_json like '%\"kind\":\"subagent\"%' "
            "and status in ('failed','canceled','aborted') order by ended_at_ms desc").fetchall()
        for rowid, rec, status, ended in rows:
            try:
                o = json.loads(rec)
            except Exception:
                continue
            meta = o.get("metadata") or {}
            child = meta.get("childSessionId")
            state, turn, turn_status = child_state(con, child)
            err = (o.get("lastError") or {}).get("message") or ""
            out.append({
                "task_id": o.get("taskId"),
                "description": o.get("description") or "",
                "child_session": child,
                "state": state,
                "last_turn": turn,
                "last_turn_status": turn_status,
                "ended_at": f(ended),
                "last_error": err,
                "owner_session": o.get("ownerSessionId") or meta.get("parentSessionId"),
            })
    finally:
        con.close()
    return out


def main():
    areas = [a for a in sorted(ROOT.glob("p-*")) if a.is_dir()]
    if len(sys.argv) > 1:
        want = sys.argv[1].replace("p-", "")
        areas = [a for a in areas if a.name == "p-" + want]
    total_broken = 0
    for area in areas:
        items = orphans(area)
        if not items:
            continue
        broken = [i for i in items if i["state"] == "оборван"]
        done = [i for i in items if i["state"] == "завершён"]
        total_broken += len(broken)
        print("=" * 78)
        print("%s: прервано — %d, завершено (не трогаем) — %d" % (area.name, len(broken), len(done)))
        for i in broken:
            print("   БУДИТЬ  task_id=%s  сессия=%s  ход=%s (%s)  «%s»  ошибка: %s" % (
                i["task_id"], (i["child_session"] or "?")[:22], (i["last_turn"] or "?")[:12],
                i["last_turn_status"], i["description"][:60], i["last_error"] or "—"))
        for i in done:
            print("   готово  task_id=%s  «%s»" % (i["task_id"], i["description"][:60]))
    print("=" * 78)
    print("ВСЕГО оборванных (нужно будить):", total_broken)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
