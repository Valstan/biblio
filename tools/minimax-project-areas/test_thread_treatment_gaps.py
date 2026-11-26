# -*- coding: utf-8 -*-
"""Приёмка: два пробела промта КАРМАНА #172, которые остались после слияния
параллельной полосы (PR #222).

Промт #172 требует от лечения сессий двух вещей, которых в слитой реализации
не было:

1. **Предел простоя вынесен явно** — «“Продолжать” без единого действия —
   сессия может стоять долго. Нужен предел (например, N часов без отчёта ⇒
   перенос нитки) — это решение владельца, вынеси его явно и не выбирай сам».
   Значит: константа есть, значение **не выбрано** (0 = выключено), а когда
   владелец её включит — молчащая нитка переносится, а не стоит вечно.
2. **Секретов в паспорте нет** (D-038): паспорт наполняется текстом агента
   (сводка, handoff, отчёт) — а агент иногда печатает токен или ключ.

Запуск: python3 tools/minimax-project-areas/test_thread_treatment_gaps.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    thread = load_module("thread_172_gaps", HERE / "minigen_thread.py")

    print("1) предел простоя вынесен явно, а значение не выбрано")
    check(hasattr(thread, "STUCK_LIMIT_HOURS"),
          "в модуле нет константы предела простоя")
    check(thread.STUCK_LIMIT_HOURS == 0,
          "предел простоя включён без решения владельца: %s"
          % thread.STUCK_LIMIT_HOURS)
    src = (HERE / "minigen_thread.py").read_text(encoding="utf-8")
    check("STUCK_LIMIT_HOURS" in src and "решение владельца" in src.lower(),
          "в коде нет пометки, что предел — решение владельца")

    print("2) правило-фолбэк: без отчёта — продолжать, а не завершать")
    got = thread.fallback_decision({"has_report": False, "last_turn": {"idle_min": 5}})
    check(got["action"] == thread.CONTINUE,
          "без отчёта правило требует не «%s»" % got["action"])
    check("заверши" not in str(got.get("instruction") or "").lower(),
          "инструкция правила требует завершения без отчёта: %s" % got.get("instruction"))

    print("3) отчёт есть — завершать можно")
    got = thread.fallback_decision({"has_report": True, "last_turn": {"idle_min": 5}})
    check(got["action"] == thread.FINISH,
          "при отчёте правило не завершает: %s" % got["action"])

    print("4) когда владелец включает предел — молчащая нитка переносится")
    old = thread.STUCK_LIMIT_HOURS
    thread.STUCK_LIMIT_HOURS = 6
    try:
        got = thread.fallback_decision({"has_report": False,
                                        "last_turn": {"idle_min": 60 * 7}})
        check(got["action"] == thread.NEW_SESSION,
              "молчащая нитка не перенесена при включённом пределе: %s" % got["action"])
        check("паспорт" in str(got.get("instruction") or "").lower(),
              "инструкция переноса не упоминает паспорт: %s" % got.get("instruction"))
        got = thread.fallback_decision({"has_report": False,
                                        "last_turn": {"idle_min": 10}})
        check(got["action"] == thread.CONTINUE,
              "нить перенесена раньше предела: %s" % got["action"])
    finally:
        thread.STUCK_LIMIT_HOURS = old

    print("5) секретов в паспорте нет (D-038)")
    secret = "Authorization: Bearer sk-abcdef0123456789abcdef"
    got = thread.redact("видишь токен? %s" % secret)
    check("sk-abcdef0123456789abcdef" not in got,
          "токен остался в тексте паспорта: %s" % got)
    check("видишь токон" not in got and "видишь токен" in got,
          "redact() съел не только секрет: %s" % got)
    got = thread.redact("api_key=supersecretvalue123")
    check("supersecretvalue123" not in got,
          "ключ остался в тексте паспорта: %s" % got)

    print("6) паспорт собирается с вырезанными секретами")
    passport = thread.build_passport(
        {"slug": "minigen", "folder": str(HERE)},
        {"session_id": "s-1", "title": "нитка", "workspace_dir": str(HERE)},
        {"done": "сделано: %s" % secret, "left": "осталось: %s" % secret,
         "criteria": "критерии", "task": "задача"})
    for field in ("done", "left"):
        check("sk-abcdef0123456789abcdef" not in str(passport.get(field) or ""),
              "в паспорте (%s) остался секрет: %s" % (field, passport.get(field)))

    if failures:
        print("❌ приёмка пробелов #172: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка пробелов #172: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
