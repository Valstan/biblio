"""Совместимость: модуль называется `minigen_core.py` (с подчёркиванием).

Дефис в имени модуля делает его неимпортируемым (`import minigen-core` не
работает), поэтому настоящая реализация живёт в `minigen_core.py`, а этот
файл — тонкий шим для старых ссылок. Ничего тут не дублируется: кода нет.

См. `minigen_core.py` — общие куски «МиниГена» (журнал, состояние, конфиг
областей, чтение базы рантайма, запуск `mcode`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minigen_core import *  # noqa: E402,F401,F403
