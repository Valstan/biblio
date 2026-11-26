# -*- coding: utf-8 -*-
"""Приёмка: watchdog очков — пороги, проба аккаунта, учёт расхода.

Что проверяем (всё — на живых правилах, без сети):

1. **Классификация ответа вендора.** Отказ по очкам и отказ по сети — это
   разные вещи: первый лечится сменой аккаунта, второй — сменой прокси.
   Ошибочная классификация жжёт пул Кармана (урок 21.11).
2. **Порог по расходу.** Дефолт по номиналу очков; своя статистика отказов
   порог **понижает**; «мёртвый на входе» аккаунт (1–2 запроса) в статистику
   не попадает — его ловит проба.
3. **Учёт расхода.** Биллинговые токены = input + output; кэш в счёт не
   идёт (он в десятки раз больше при той же ёмкости аккаунта).
4. **Окно суток.** Расход считается от суточного сброса очков (03:00 MSK),
   а не от установки аккаунта: вчерашний расход сегодняшними очками не
   оплачивается.
5. **Встройка в лечащего.** Вердикт «ПОЧТИ БЕЗ ОЧКОВ» (T4) лечится, проба
   аккаунта стоит в пути получения аккаунта.

Запуск: python3 tools/minimax-project-areas/test_minimax_points.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sqlite3
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(HERE / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_area(tmp: str, rows: list) -> pathlib.Path:
    """Фейковая область с базой рантайма и несколькими запросами."""
    area = pathlib.Path(tmp) / "p-test"
    db_dir = area / "v2" / "sqlite"
    db_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_dir / "runtime-state.sqlite"))
    con.execute("CREATE TABLE local_runtime_token_usage ("
                "id INTEGER PRIMARY KEY, session_id TEXT, agent_name TEXT, "
                "framework_type TEXT, turn_id TEXT, model TEXT, ts INTEGER, "
                "input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, "
                "cache_read_tokens INTEGER, cache_write_tokens INTEGER, "
                "cost_usd REAL, raw TEXT)")
    for ts, inp, out, cache in rows:
        con.execute("INSERT INTO local_runtime_token_usage "
                    "(session_id, agent_name, framework_type, turn_id, model, ts, "
                    " input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, "
                    " cache_write_tokens, cost_usd, raw) "
                    "VALUES ('s','a','f','t','m',?,?,?,0,?,0,0,'')", (ts, inp, out, cache))
    con.commit()
    con.close()
    return area


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    mp = load_module("minimax_points_check", "minimax-points.py")

    print("1) ответ вендора: отказ по очкам ≠ отказ по сети")
    check(mp.classify_response(200, "") == "ok", "200 должен быть ok")
    check(mp.classify_response(401, "") == "invalid", "401 — недействительный вход")
    check(mp.classify_response(402, "") == "exhausted", "402 — кончились очки")
    check(mp.classify_response(400, "insufficient balance (1008)") == "exhausted",
          "текст 'insufficient balance' — кончились очки")
    check(mp.classify_response(429, "FreeUsageLimitError") == "exhausted",
          "429 с FreeUsageLimitError — кончились очки")
    check(mp.classify_response(429, "rate limit exceeded") == "unknown",
          "429 без слов о балансе — это rate limit, не очки")
    check(mp.classify_response(500, "internal error") == "unknown",
          "500 — неизвестно, очки не трогаем")
    check(mp.classify_response(200, "") != "exhausted", "живой ответ не должен быть отказом")

    print("2) порог по расходу")
    check(mp.budget_for(1000, []) == mp.DEFAULT_BUDGET[1000],
          "дефолт для 1000 очков не взят")
    check(mp.budget_for(400, []) == mp.DEFAULT_BUDGET[400],
          "дефолт для 400 очков не взят")
    check(mp.budget_for(0, []) == mp.FALLBACK_BUDGET,
          "без номинала — запасной порог")
    # своя статистика отказов порог понижает
    deaths = [{"points": 1000, "burn": 300_000, "requests": 40},
              {"points": 1000, "burn": 400_000, "requests": 60},
              {"points": 1000, "burn": 500_000, "requests": 80}]
    learned = mp.budget_for(1000, deaths)
    check(learned < mp.DEFAULT_BUDGET[1000],
          "своя статистика отказов должна понижать порог (получено %s)" % learned)
    check(learned == 300_000, "порог должен стать p25 отказов (получено %s)" % learned)
    # «мёртвый на входе» в статистику не идёт
    only_dead_on_arrival = [{"points": 1000, "burn": 50_000, "requests": 1}]
    check(mp.budget_for(1000, only_dead_on_arrival) == mp.DEFAULT_BUDGET[1000],
          "отказ с 1 запросом — это «мёртвый на входе», порог трогать не должен")
    # порог не проваливается в ноль
    check(mp.budget_for(1000, [{"points": 1000, "burn": 1, "requests": 50}]) >= mp.MIN_BUDGET,
          "порог не может быть нулевым")

    print("3) учёт расхода: input + output, кэш не в счёт")
    now = int(time.time() * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        area = make_area(tmp, [(now - 60_000, 1000, 200, 999_999),
                               (now - 30_000, 300, 100, 500_000)])
        burn = mp.burn_since(area, 0)
        check(burn.get("billable") == 1600,
              "биллинговые токены должны быть input+output (получено %s)" % burn.get("billable"))
        check(burn.get("cache") == 1_499_999,
              "кэш считается отдельно (получено %s)" % burn.get("cache"))
        check(burn.get("requests") == 2, "число запросов (получено %s)" % burn.get("requests"))
        check(burn.get("last_ms", 0) > 0, "время последнего запроса не определено")
        empty = mp.burn_since(area, now + 1000)
        check(empty.get("billable", 0) == 0, "за окном после конца расхода не должно быть")

    print("4) окно суток: от суточного сброса (03:00 MSK)")
    start = mp.day_start_ms() / 1000
    local = time.localtime(start)
    check(local.tm_hour == mp.RESET_HOUR,
          "окно должно начинаться в %s:00, а не в %s:00" % (mp.RESET_HOUR, local.tm_hour))
    check(start <= time.time(), "окно не может быть в будущем")
    import datetime
    ref = datetime.datetime.now().replace(hour=mp.RESET_HOUR, minute=0, second=0, microsecond=0)
    if datetime.datetime.now() < ref:
        ref -= datetime.timedelta(days=1)
    check(abs(start - ref.timestamp()) < 120,
          "окно не совпадает с сегодняшним сбросом (получено %s)" % datetime.datetime.fromtimestamp(start))

    print("5) лечащий: вердикт T4 и проба аккаунта на месте")
    source = (HERE / "minimax-autheal.py").read_text(encoding="utf-8")
    check('out["verdict"] = "ПОЧТИ БЕЗ ОЧКОВ"' in source,
          "в diagnose нет вердикта «ПОЧТИ БЕЗ ОЧКОВ»")
    check('"ПОЧТИ БЕЗ ОЧКОВ"' in re.search(r'diag\["verdict"\] not in \([^)]*\)', source).group(0),
          "исчерпывающий список лечимых вердиктов не включает T4")
    check("points_probe(" in source, "проба аккаунта не вызывается лечащим")
    check("points_low(" in source, "проверка расхода не вызывается лечащим")
    check("points_learn(" in source, "лечащий не учит порог на своих отказах")
    probe_call = source.count("points_probe(area[\"dir\"])")
    check(probe_call >= 1, "проба аккаунта не стоит в пути получения аккаунта")

    print("6) watchdog не ломает лечащего без сети")
    check(mp.classify_response(0, "") == "unknown",
          "сетевая ошибка не должна читаться как отказ по очкам")

    if failures:
        print("❌ приёмка watchdog'а очков: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка watchdog'а очков: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
