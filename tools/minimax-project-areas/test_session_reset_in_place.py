# -*- coding: utf-8 -*-
"""Приёмка: сброс сессии НА МЕСТЕ (заказ владельца 24.11).

Владелец: «может не начинать новую сессию, а просто тут же, в этой сессии
текущей, делать очистку/сброс сессии и начинать с начала? Чтобы МиниГен умел
это делать сам автоматически, если сессия приближается к лимиту контекста или
только что произошла компактизация».

Проверяем:

1. **порог компактизации ставится нативно** — `contextManagement.
   compactionTriggerAt` в `config.yaml` области (рантайм сам сожмёт контекст
   внутри сессии); остальной конфиг не портится (в нём есть YAML-якоря);
2. **сторож умеет сброс на месте**: правильно завершить → очистить →
   продолжить ТУ ЖЕ сессию (новая не открывается);
3. **ротация осталась откатом** — если сброс не удался, открывается новая
   сессия (как раньше);
4. **повторного сброса подряд не будет** (кулдаун): пик заполнения контекста
   после сброса остаётся высоким, и без кулдауна сторож гонял бы цикл.

Запуск: python3 tools/minimax-project-areas/test_session_reset_in_place.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

CONFIG = """\
logLevel: info
provider:
  minimax:
    name: MiniMax
    options:
      baseURL: https://example.invalid/v1
    models:
      MiniMax-M3.1-Flash:
        limit:
          context: 512000
          output: 128000
    model_order:
      - MiniMax-M3.1-Flash
defaultModel: minimax/MiniMax-M3.1-Flash
defaultModelContextWindow: 1000000
capabilities: &ref_0
  support_files_api: true
"""


def load_core():
    spec = importlib.util.spec_from_file_location(
        "core_reset_under_test", str(HERE / "minigen_core.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_watcher():
    spec = importlib.util.spec_from_file_location(
        "watcher_reset_under_test", str(HERE / "minimax-sessionwatch.py"))
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

    core = load_core()
    watcher = load_watcher()

    print("1) порог компактизации ставится нативно")
    with tempfile.TemporaryDirectory() as tmp:
        area = pathlib.Path(tmp) / "p-test"
        area.mkdir()
        cfg = area / "config.yaml"
        cfg.write_text(CONFIG, encoding="utf-8")
        res = core.set_compaction_trigger(area, 0.5)
        check(res.get("ok"), "порог не поставился: %s" % res.get("why"))
        text = cfg.read_text(encoding="utf-8")
        check("compactionTriggerAt: 0.5" in text,
              "в конфиге нет compactionTriggerAt:\n%s" % text)
        check("&ref_0" in text, "YAML-якорь сломан — конфиг переписывается неверно")
        check("defaultModelContextWindow: 1000000" in text, "остальное конфига затёрто")
        again = core.set_compaction_trigger(area, 0.5)
        check(again.get("ok") and not again.get("changed"), "повторная запись не идемпотентна")

    print("2) сторож умеет сброс на месте")
    check(hasattr(watcher, "reset_session"), "в стороже нет reset_session")
    check(hasattr(watcher, "RESET_COMMAND"), "в стороже нет RESET_COMMAND")
    check("В НЕЙ ЖЕ" in getattr(watcher, "RESET_COMMAND", ""),
          "команда завершения не говорит, что сессию продолжают в ней же")
    check(hasattr(watcher, "rotate_session"), "ротация-откат пропала")
    check(int(getattr(watcher, "RESET_COOLDOWN", 0)) > 0, "нет кулдауна на повторный сброс")

    print("3) ветка решения предпочитает сброс, а ротация — откат")
    src = (HERE / "minimax-sessionwatch.py").read_text(encoding="utf-8")
    branch = src[src.find('if v["verdict"] in ("скомпактилась"'):][:1500]
    check("reset_session(area, sess)" in branch,
          "ветка вердиктов не вызывает reset_session")
    check("rotate_session(area, sess)" in branch and "fallback" in branch,
          "в ветке нет отката на ротацию")
    check('state.setdefault("reset", {})' in branch, "нет отметки времени сброса")

    print("4) порог компактизации — постоянные 0.9 (решение владельца 24.11)")
    check(0 < float(getattr(watcher, "COMPACTION_TRIGGER", 0)) <= 1,
          "порог компактизации вне разумного диапазона: %s"
          % getattr(watcher, "COMPACTION_TRIGGER", None))
    check(float(getattr(watcher, "COMPACTION_TRIGGER", 0)) == 0.9,
          "порог компактизации не 0.9, а %s — решение владельца 24.11"
          % getattr(watcher, "COMPACTION_TRIGGER", None))
    check("COMPACTION_TRIGGER = 0.9" in src,
          "в коде сторожа порог не равен 0.9")

    if failures:
        print("❌ приёмка сброса на месте: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка сброса на месте: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
