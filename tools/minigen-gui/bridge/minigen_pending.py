"""Ожидающее подтверждение опасного действия — на диске машины (промт #129).

Заказ владельца (промт #129, п. 3–4): чат «Управляющий» не теряет историю при
смене вкладки и **перезапуске окна**. История на диске уже лежит
(`minigen_chat_store.py`), а ожидающее подтверждение жило только в памяти
моста: перезапустил окно — и вопрос «подтвердите действие» исчез, а действие
молча не выполнилось. Владелец не знает, что на него ждали ответа.

Что здесь и почему так:

- файл `%LOCALAPPDATA%\\minigen-gui\\chat-pending.json` — **вне репозитория**
  (D-038): в нём могут быть аргументы опасного действия (путь, текст правки);
- **токен вендора в файл не попадает**: конфиг запроса в снимок не берётся, при
  подтверждении он собирается заново (у области свой живой токен);
- **переписка не дублируется**: её и так восстанавливает `minigen_chat_store`,
  в файле лежит только сам ход (его блоки и вызовы);
- запись **атомарная** (временный файл + замена): оборванная запись не
  оставит «половинчатое» подтверждение;
- **предел размера** — 8 МБ: если ход не влезает, результаты безопасных
  вызовов обрезаются (вендор видел именно обрезанные), а если и это не
  влезает — файл не пишется вовсе (лучше потерять восстановление, чем
  оставить владельцу подтверждение, которого не выполнить).

Модуль хранит **снимок** (его делает агент: `Agent.pending_snapshot()`) и
ничего не знает про устройство хода.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Стор программы на машине (настройки, история чата, родословная).
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
#: Файл ожидающего подтверждения.
PENDING_FILE = LOCAL / "chat-pending.json"
#: Предел размера файла (байт).
MAX_BYTES = 8 * 1024 * 1024
#: Версия формата.
VERSION = 1


def _now() -> int:
    return int(time.time() * 1000)


def path() -> Path:
    """Где лежит ожидающее подтверждение (путь наружу можно: секретов нет)."""
    return PENDING_FILE


def _write(payload: dict[str, Any]) -> bool:
    """Записать снимок атомарно. Не влезает — False (файл не трогаем)."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > MAX_BYTES:
        return False
    tmp = PENDING_FILE.with_suffix(".tmp")
    try:
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(PENDING_FILE))
        return True
    except OSError:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        return False


def save(snapshot: dict[str, Any] | None) -> bool:
    """Сохранить снимок ожидающего подтверждения (None ⇒ стереть).

    ⚠️ Если снимок не влезает в предел — пробуем ещё раз с обрезанными
    результатами вызовов: вендор видел обрезанные, значит и продолжение хода
    увидит то же самое.
    """
    if not snapshot:
        return clear()
    payload = {"version": VERSION, "updated_at": _now(), "pending": snapshot}
    if _write(payload):
        return True
    trimmed = dict(snapshot)
    trimmed["results"] = [[item[0], {"truncated": True,
                                     "preview": str(item[1])[:2000]}]
                          for item in (snapshot.get("results") or [])
                          if isinstance(item, (list, tuple)) and len(item) == 2]
    payload = {"version": VERSION, "updated_at": _now(), "pending": trimmed}
    return _write(payload)


def load() -> dict[str, Any] | None:
    """Снимок ожидающего подтверждения с диска (нет файла — None)."""
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    snapshot = data.get("pending")
    if not isinstance(snapshot, dict) or not snapshot:
        return None
    return snapshot


def clear() -> bool:
    """Стереть ожидающее подтверждение (файла нет — уже стёрто)."""
    try:
        if PENDING_FILE.is_file():
            PENDING_FILE.unlink()
    except OSError:
        return False
    return True
