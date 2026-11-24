# -*- coding: utf-8 -*-
"""Приёмка: время программы — **везде Москва, Россия (UTC+3)**.

Заказ владельца (24.11): «возьми за основу время по Москве и явно об этом
показывай в программе, что она время везде считает по часовому поясу Москва,
Россия».

Проверяем:

1. **одна реализация** (`minigen_time.py`): ядро, демоны и мост берут её оттуда;
2. **журнал пишется по Москве** — иначе окно статистики (граница окна тоже
   московская) не видело бы события;
3. **демоны не печатают время по зоне машины** — лечащий, сторож оборванных,
   сторож сессий, переключатель;
4. **мост считает границу окна статистики по Москве**;
5. окно объявляет зону явно (проверяется отдельно тестами окна).

Запуск: python3 tools/minimax-project-areas/test_time_is_moscow.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
MSK = timezone(timedelta(hours=3), "MSK")


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        "time_check_%s" % name.replace("-", "_"), str(HERE / name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("1) модуль времени: Москва, Россия (UTC+3)")
    mtime = load("minigen_time.py")
    check(mtime.TZ_NAME == "Europe/Moscow", "зона не объявлена: %s" % mtime.TZ_NAME)
    check("Москва" in mtime.TZ_LABEL, "подпись не про Москву: %s" % mtime.TZ_LABEL)
    check(mtime.MSK.utcoffset(None) == timedelta(hours=3),
          "смещение не +3: %s" % mtime.MSK.utcoffset(None))
    moment = mtime.to_msk(1_700_000_000)
    check(moment is not None and moment.utcoffset() == timedelta(hours=3),
          "метка времени переведена не в Москву")
    check(mtime.fmt_ms(0) == "—", "пустое время должно быть «—»")
    now_ms = mtime.now_ms()
    check(abs(now_ms - int(time.time() * 1000)) < 5000,
          "now_ms() расходится с системным временем")

    print("2) ядро пишет журнал по Москве")
    core = load("minigen_core.py")
    check(getattr(core, "MSK", None) is not None, "ядро не знает про Москву")
    stamp = core.fmt_ms(int(time.time() * 1000))
    check(datetime.now(MSK).strftime("%d.%m") == stamp[:5],
          "журнал и время программы разошлись: %s" % stamp)
    check("datetime.now()" not in (HERE / "minigen_core.py").read_text(encoding="utf-8"),
          "ядро печатает время по зоне машины")

    print("3) демоны считают время по Москве")
    for name in ("minimax-autheal.py", "minimax-orphans.py",
                 "minimax-sessionwatch.py", "minimax-switch.py"):
        src = (HERE / name).read_text(encoding="utf-8")
        # Общее время можно взять прямо (`minigen_time`) или через ядро
        # (`core.now()`) — это одна и та же реализация.
        takes_common = ("import minigen_time as mtime" in src) or ("core.now()" in src)
        check(takes_common, "%s не берёт общее время" % name)
        check("datetime.now()" not in src,
              "%s печатает время по зоне машины" % name)
        check("fromtimestamp(ms / 1000)" not in src,
              "%s форматирует время по зоне машины" % name)

    print("4) мост считает границу окна по Москве")
    state = (HERE.parent / "minigen-gui" / "bridge" / "minigen_state.py").read_text(
        encoding="utf-8")
    check("time.localtime" not in state,
          "мост ещё считает границу окна по зоне машины")
    check('fmt_ms(int(since * 1000)' in state,
          "мост не считает границу окна по Москве")

    print("5) раскатка везёт модуль времени на боевое место")
    rollout = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
    check('"minigen_time.py"' in rollout,
          "модуль времени не в списке раскатки — демоны и мост его не найдут")

    if failures:
        print("❌ приёмка московского времени: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка московского времени: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
