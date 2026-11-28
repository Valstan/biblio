"""Свои критические ошибки «МиниГена» — задача встроенному «Управляющему».

Заказ владельца (промт КАРМАНА #185, 28.11): программа должна ловить **свои**
критические ошибки и отдавать их «Управляющему» как задачу — починить код
программы и выпустить обновление. При этом:

- **источник назван прямо**: «ошибка программы МиниГен» — значит чинить код и
  обновлять программу; «вывод сессии другого агента» — значит отвечать агенту
  или управлять им (это **не** задача на правку кода);
- **одна и та же ошибка уходит не чаще раза в полчаса**: «Управляющий» её
  чинит — а двадцать одинаковых сбоев подряд не должны превратиться в
  двадцать задач; **другая** ошибка (другой отпечаток) уходит сразу;
- **ошибка помечена «в работе»**: по отпечатку видно, когда она последний раз
  уходила и когда окно откроется снова.

**Что здесь и почему так**

- **отпечаток ошибки** (`fingerprint`) — не дословный текст, а его нормализованная
  тень: регистр, пробелы и **меняющиеся числа** («попытка 3», «через 42 с»)
  сглаживаются. Иначе одна и та же ошибка в двадцати вариантах прошла бы как
  двадцать разных, а повтор ушёл бы Управляющему двадцать раз (риск петли из
  промта #185);
- **окно — на конкретную ошибку**, а не на всё: полчаса держит **один и тот же**
  дефект; другая ошибка уходит сразу (это прямо предложение владельца из
  промта: «Предлагаю на конкретную ошибку (по отпечатку)»);
- **состояние — на диске**, рядом с «ящиком отчётов» и пометкой «до какого
  отчёта нейросеть в курсе»: перезапуск окна не должен забыть, что ошибка
  уже уходила (иначе после перезапуска она ушла бы снова);
- **порог «критической» ошибки** (промт #185, п. 6): в задачу попадает **только**
  вид `crash` — сбои сети, отказ вендора и чужие машины остаются обычными
  отчётами и задачами не становятся.

⚠️ Словарь видов и источников — **один на программу**: он живёт в ядре
(`minigen_core.KIND_CRASH` и т. д.), а здесь продублирован для моста — приёмка
`test_program_errors_185.py` проверяет, что словари совпадают.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

#: Куда лежит состояние ошибок (рядом с пометкой «до какого отчёта нейросеть
#: в курсе» — оба файла принадлежат каналу «Управляющий»).
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
STATE_FILE = LOCAL / "errors-state.json"

#: Окно, в течение которого одна и та же ошибка уходит Управляющему **один**
#: раз (промт #185, п. 3: «полчаса»).
WINDOW_MS = 30 * 60 * 1000

#: Предел длины текста ошибки в задаче: большой стектрейс съедает контекст
#: «Управляющего» (риск из промта #185).
TEXT_LIMIT = 1200

#: Словарь видов и источников (см. `minigen_core` — приёмка проверяет равенство).
KIND_CRASH = "crash"
#: ⚠️ Промт КАРМАНА #186: вид «работа встроенной нейросети программы» —
#: решение «Управляющего» (встроенной нейросети «МиниГена»). Своим видом:
#: иначе в «ящике отчётов» не сказано, что говорит нейросеть.
KIND_MODEL = "model"
SOURCE_PROGRAM = "minigen"
SOURCE_AGENT = "agent"

#: Заголовок задачи — по нему задача отличима от ленты отчётов.
TASK_MARK = "ЗАДАЧА УПРАВЛЯЮЩЕМУ"


def time_ms() -> int:
    """Текущее время в миллисекундах."""
    return int(time.time() * 1000)


def is_crash(record: Any) -> bool:
    """Отчёт — критический сбой программы (вид `crash` и уровень `error`)?"""
    if not isinstance(record, dict):
        return False
    return (str(record.get("kind") or "") == KIND_CRASH
            and str(record.get("level") or "") == "error")


def report_crash(core: Any, text: str, area: str = "",
                 source: str = SOURCE_PROGRAM) -> None:
    """Отчёт о сбое программы — в «ящик отчётов» (через ядро).

    ⚠️ Ядро на машине может быть **старше** программы (раскатка ещё не
    прошла) — тогда `report_crash` в нём ещё нет, и отчёт пишется обычным
    `report` с видом `crash`. Отчёт — дополнительный канал: его поломка не
    должна ломать саму работу.
    """
    try:
        writer = getattr(core, "report_crash", None)
        if callable(writer):
            writer(text, area=area, source=source)
            return
        core.report("error", KIND_CRASH, text, area=area)
    except Exception:  # noqa: BLE001
        pass


def source_of(record: Any) -> str:
    """Источник сообщения: сбой программы или вывод сессии агента."""
    if not isinstance(record, dict):
        return SOURCE_PROGRAM
    return str(record.get("source") or "") or SOURCE_PROGRAM


def fingerprint(record: Any) -> str:
    """Отпечаток ошибки: тот же дефект — тот же отпечаток.

    ⚠️ Нормализация (промт #185, «что считать той же ошибкой» — решение
    владельца нужно подтвердить): регистр и пробелы сглаживаются, **числа
    заменяются на «#»** — иначе «нет ответа, попытка 3» и «нет ответа,
    попытка 4» считались бы разными ошибками и повтор ушёл бы Управляющему
    дважды (а он в это время чинит тот же самый дефект).
    """
    if not isinstance(record, dict):
        record = {"text": str(record or "")}
    text = str(record.get("text") or "")
    norm = re.sub(r"\d+", "#", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()[:400]
    raw = "|".join([str(record.get("kind") or ""),
                    source_of(record),
                    str(record.get("area") or ""),
                    norm])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        pass


def claim(record: Any, path: Path | None = None,
          window_ms: int | None = None) -> dict[str, Any]:
    """Можно ли отдать эту ошибку Управляющему **сейчас**?

    Возвращает:

    - `allowed` — True: ошибка уходит как задача (окно прошло или ошибка новая);
    - `repeat` — True: та же ошибка, но окно **не прошло** — повтор не отправляем;
    - `show` — True: про повтор надо сказать вслух (иначе «в работе» не видно);
    - `fingerprint`, `state`, `record` — отпечаток, состояние и сама запись.

    ⚠️ `window_ms` — по умолчанию `WINDOW_MS` (полчаса). Значение читается
    **в момент вызова** (не как аргумент по умолчанию): иначе приёмка не смогла
    бы проверить «окно открывается снова» без ожидания полчаса.
    """
    window = int(WINDOW_MS if window_ms is None else window_ms)
    state_path = Path(path or STATE_FILE)
    fp = fingerprint(record)
    data = _read(state_path)
    rec = dict(data.get(fp) or {})
    now = int(time_ms())
    last = int(rec.get("last_sent_ms") or 0)
    ts = int((record or {}).get("ts") or 0) if isinstance(record, dict) else 0
    rec["fingerprint"] = fp
    rec["source"] = source_of(record)
    rec["area"] = str((record or {}).get("area") or "") if isinstance(record, dict) else ""
    rec["text"] = str((record or {}).get("text") or "") if isinstance(record, dict) else ""
    rec["seen_ms"] = now
    rec["seen_count"] = int(rec.get("seen_count") or 0) + 1
    if last and ts and ts <= last:
        # Эта запись уже уходила — второй раз её не отправляем (никогда).
        # Файл состояния **не** трогаем: иначе каждый ход чата переписывал бы
        # его по кругу (в ящике отчётов старых сбоев может быть много).
        return {"allowed": False, "repeat": False, "show": False,
                "fingerprint": fp, "state": rec, "record": record}
    if last and (now - last) < window:
        # Окно не прошло: та же ошибка уже в работе — повтор **не отправляем**.
        rec["in_work"] = True
        rec["unblock_ms"] = last + window
        notified = int(rec.get("repeat_notified_ms") or 0)
        show = (not notified) or (now - notified) >= window
        if show:
            rec["repeat_notified_ms"] = now
        data[fp] = rec
        _write(state_path, data)
        return {"allowed": False, "repeat": True, "show": show,
                "fingerprint": fp, "state": rec, "record": record}
    # Окно прошло (или ошибка новая) — ошибка уходит как задача.
    rec["first_ms"] = int(rec.get("first_ms") or now)
    rec["last_sent_ms"] = now
    rec["sent_count"] = int(rec.get("sent_count") or 0) + 1
    rec["in_work"] = True
    rec["unblock_ms"] = now + window
    rec["window_ms"] = window
    data[fp] = rec
    _write(state_path, data)
    return {"allowed": True, "repeat": False, "show": True,
            "fingerprint": fp, "state": rec, "record": record}


def view(path: Path | None = None) -> list[dict[str, Any]]:
    """Состояние ошибок: по отпечатку — когда уходила, в работе ли, когда окно.

    Это то, что видно «по отпечатку» (промт #185, п. 4): и в задаче, и в
    инструменте `errors.state`.
    """
    state_path = Path(path or STATE_FILE)
    data = _read(state_path)
    now = int(time_ms())
    out: list[dict[str, Any]] = []
    for fp, rec in data.items():
        if not isinstance(rec, dict):
            continue
        last = int(rec.get("last_sent_ms") or 0)
        window = int(rec.get("window_ms") or WINDOW_MS)
        out.append({
            "fingerprint": fp,
            "source": str(rec.get("source") or ""),
            "area": str(rec.get("area") or ""),
            "text": str(rec.get("text") or "")[:400],
            "last_sent_ms": last,
            "unblock_ms": (last + window) if last else 0,
            "in_work": bool(last) and (now - last) < window,
            "sent_count": int(rec.get("sent_count") or 0),
            "seen_count": int(rec.get("seen_count") or 0),
        })
    out.sort(key=lambda item: int(item.get("last_sent_ms") or 0), reverse=True)
    return out


def _clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "… (дальше обрезано)"


def render(tasks: list[dict[str, Any]], blocked: list[dict[str, Any]],
           fmt: Callable[[int], str]) -> str:
    """Текст задач — в ход чата «Управляющего».

    `fmt` — печать времени по Москве (у ядра `fmt_ms`).

    ⚠️ Задача и лента отчётов — **разные вещи**: здесь каждая ошибка названа
    своим источником и своим действием (починить код / ответить агенту).
    """
    lines: list[str] = []
    for item in tasks or []:
        record = item.get("record") or {}
        state = item.get("state") or {}
        source = source_of(record)
        area = str(record.get("area") or "") or "—"
        last = int(state.get("last_sent_ms") or 0)
        unblock = int(state.get("unblock_ms") or 0)
        if source == SOURCE_AGENT:
            lines.append("%s (вывод сессии агента): ответить агенту или управлять им."
                         % TASK_MARK)
            lines.append("Источник: вывод сессии другого агента — это НЕ ошибка "
                         "программы, код чинить не надо.")
            lines.append("Отпечаток: %s — в работе с %s, окно откроется в %s."
                         % (item.get("fingerprint") or "—",
                            fmt(last) if last else "—",
                            fmt(unblock) if unblock else "—"))
            lines.append("Сообщение: %s" % _clip(str(record.get("text") or "")))
            lines.append("Область: %s" % area)
            lines.append("Что делать: sessions.read по области %s — посмотреть вывод "
                         "сессии; ответить агенту или взять нитку под управление."
                         % area)
        else:
            lines.append("%s (ошибка программы «МиниГен»): починить код и выпустить "
                         "обновление." % TASK_MARK)
            lines.append("Источник: ошибка программы МиниГен — чинить код и "
                         "обновлять программу.")
            lines.append("Отпечаток: %s — в работе с %s, окно откроется в %s."
                         % (item.get("fingerprint") or "—",
                            fmt(last) if last else "—",
                            fmt(unblock) if unblock else "—"))
            lines.append("Ошибка: %s" % _clip(str(record.get("text") or "")))
            lines.append("Что делать: code.search/code.read → code.branch (своя "
                         "ветка и свой worktree) → code.patch → code.tests → "
                         "code.commit → code.pr; сборка и раскатка обновления — "
                         "update.start (за словом владельца).")
        lines.append("")
    for item in blocked or []:
        if not item.get("show"):
            continue
        state = item.get("state") or {}
        last = int(state.get("last_sent_ms") or 0)
        unblock = int(state.get("unblock_ms") or 0)
        lines.append("Ошибка %s уже в работе (ушла в %s) — повтор не отправляю "
                     "до %s." % (item.get("fingerprint") or "—",
                                 fmt(last) if last else "—",
                                 fmt(unblock) if unblock else "—"))
        lines.append("")
    return "\n".join(lines).strip()
