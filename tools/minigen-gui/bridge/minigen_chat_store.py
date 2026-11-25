"""История чата «Управляющего» на диске машины (промт #129, заказ 25.11).

Заказ владельца: «Чат «Управляющий» должен помнить историю, не терять её при
смене вкладки и перезапуске окна».

**Что здесь и почему так**

- история лежит **на машине**, в `%LOCALAPPDATA%\\minigen-gui\\chat-history.json` —
  **вне репозитория** (D-038): в переписке могут быть имена хостов, пути и
  ответы инструментов, в репозиторий такое не выкладывается;
- формат — список реплик `{role, text, calls, at}`: `role` (`user` /
  `assistant` / `system`), `text` (текст реплики), `calls` (вызовы
  инструментов — подробности, которые окно показывает по требованию) и `at`
  (время, мс);
- **предел**: 200 реплик или 1 МБ — что наступит первым. Старые реплики
  уходят первыми; без предела файл рос бы без остановки;
- запись **атомарная**: сначала временный файл, потом замена — оборванная
  запись не портит историю;
- **отчёты сторожей сюда не попадают** (см. `minigen_bridge.py`): сводка
  отчётов — это подсказка на один ход, а не реплика в переписке.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Стор программы на машине (настройки, родословная, пул генов).
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
#: Файл видимой истории чата.
HISTORY_FILE = LOCAL / "chat-history.json"
#: Предел числа реплик в файле.
MAX_MESSAGES = 200
#: Предел размера файла (байт).
MAX_BYTES = 1024 * 1024
#: Роли, которые могут быть в истории.
ROLES = ("user", "assistant", "system")


def _now() -> int:
    return int(time.time() * 1000)


def path() -> Path:
    """Где лежит история (путь — наружу можно: секретов в файле нет)."""
    return HISTORY_FILE


def _clean(message: Any) -> dict[str, Any] | None:
    """Привести реплику к виду файла; пустое — выбросить."""
    if not isinstance(message, dict):
        return None
    role = str(message.get("role") or "")
    if role not in ROLES:
        return None
    text = str(message.get("text") or "").strip()
    calls = message.get("calls")
    clean_calls = []
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            clean_calls.append({"tool": str(call.get("tool") or ""),
                                "args": call.get("args") or {},
                                "result": call.get("result"),
                                "approved": bool(call.get("approved"))})
    if not text and not clean_calls:
        return None            # пустая реплика — не место в истории
    out: dict[str, Any] = {"role": role, "text": text}
    if clean_calls:
        out["calls"] = clean_calls
    at = message.get("at")
    out["at"] = int(at) if isinstance(at, (int, float)) else _now()
    return out


def load() -> list[dict[str, Any]]:
    """История с диска (пустая — если файла нет или он битый)."""
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        clean = _clean(item)
        if clean is not None:
            out.append(clean)
    return out


def save(messages: list[dict[str, Any]]) -> bool:
    """Записать историю (атомарно, с пределами). Успех — True."""
    clean: list[dict[str, Any]] = []
    for message in messages or []:
        item = _clean(message)
        if item is not None:
            clean.append(item)
    clean = clean[-MAX_MESSAGES:]
    payload = {"version": 1, "updated_at": _now(), "messages": clean}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # предел по размеру: отрезаем самые старые реплики, пока файл не влез
    while len(text.encode("utf-8")) > MAX_BYTES and len(clean) > 1:
        clean = clean[1:]
        payload = {"version": 1, "updated_at": _now(), "messages": clean}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = HISTORY_FILE.with_suffix(".tmp")
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(HISTORY_FILE))
        return True
    except OSError:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        return False


def append(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Дописать реплику в историю и вернуть её целиком."""
    clean = _clean(message)
    if clean is None:
        return load()
    items = load()
    items.append(clean)
    save(items)
    return items


def clear() -> bool:
    """Стереть историю (файл и его содержимое)."""
    try:
        if HISTORY_FILE.is_file():
            HISTORY_FILE.unlink()
    except OSError:
        return False
    return True


def agent_history(messages: list[dict[str, Any]],
                  limit: int = 40) -> list[dict[str, Any]]:
    """Видимая история → память агента (в формате сообщений модели).

    ⚠️ Восстанавливается **текст** реплик: подробности вызовов в память
    модели не возвращаются — она узнаёт состояние программы через свои
    инструменты, а не через чужой JSON (знания программы так и требуют).
    """
    out: list[dict[str, Any]] = []
    for message in messages or []:
        role = str(message.get("role") or "")
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            out.append({"role": "user", "content": text})
        elif role == "assistant":
            out.append({"role": "assistant", "content": text})
    return out[-limit:]
