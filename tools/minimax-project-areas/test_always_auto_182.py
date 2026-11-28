# -*- coding: utf-8 -*-
r"""Приёмка: «Всегда автомат» — колонка «Режим» убрана, колонки переименованы
(промт КАРМАНА #182).

Зачитка владельца (27.11): ручного режима в «МиниГене» больше не нужно.
Колонка «Режим» с переключателем «Автомат / Вручную» убирается совсем, МиниГен
всегда сам перелогинивает аккаунты, колонка «Водитель» становится «Сессиями»,
а бывшие «Сессии» — «Состоянием».

Проверяем на живом коде (окно + ядро + мост), а не глазами:

1. **в списке областей нет колонки «Режим»** — и нет её модуля с кнопками
   (`areas-mode.ts`), и нет её стилей, и нет сортировки по ней;
2. **из окна нельзя отключить автомат** — ни кнопки, ни вызова моста;
3. **автомат включён всегда**: ядро отдаёт `autoheal: true` даже для области,
   у которой в сохранённом состоянии автомат был выключен; новая область
   стартует в автомате; сохранённое состояние приводится к автомату
   (решение владельца: при обновлении автомат включается принудительно);
4. **мост не даёт выключить автомат**: `areas.set_autoheal {autoheal: false}`
   возвращает отказ, а в конфиге остаётся `autoheal: true`; в коде нет ни
   одного места, пишущего `autoheal = false`;
5. **колонки переименованы**: «Сессии» (бывшая «Водитель») перед «Состоянием»
   (бывшие «Сессии»), подсказка сортировки перечисляет ровно эти столбцы;
6. **«Новый вход» остался** — запасной ручной путь, и это сказано в подсказке;
7. **словарь «водитель» не тронут**: в мосте, в отчёте о лечении и в журнале
   по-прежнему «водитель — …» (это разные вещи, чем заголовок колонки).

⚠️ Конфиг областей — **локальная вещь компьютера** (`C:\Users\valstan\bin`),
поэтому приёмка работает на **временном** конфиге и никогда не трогает боевой.

Запуск: python3 tools/minimax-project-areas/test_always_auto_182.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GUI = HERE.parent / "minigen-gui"
TABS = GUI / "src" / "renderer" / "src" / "tabs"
BRIDGE = GUI / "bridge"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BRIDGE))


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def ts_area_columns(text: str) -> list[str]:
    """Порядок колонок списка областей из окна (`AREA_COLUMNS`)."""
    m = re.search(r"AREA_COLUMNS\s*:\s*AreaColumn\[\]\s*=\s*\[(.*?)\n\]", text, re.S)
    if not m:
        return []
    return re.findall(r"label:\s*'([^']*)'", m.group(1))


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    # ⚠️ Приёмка проверяет **код репозитория**: боевой код на машине доезжает
    # только раскаткой, а проверка должна быть зелёной уже сейчас (и в CI,
    # где боевой папки нет вовсе).
    os.environ["MINIGEN_DAEMON_DIR"] = str(HERE)

    # ⚠️ Заказ владельца 28.11: «Области» и «Агенты» — одна вкладка,
    # файл `AreasTab.tsx` удалён ⇒ смотрим объединённую «Агенты».
    tab_src = read(TABS / "AgentsTab.tsx")
    sort_src = read(TABS / "areas-sort.ts")
    styles_src = read(GUI / "src" / "renderer" / "src" / "styles.css")
    api_ts = read(GUI / "src" / "renderer" / "src" / "api.ts")

    # ── 1) колонки «Режим» нет ────────────────────────────────────────────
    print("1) колонки списка областей: «Режима» нет, переименования на месте")
    columns = ts_area_columns(sort_src)
    check("Режим" not in columns, "колонка «Режим» вернулась: %s" % columns)
    check("Сессии" in columns, "колонки «Сессии» нет: %s" % columns)
    check("Состояние" in columns, "колонки «Состояние» нет: %s" % columns)
    if "Сессии" in columns and "Состояние" in columns:
        check(columns.index("Сессии") < columns.index("Состояние"),
              "«Сессии» и «Состояние» перепутаны местами: %s" % columns)
    check("Водитель" not in columns,
          "прежнее имя колонки «Водитель» вернулось на экран: %s" % columns)
    check("case 'mode'" not in sort_src, "сортировка по «Режиму» осталась в коде")
    check("'mode'" not in sort_src.split("export interface")[0],
          "ключ сортировки «mode» остался в areas-sort.ts")

    # ── 2) модуля с кнопками режима нет ───────────────────────────────────
    print("2) модуль кнопок «Режима» удалён, стилей его тоже")
    check(not (TABS / "areas-mode.ts").is_file(),
          "модуль кнопок «Режима» снова в коде: %s" % (TABS / "areas-mode.ts"))
    check("MODE_BUTTONS" not in tab_src, "окно снова рисует кнопки «Режима»")
    check("areas-mode" not in tab_src, "окно снова тянет модуль «Режима»")
    check("mode-switch" not in styles_src, "стили кнопок «Режима» остались в коде")
    check("mode-btn" not in styles_src, "стили кнопок «Режима» остались в коде")

    # ── 3) из окна нельзя отключить автомат ────────────────────────────────
    print("3) в окне нет пути, отключающего автомат")
    check("setAutoheal" not in api_ts, "в api окна снова есть вызов setAutoheal")
    check("areas.set_autoheal" not in api_ts,
          "в api окна снова есть команда areas.set_autoheal")
    check("setAutoheal" not in tab_src, "в окне снова есть вызов setAutoheal")

    # ── 4) «Новый вход» — запасной ручной путь ────────────────────────────
    print("4) «Новый вход» на месте и подписан как запасной ручной путь")
    check("Новый вход" in tab_src, "кнопка «Новый вход» пропала из окна")
    check("Запасной ручной путь" in tab_src,
          "в подсказке «Новый вход» не сказано, что это запасной ручной путь")

    # ── 5) ядро и мост: автомат включён всегда ─────────────────────────────
    print("5) ядро и мост: автомат аккаунтов включён всегда")
    # Мост и состояние — **одни и те же** модули, что и у боевой программы
    # (иначе приёмка проверяла бы разные экземпляры кода).
    import minigen_state as state        # noqa: E402
    import minigen_bridge as bridge       # noqa: E402

    mods = state.load_daemons()
    core = mods["core"]
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        config = root / "mcode-project-areas.json"
        config.write_text(json.dumps({
            "projects": [
                {"slug": "brain", "title": "МОЗГ", "autoheal": False},
                {"slug": "karman", "title": "КАРМАН", "autoheal": True},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        old_config = core.CONFIG
        old_root = state.AREAS_ROOT
        # Журнал и «ящик отчётов» — тоже боевые файлы машины: приёмка не
        # должна оставлять в них записи про несуществующие области.
        old_journal = core.JOURNAL
        old_reports = core.REPORTS
        core.CONFIG = config
        core.JOURNAL = root / "journal.log"
        core.REPORTS = root / "reports.jsonl"
        state.AREAS_ROOT = root / "areas"
        try:
            # 5а) ядро отдаёт автомат включённым даже для ручной области
            by_slug = {a["slug"]: a for a in core.areas()}
            check(by_slug.get("brain", {}).get("autoheal") is True,
                  "область с выключенным автоматом видна как ручная: %s"
                  % by_slug.get("brain"))
            check(by_slug.get("karman", {}).get("autoheal") is True,
                  "автомат выключен у области, у которой он был включён")
            check(getattr(core, "AUTOHEAL_ALWAYS", None) is True,
                  "в ядре нет константы AUTOHEAL_ALWAYS = True")

            # 5б) сохранённое состояние приводится к автомату
            fixed = state.enforce_autoheal()
            check("brain" in fixed,
                  "приведение к автомату не тронуло область с выключенным "
                  "автоматом: %s" % fixed)
            saved = json.loads(config.read_text(encoding="utf-8"))
            saved_auto = {p["slug"]: bool(p.get("autoheal")) for p in saved["projects"]}
            check(all(saved_auto.values()),
                  "в сохранённом состоянии остался выключенный автомат: %s"
                  % saved_auto)
            check(not state.enforce_autoheal(),
                  "приведение к автомату повторно что-то меняет (должно быть "
                  "идемпотентно)")

            # 5в) мост не даёт выключить автомат
            # ⚠️ Перед попыткой кладём в конфиг область с **выключенным**
            # автоматом (как если бы миграция её не тронула): тогда видно,
            # что мост не пишет `false` и не оставляет область ручной.
            cfg = json.loads(config.read_text(encoding="utf-8"))
            for project in cfg["projects"]:
                if project["slug"] == "brain":
                    project["autoheal"] = False
            config.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

            # Подделка вместо моста: команде нужен только `core` (для журнала).
            fake = type("FakeBridge", (), {})()
            fake.core = core
            fake.log = lambda msg: None

            off = bridge.Bridge.cmd_set_autoheal(
                fake, {"slug": "brain", "autoheal": False})
            check("error" in off, "мост согласился выключить автомат: %s" % off)
            after = json.loads(config.read_text(encoding="utf-8"))
            brain = next(p for p in after["projects"] if p["slug"] == "brain")
            check(brain.get("autoheal") is True,
                  "после попытки выключить автомат в конфиге не автомат: %s"
                  % brain)

            # «включить» — можно (область и так в автомате)
            on = bridge.Bridge.cmd_set_autoheal(
                fake, {"slug": "brain", "autoheal": True})
            check(on.get("ok"), "включение автомата не удалось: %s" % on)
            check(on.get("autoheal") is True, "автомат не включился: %s" % on)

            # список областей моста отдаёт автомат включённым
            listed = state.areas_state(mods)
            check(all(item.get("autoheal") for item in listed),
                  "в списке областей есть выключенный автомат: %s" % listed)
        finally:
            core.CONFIG = old_config
            core.JOURNAL = old_journal
            core.REPORTS = old_reports
            state.AREAS_ROOT = old_root

    # ── 6) новая область стартует в автомате ──────────────────────────────
    print("6) новая область заводится сразу в автомате")
    bridge_src = read(BRIDGE / "minigen_bridge.py")
    m = re.search(r"projects\.append\(\{(.*?)\n\s*\}\)", bridge_src, re.S)
    check(bool(m), "не нашли, где мост заводит область")
    if m:
        check('"autoheal": True' in m.group(1),
              "новая область заводится не в автомате: %s" % m.group(1)[:200])

    # ── 7) в коде нет ни одного места, пишущего выключенный автомат ───────
    print("7) в мосте и в состоянии нет пути, пишущего autoheal = false")
    for name, source in (("minigen_bridge.py", bridge_src),
                          ("minigen_state.py", read(BRIDGE / "minigen_state.py")),
                          ("minigen_core.py", read(HERE / "minigen_core.py"))):
        for hit in re.finditer(r'"autoheal"\]\s*=\s*([^\n]+)', source):
            check(hit.group(1).strip().startswith("True"),
                  "в %s автомат записывается не как True: %s"
                  % (name, hit.group(0).strip()))
        check(not re.search(r'autoheal["\']?\s*[=:]\s*False', source),
              "в %s есть место, где автомат выключается" % name)

    # ── 8) словарь «водитель» не тронут ───────────────────────────────────
    print("8) в отчёте и в журнале по-прежнему «водитель»")
    watcher = read(HERE / "minimax-sessionwatch.py")
    check("водитель — %s" in watcher,
          "в журнале и отчёте больше нет слов «водитель — …»")
    check("водитель сессий" in watcher or "водителем" in watcher,
          "в стороже сессий нет слова «водитель»")
    check(hasattr(core, "DRIVER_TITLES"), "в ядре пропал словарь водителей")
    check(core.driver_title(core.DRIVER_MANAGER) == "Внешнее управление",
          "ядро называет водителя иначе: %s" % core.driver_title(core.DRIVER_MANAGER))

    if failures:
        print("❌ приёмка «всегда автомат» (#182): FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка «всегда автомат» (#182): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
