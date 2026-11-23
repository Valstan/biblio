"""Каталог инструментов встроенной нейросети «МиниГена».

Каждый инструмент — это обёртка над командой программы (`minigen_bridge.py`).
Никакой логики «МиниГена» здесь нет: нейросеть умеет ровно то же, что
кнопки в окне, и ничего больше. Это и делает её безопасной — она не может
сделать то, чего не может сделать владелец через интерфейс.

Опасность помечена явно:

- `dangerous=False` — читающий или восстанавливающий вызов, выполняется сразу;
- `dangerous=True`  — действие, которое меняет состояние области или тратит
  очки: выполняется только после подтверждения владельца.

Полное описание — в `docs/TOOLS.md`.
"""

from __future__ import annotations

from typing import Any

SLUG = {"type": "string", "description": "Короткое имя области (slug), например brain2"}


def _tool(name: str, description: str, parameters: dict[str, Any], command: str,
          dangerous: bool = False, hint: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "command": command,
        "dangerous": dangerous,
        "confirm_hint": hint,
    }


TOOLS: list[dict[str, Any]] = [
    _tool("areas.list",
          "Список всех рабочих областей «МиниГена»: название, запущена ли, "
          "режим обслуживания (автомат/вручную), состояние входа. "
          "Начинай с этого инструмента.",
          {"type": "object", "properties": {}},
          "areas.list"),

    _tool("areas.state",
          "Подробное состояние одной области: сессии с их вердиктами "
          "(работает/готова/брошена/упала по сети/кончились очки/контекст заполнен), "
          "заполнение контекста, состояние входа.",
          {"type": "object", "properties": {"slug": SLUG}, "required": ["slug"]},
          "areas.state"),

    _tool("sessions.list",
          "Сессии области с вердиктами сторожа (что с каждой ниткой и почему).",
          {"type": "object", "properties": {"slug": SLUG}, "required": ["slug"]},
          "sessions.list"),

    _tool("sessions.read",
          "Последние ходы сессии области: что обсуждалось и чем закончилось.",
          {"type": "object",
           "properties": {"slug": SLUG, "session_id": {"type": "string"}},
           "required": ["slug", "session_id"]},
          "sessions.read"),

    _tool("journal.read",
          "Хвост журнала «МиниГена» (сторожа и решения). Можно отфильтровать "
          "по подстроке. Используй, чтобы понять, что уже чинили и почему.",
          {"type": "object",
           "properties": {"limit": {"type": "integer"},
                          "contains": {"type": "string"}},
           "required": []},
          "journal.read"),

    _tool("stats.summary",
          "Сводка работы за период: сколько раз перезапускались области и по "
          "каким причинам (отказ по очкам, обрыв сети, компактизация контекста, "
          "потеря входа), сколько областей запущено.",
          {"type": "object",
           "properties": {"days": {"type": "integer", "description": "сколько дней смотреть"}},
           "required": []},
          "stats.summary"),

    _tool("network.check",
          "Живая проверка связи: доступен ли вендор MiniMax, какие пути (прокси) "
          "живы, устойчиво ли соединение. Занимает секунды — зови, если область "
          "не видит интернет.",
          {"type": "object", "properties": {}},
          "network.check"),

    _tool("areas.set_autoheal",
          "Переключить режим обслуживания области: автомат (отказ по очкам "
          "лечится сам) или вручную (вход меняет владелец).",
          {"type": "object",
           "properties": {"slug": SLUG, "autoheal": {"type": "boolean"}},
           "required": ["slug", "autoheal"]},
          "areas.set_autoheal", True,
          "сменит режим обслуживания области"),

    _tool("areas.start",
          "Запустить десктоп области (открыть окно MiniMax Code этой области).",
          {"type": "object", "properties": {"slug": SLUG}, "required": ["slug"]},
          "areas.start", True, "откроет окно десктопа области"),

    _tool("areas.stop",
          "Остановить десктоп области.",
          {"type": "object", "properties": {"slug": SLUG}, "required": ["slug"]},
          "areas.stop", True, "остановит десктоп области (работа в ней прервётся)"),

    _tool("areas.restart",
          "Перезапустить десктоп области: стоп с добиванием дерева процессов, "
          "проверка, что ушёл, затем старт и проверка, что поднялся.",
          {"type": "object", "properties": {"slug": SLUG}, "required": ["slug"]},
          "areas.restart", True, "перезапустит десктоп области"),

    _tool("areas.relogin",
          "Оживить вход области: взять у КАРМАНА следующий аккаунт, посадить его "
          "и перезапустить область. Пул аккаунтов при этом тратится.",
          {"type": "object", "properties": {"slug": SLUG}, "required": ["slug"]},
          "areas.relogin", True, "сменит аккаунт области (тратится аккаунт пула)"),

    _tool("areas.create",
          "Создать новую рабочую область: каталог данных, авторизация из шаблона, "
          "запись в конфиг, ярлык на рабочем столе.",
          {"type": "object",
           "properties": {
               "slug": {"type": "string", "description": "короткое имя латиницей"},
               "title": {"type": "string", "description": "название для панели задач"},
               "folder": {"type": "string", "description": "рабочая папка проекта"},
               "karman": {"type": "string", "description": "раздел КАРМАНа для токена"},
           },
           "required": ["slug", "title"]},
          "areas.create", True, "создаст новую область (файлы и запись в конфиге)"),

    _tool("sessions.send",
          "Отправить сообщение в сессию области (например «Продолжай»). "
          "Тратит очки аккаунта области.",
          {"type": "object",
           "properties": {"slug": SLUG, "session_id": {"type": "string"},
                          "text": {"type": "string"}},
           "required": ["slug", "session_id", "text"]},
          "sessions.send", True, "отправит сообщение в сессию (тратятся очки)"),

    _tool("sessions.rotate",
          "Ротация сессии: корректно закрыть, архивировать и открыть новую "
          "с той же работой. Нужна, когда контекст заполнен или рантайм сжал его.",
          {"type": "object",
           "properties": {"slug": SLUG, "session_id": {"type": "string"}},
           "required": ["slug", "session_id"]},
          "sessions.rotate", True, "закроет сессию и откроет новую"),

    _tool("sessions.close",
          "Закрыть сессию области.",
          {"type": "object",
           "properties": {"slug": SLUG, "session_id": {"type": "string"}},
           "required": ["slug", "session_id"]},
          "sessions.close", True, "закроет сессию"),
]


def by_name() -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in TOOLS}
