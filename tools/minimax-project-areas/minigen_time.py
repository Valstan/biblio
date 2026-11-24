# -*- coding: utf-8 -*-
"""Время программы: **везде Москва, Россия (UTC+3)**.

Заказ владельца (24.11): «возьми за основу время по Москве и явно об этом
показывай в программе, что она время везде считает по часовому поясу Москва,
Россия».

Почему фиксированное смещение, а не `ZoneInfo("Europe/Moscow")`: на Windows
зоны IANA нет без пакета `tzdata` (а он не везде), а Москва с 2014 года **не
переходит на летнее время** ⇒ UTC+3 круглый год, без «часовой стрелки» в
конце марта.

Unix-время (`time.time()`) всегда UTC — его зона не меняется; меняется только
то, как мы его **показываем**. Здесь вся программа показывает его по Москве:
журнал, статистика, «в установлено», легенда обновления, отчёты.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

TZ_NAME = "Europe/Moscow"
TZ_LABEL = "Москва, Россия (UTC+3)"
MSK = timezone(timedelta(hours=3), "MSK")


def now() -> datetime:
    """Текущее время по Москве."""
    return datetime.now(MSK)


def now_ms() -> int:
    """Текущее время в миллисекундах (Unix — он всегда UTC)."""
    return int(time.time() * 1000)


def to_msk(value: float | int | None) -> datetime | None:
    """Метка времени (мс или с — как пришло) → по Москве."""
    if not value:
        return None
    number = float(value)
    seconds = number / 1000.0 if number > 1e11 else number
    try:
        return datetime.fromtimestamp(seconds, MSK)
    except (OSError, OverflowError, ValueError):
        return None


def fmt_ms(value: float | int | None, fmt: str = "%d.%m %H:%M:%S") -> str:
    """Метка времени → строка по Москве (пусто — «—»)."""
    moment = to_msk(value)
    return moment.strftime(fmt) if moment else "—"


def fmt_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Сейчас по Москве — строкой."""
    return now().strftime(fmt)
