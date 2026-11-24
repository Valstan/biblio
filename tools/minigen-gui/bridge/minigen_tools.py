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

    _tool("reports.recent",
          "Отчёты программы: что сторож и инструменты сделали с прошлого раза "
          "и где ошиблись. Это «ящик отчётов» — он короче журнала и с "
          "уровнем (info/warn/error). Смотри его первым, когда спрашивают, "
          "что происходило.",
          {"type": "object",
           "properties": {"limit": {"type": "integer"},
                          "level": {"type": "string",
                                    "description": "info | warn | error (пусто — все)"}},
           "required": []},
          "reports.list"),

    _tool("lineage.show",
          "Родословная этой копии программы: её идентификатор, семейство, "
          "поколение, от какой версии «МиниГена» она получила форк, кто её "
          "родитель и какие наработки (гены) она приняла от других копий.",
          {"type": "object", "properties": {}},
          "lineage.show"),

    _tool("clone.status",
          "Свой клон копии на GitHub: есть ли он, где лежит, какие репозитории "
          "подключены (свой и upstream), в какой ветке работа и есть ли "
          "незакоммиченные правки.",
          {"type": "object", "properties": {}},
          "clone.status"),

    _tool("clone.setup",
          "Завести свою копию на GitHub: создать репозиторий копии, взять клон "
          "рядом с программой, подключить upstream (репозиторий программы) и "
          "клон пула наработок. После этого правки и PR идут из своего клона.",
          {"type": "object",
           "properties": {"upstream": {"type": "string",
                                       "description": "репозиторий программы (по умолчанию Valstan/biblio)"},
                          "pool": {"type": "string",
                                   "description": "репозиторий пула наработок"},
                          "private": {"type": "boolean",
                                      "description": "свой репозиторий приватный (по умолчанию да)"}},
           "required": []},
          "clone.setup", True,
          "создаст репозитории в вашем GitHub-аккаунте"),

    _tool("clone.sync",
          "Обновить свой клон и пул наработок (только fast-forward; локальные "
          "правки не трогаются).",
          {"type": "object", "properties": {}},
          "clone.sync"),

    _tool("map.show",
          "Карта родословной «МиниГена»: все копии и гены, кто с кем связан — кто "
          "родился от кого, кто какой ген создал, кто чей взял и у кого. "
          "Смотри её, когда надо понять, где мы в общей паутине разработки.",
          {"type": "object", "properties": {}},
          "map.show"),

    _tool("genes.list",
          "Гены пула: наработки, которые опубликовали копии «МиниГена». Каждый "
          "ген — манифест, diff и заметка: что сделано, кем, на какой версии. "
          "Смотри его, когда ищешь, что можно взять себе.",
          {"type": "object", "properties": {}},
          "genes.list"),

    _tool("genes.read",
          "Прочитать ген: манифест, заметка «зачем» и сам diff.",
          {"type": "object",
           "properties": {"gene": {"type": "string",
                                   "description": "идентификатор гена, например gene-mg-abcdef12-001"}},
           "required": ["gene"]},
          "genes.read"),

    _tool("genes.publish",
          "Опубликовать свою наработку в общий пул: берётся текущая правка "
          "(code.branch + code.patch), упаковывается в ген и уходит в пул. "
          "После публикации ген доступен другим копиям.",
          {"type": "object",
           "properties": {"title": {"type": "string"},
                          "summary": {"type": "string", "description": "в двух словах"},
                          "notes": {"type": "string", "description": "подробнее: зачем и как"}},
           "required": ["title"]},
          "genes.publish", True, "опубликует наработку в общем пуле (видят другие копии)"),

    _tool("genes.adopt",
          "Внедрить чужой ген: создаётся ветка в своём клоне, ген применяется, "
          "прогоняются проверки — и открывается PR. Слияние — за владельцем.",
          {"type": "object",
           "properties": {"gene": {"type": "string"},
                          "title": {"type": "string", "description": "своё название ветки/PR"}},
           "required": ["gene"]},
          "genes.adopt", True, "изменит код программы (предложит PR)"),

    _tool("update.status",
          "Обновление программы: какая версия установлена, какая есть в "
          "репозитории, идёт ли обновление сейчас и что уже сделано по шагам.",
          {"type": "object", "properties": {}},
          "update.status"),
    _tool("update.check",
          "Проверить, есть ли новая версия программы (сходит в репозиторий "
          "за изменениями). Ничего не меняет.",
          {"type": "object", "properties": {}},
          "update.check"),
    _tool("update.start",
          "Обновить программу: забрать новую версию из репозитория, собрать "
          "окно и поставить программу на место (демоны перезапустятся). Идёт "
          "в фоне, шаги видны в update.status. После обновления окно надо "
          "перезапустить — решение за владельцем.",
          {"type": "object", "properties": {}},
          "update.start", True,
          "пересоберёт и переустановит программу"),

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

    _tool("sessions.reset",
          "Сбросить сессию НА МЕСТЕ: сессия сама доходит до конца и печатает "
          "сводку, рантайм сжимает контекст, и та же сессия продолжает нитку. "
          "Новая сессия не открывается. Нужна, когда контекст заполнен или "
          "рантайм его уже сжал.",
          {"type": "object",
           "properties": {"slug": SLUG, "session_id": {"type": "string"}},
           "required": ["slug", "session_id"]},
          "sessions.reset", True, "очистит контекст сессии и продолжит её же"),

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

    # ── самообновление программы ────────────────────────────────────────
    _tool("code.list",
          "Список файлов репозитория программы (исходники «МиниГена» и "
          "соседние модули). Начинай с него, если ищешь, где что лежит.",
          {"type": "object",
           "properties": {"path": {"type": "string", "description": "подкаталог, пусто — весь репозиторий"}},
           "required": []},
          "code.list"),

    _tool("code.read",
          "Прочитать файл репозитория программы. Файлы с ключами и "
          "авторизацией закрыты — их читать нельзя (и не нужно). "
          "Большие файлы можно читать по частям: start_line и end_line.",
          {"type": "object",
           "properties": {
               "path": {"type": "string", "description": "путь относительно корня репозитория"},
               "start_line": {"type": "integer", "description": "с какой строки читать"},
               "end_line": {"type": "integer", "description": "до какой строки читать"}},
           "required": ["path"]},
          "code.read"),

    _tool("code.search",
          "Поиск по репозиторию программы: где встречается текст. Возвращает "
          "файл, номер строки и саму строку.",
          {"type": "object",
           "properties": {"query": {"type": "string"}, "path": {"type": "string"}},
           "required": ["query"]},
          "code.search"),

    _tool("code.branch",
          "Начать правку программы: создать отдельный git-worktree и ветку "
          "под неё. Правка идёт в отдельном дереве (дерево сессии не "
          "трогается), и только в ветке — никогда в master.",
          {"type": "object",
           "properties": {"title": {"type": "string", "description": "что чиним, коротко"}},
           "required": ["title"]},
          "code.branch", True, "создаст ветку и отдельное рабочее дерево"),

    _tool("code.patch",
          "Точечная правка файла в worktree правки: заменить фрагмент "
          "другим. Фрагмент должен встречаться РОВНО один раз — иначе "
          "правка не применяется (приблизительная замена — это порча кода).",
          {"type": "object",
           "properties": {
               "path": {"type": "string"},
               "find": {"type": "string", "description": "что заменить, дословно"},
               "replace": {"type": "string", "description": "на что заменить"}},
           "required": ["path", "find", "replace"]},
          "code.patch", True, "изменит код программы"),

    _tool("code.diff",
          "Показать текущий diff правки в worktree: какие файлы изменены и "
          "как. Смотри diff перед коммитом — так видно, что правка не задела "
          "лишнего.",
          {"type": "object", "properties": {}},
          "code.diff"),

    _tool("code.tests",
          "Прогнать проверки репозитория в worktree правки (тесты моста, "
          "приёмка панели, приёмка записи конфига). Зелёные проверки — "
          "условие для PR.",
          {"type": "object",
           "properties": {"target": {"type": "string",
                                     "description": "all | python | panel | race | js"}},
           "required": []},
          "code.tests"),

    _tool("code.commit",
          "Закоммитить правку в ветку worktree (сообщение коммита — по "
          "правилам репозитория: Conventional Commits, по-английски).",
          {"type": "object",
           "properties": {"message": {"type": "string"}},
           "required": ["message"]},
          "code.commit", True, "закоммитит правку в ветку"),

    _tool("code.pr",
          "Отправить ветку и открыть PR в master по правилам репозитория "
          "(ветка → коммит → push → PR; прямой пуш в master запрещён). "
          "Решение о слиянии — за владельцем.",
          {"type": "object",
           "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
           "required": ["title"]},
          "code.pr", True, "отправит ветку и откроет PR"),

    _tool("code.discard",
          "Убрать worktree правки и её ветку (правка не нужна).",
          {"type": "object", "properties": {}},
          "code.discard", True, "удалит worktree правки и её ветку"),
]


def by_name() -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in TOOLS}
