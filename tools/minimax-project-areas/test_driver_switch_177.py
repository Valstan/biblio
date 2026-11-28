# -*- coding: utf-8 -*-
"""Приёмка: переключатели в списке областей (промт КАРМАНА #177).

Зачитка владельца (27.11): «В списке областей МиниГена колонки „Режим“ и
„Водитель“ конкурируют: режим должен стать переключателем из двух небольших
кнопок (ручной/автомат) с видной включённой, а водитель — узкой колонкой сразу
за ним с тремя понятными вариантами: внешнее управление, управление МиниГеном
и оператор».

⚠️ ПОЗЖЕ В ТОТ ЖЕ ДЕНЬ (заказ владельца 27.11): «Водитель» — тоже **две
небольшие кнопки**, как у «Режима», и положений **два**: **«Миниген»** и
**«Оператор»**. «Внешнее управление» и «Оператор» — одно и то же (управление
внешнее; оператор или другой агент — без разницы). В **ядре** значение
`manager` осталось — это ответ **правила** («если в области работает
Управляющий — водит он»), и правило обязано уметь его назвать.

Проверяем на живом коде (окно + ядро), а не глазами:

1. **словарь один и тот же в окне и в ядре**: в переключателе **ровно два**
   положения — «Миниген» и «Оператор», и оба значения ядро понимает. «По
   правилу» в переключателе **нет**: это правило ядра для области, где водитель
   не выбран явно;
2. **«Режима» больше нет** (промт КАРМАНА #182, 27.11): ручного режима в
   «МиниГене» не осталось — колонка «Режим» убрана вместе со своим модулем,
   и выключить автомат аккаунтов **нечем**;
3. **колонки переименованы**: бывшая «Водитель» — «Сессии», бывшие «Сессии» —
   «Состояние»; ячейки строки идут по тому же списку колонок, что и шапки, —
   колонка не может «уехать»; кнопка «Новый вход» осталась запасным ручным
   путём на случай сбоя автоматики;
4. **«Внешнее управление»** (ответ правила): сессиями водит внешний агент,
   автомат в сессии **не вмешивается**, а в журнале и в отчёте пишет словами,
   что действие не выполнялось;
5. **«Оператор»**: никто не вмешивается в сессии;
6. **«Управление МиниГеном»** (кнопка «Миниген»): завершение работы
   определяется **по выводу сессии** (вывод идёт в факты к встроенной
   нейросети), а решение NN даёт цепочку «команда завершения → проверка
   закрытия → архив → новая сессия с продолжением нитки»; при незавершённой
   работе сессия продолжается;
7. **зависшая сессия** распознаётся, предпринимается попытка продолжения, а
   решение и причина пишутся в журнал; реакция агента разбирается, и попытки
   ограничены;
8. **на смену аккаунта уходит ровно одно «Продолжай»** (сторож сессий), а
   автомат аккаунтов в сессию не пишет ничего;
9. **один водитель на проект**: вторая сессия не поднимается поверх живой,
   порядок «сначала архив старой, потом новая».

Запуск: python3 tools/minimax-project-areas/test_driver_switch_177.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
GUI = HERE.parent / "minigen-gui"
TABS = GUI / "src" / "renderer" / "src" / "tabs"

sys.path.insert(0, str(HERE))

# Общая оснастка (настоящая база рантайма во временном каталоге) — из приёмки
# #174: свой велосипед здесь означал бы два разных «живых» кода.
from test_session_driver_rules import Recorder, load_watcher, make_area  # noqa: E402

MANAGER_TITLE = "ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — промт КАРМАНА 172"


# ─────────────────────────────────────────────── разбор файлов окна

def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def ts_driver_values(text: str) -> list[str]:
    """Значения переключателя водителя из окна (`DRIVER_VALUES`).

    ⚠️ Регулярка смотрит именно на `DRIVER_VALUES`, а не на что-либо с таким
    же хвостом: словарь **ядра** в окне назван `CORE_DRIVER_VALUES` — и он
    другой (три значения ядра + «по правилу»).
    """
    m = re.search(r"(?<![A-Za-z_])DRIVER_VALUES\s*=\s*\[([^\]]*)\]", text)
    if not m:
        return []
    return re.findall(r"'([^']+)'", m.group(1))


def ts_driver_labels(text: str) -> dict:
    """Подписи водителя из окна (`DRIVER_LABELS`)."""
    m = re.search(r"DRIVER_LABELS\s*:[^\n=]*=\s*\{(.*?)\n\}", text, re.S)
    if not m:
        return {}
    return dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", m.group(1)))


def ts_area_columns(text: str) -> list[str]:
    """Порядок колонок списка областей из окна (`AREA_COLUMNS`)."""
    m = re.search(r"AREA_COLUMNS\s*:\s*AreaColumn\[\]\s*=\s*\[(.*?)\n\]", text, re.S)
    if not m:
        return []
    return re.findall(r"label:\s*'([^']*)'", m.group(1))


def ts_mode_buttons(text: str) -> list[str]:
    """Подписи кнопок «Режима» из окна (`MODE_BUTTONS`).

    ⚠️ Промт КАРМАНА #182: ручного режима больше нет, модуля `areas-mode.ts`
    в коде нет ⇒ список пуст. Проверка живёт, чтобы поймать возвращение
    колонки «Режим».
    """
    m = re.search(r"MODE_BUTTONS\s*:[^\n=]*=\s*\[(.*?)\n\]", text, re.S)
    if not m:
        return []
    return re.findall(r"label:\s*'([^']*)'", m.group(1))


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    # ── 1) словарь: окно и ядро говорят одно и то же ───────────────────────
    print("1) водитель: в окне два положения, ядро знает все значения")
    driver_view = read(TABS / "driver-view.ts")
    core = None
    with __import__("tempfile").TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        passport_file = root / "thread-passports.json"
        watcher = load_watcher(passport_file)
        core = watcher.core
        # Настоящие функции — до подмен: проверка «вторая сессия не
        # поднимается поверх живой» должна идти по живому коду.
        real_new_session = watcher.new_session
        window_values = ts_driver_values(driver_view)
        # ⚠️ ЗАКАЗ ВЛАДЕЛЬЦА 27.11: положений переключателя **два** —
        # «Миниген» и «Оператор», в этом порядке. «Внешнее управление» и
        # «Оператор» — одно и то же положение (управление внешнее; оператор
        # или другой агент — без разницы).
        check(window_values == [core.DRIVER_AUTOMATON, core.DRIVER_NONE],
              "в окне положения %s, а ждём «Миниген» и «Оператор» (%s, %s)"
              % (window_values, core.DRIVER_AUTOMATON, core.DRIVER_NONE))
        window_labels = ts_driver_labels(driver_view)
        check(window_labels.get(core.DRIVER_AUTOMATON) == "Миниген",
              "водитель МиниГена назван не так: %s"
              % window_labels.get(core.DRIVER_AUTOMATON))
        check(window_labels.get(core.DRIVER_NONE) == "Оператор",
              "водитель оператора назван не так: %s"
              % window_labels.get(core.DRIVER_NONE))
        # «Внешнее управление» в переключателе больше не предлагается: для
        # МиниГена это то же, что «Оператор».
        check(core.DRIVER_MANAGER not in window_values,
              "«Внешнее управление» снова пункт переключателя — оно того же, "
              "что «Оператор»")
        # «по правилу» — не пункт переключателя (владелец просил без него).
        check(core.DRIVER_AUTO not in window_values,
              "«по правилу» снова пункт переключателя")
        check(core.DRIVER_AUTO in core.DRIVERS,
              "ядро перестало понимать «по правилу» (правило по умолчанию)")
        # В ядре `manager` остался — это ответ правила, а не положение.
        check(core.DRIVER_MANAGER in core.DRIVERS,
              "ядро перестало знать «Внешнее управление» (ответ правила)")
        check(core.DRIVER_SWITCH_VALUES == tuple(window_values),
              "ядро и окно разошлись в наборе положений водителя: %s / %s"
              % (core.DRIVER_SWITCH_VALUES, window_values))

        # ── 2) «Режима» больше нет (промт КАРМАНА #182) ───────────────────
        print("2) «Режима» нет: ручного режима не осталось")
        mode_src = read(TABS / "areas-mode.ts") if (TABS / "areas-mode.ts").is_file() else ""
        tab_src = read(TABS / "AreasTab.tsx")
        check(not mode_src, "модуль кнопок «Режима» снова в коде")
        check("MODE_BUTTONS" not in tab_src,
              "ячейка «Режима» снова рисуется из списка кнопок")
        check("areas-mode" not in tab_src, "окно снова тянет модуль «Режима»")
        check("setAutoheal" not in tab_src and "setAutoheal" not in tab_src,
              "в окне снова есть путь, отключающий автомат аккаунтов")
        api_src = read(GUI / "src" / "renderer" / "src" / "api.ts")
        check("setAutoheal" not in api_src,
              "в api окна снова есть вызов areas.set_autoheal")

        # ── 3) колонки: «Сессии» (бывшая «Водитель») и «Состояние» ────────
        print("3) «Сессии» и «Состояние» — колонки после переименования")
        sort_src = read(TABS / "areas-sort.ts")
        columns = ts_area_columns(sort_src)
        check("Режим" not in columns, "колонка «Режим» вернулась: %s" % columns)
        check("Сессии" in columns, "колонки «Сессии» нет: %s" % columns)
        check("Состояние" in columns, "колонки «Состояние» нет: %s" % columns)
        if "Сессии" in columns and "Состояние" in columns:
            check(columns.index("Сессии") < columns.index("Состояние"),
                  "«Сессии» и «Состояние» перепутаны местами: %s" % columns)
        check("AREA_COLUMNS.map((column) => (" in tab_src,
              "ячейки строки не идут по списку колонок — колонка может уехать")
        check("col-${column.key}" in tab_src and "driver-now" in tab_src,
              "у колонки водителя нет своих узких стилей")
        styles = read(GUI / "src" / "renderer" / "src" / "styles.css")
        check("td.col-driver" in styles and "max-width" in styles,
              "колонка водителя не ограничена по ширине")
        check(".driver-now" in styles and "text-overflow: ellipsis" in styles,
              "строка «сейчас: …» не обрезана — колонку раздувает")
        # Кнопка ручного входа осталась — запасной путь на случай сбоя
        # автоматики.
        check("Новый вход" in tab_src and "Запасной ручной путь" in tab_src,
              "кнопка «Новый вход» пропала или не подписана как запасной путь")

        # ── 4) «Внешнее управление»: автомат не вмешивается, но пишет ───────
        print("4) «Внешнее управление» — действие не выполняется, но видно")
        rec = Recorder()
        rec.install(watcher)
        area_dir = make_area(root, [
            {"id": "sess-work", "title": "нить владельца"},
        ])
        folder = root / "project"
        folder.mkdir(parents=True, exist_ok=True)
        area = {"slug": "test", "dir": area_dir, "folder": str(folder),
                "title": "тест", "driver": core.DRIVER_MANAGER}
        verdicts = watcher.survey(area, {})
        actions = watcher.act(area, verdicts, {})
        work = next((a for a in actions if a.get("session") == "sess-work"), {})
        check("не выполнялось" in (work.get("action") or ""),
              "действие не помечено как невыполненное: %s" % work)
        check(work.get("driver") == core.DRIVER_MANAGER,
              "в действии нет водителя: %s" % work)
        check(not rec.sent, "сторож всё же что-то отправил в сессию: %s" % rec.sent)
        check(not rec.archived, "сторож всё же архивировал сессию: %s" % rec.archived)
        lines = [text for _l, _k, text, _a in rec.reports]
        check(any("водитель — %s" % core.driver_title(core.DRIVER_MANAGER) in text
                  and "не выполнялось" in text for text in lines),
              "в отчёте нет «водитель — %s, действие не выполнялось»: %s"
              % (core.driver_title(core.DRIVER_MANAGER), lines))

        # ── 5) «Оператор»: никто не вмешивается ─────────────────────────────
        print("5) «Оператор» — в сессии никто не вмешивается")
        rec_op = Recorder()
        rec_op.install(watcher)
        area_op = dict(area, driver=core.DRIVER_NONE)
        actions_op = watcher.act(area_op, verdicts, {})
        check(all(a.get("action") != "водитель — %s, действие не выполнялось" % ""
                  for a in actions_op), "странные действия оператора: %s" % actions_op)
        check(not rec_op.sent, "при операторе в сессию ушло сообщение: %s" % rec_op.sent)
        check(not rec_op.archived, "при операторе сессия архивирована: %s" % rec_op.archived)

        # ── 6) «Управление МиниГеном»: завершение по выводу → цепочка ──────
        print("6) «Управление МиниГеном» — завершение по выводу и цепочка")
        area_ok = make_area(root, [
            {"id": "sess-done", "title": "нить владельца", "final": "ИТОГ: работа сделана, всё готово"},
        ])
        area_auto = {"slug": "test", "dir": area_ok, "folder": str(folder),
                     "title": "тест", "driver": core.DRIVER_AUTOMATON}
        verdicts_ok = watcher.survey(area_auto, {})
        done = next((v for v in verdicts_ok if v["session_id"] == "sess-done"), {})
        check(done.get("verdict") == "готова",
              "вердикт завершённой сессии не «готова»: %s" % done)
        check(bool(done.get("has_report")),
              "по выводу сессии не видно, что работа выполнена: %s" % done)

        rec2 = Recorder()
        rec2.install(watcher)
        asked: list[dict] = []
        watcher.thread.decide = lambda facts, *a, **k: (
            asked.append(facts) or
            {"ok": True, "action": watcher.thread.NEW_SESSION,
             "instruction": "Продолжи нитку.", "reason": "нитка не закончена",
             "source": "Управляющий"})
        order: list[str] = []
        real_new = watcher.new_session
        real_archive = watcher.archive_session

        def order_new(area, prompt, force=False, folder="", by_decision=False):
            order.append("new")
            return {"ok": True, "why": "новая сессия пошла"}

        def order_archive(area_dir, session_id):
            order.append("archive")
            return {"ok": True, "why": "архивирована"}

        watcher.new_session = order_new
        watcher.archive_session = order_archive
        watcher.wait_closed = lambda *a, **k: True
        watcher.send_to_session = lambda *a, **k: {"ok": True, "why": "ход пошёл"}
        watcher.thread.open_prs = lambda folder, branch: []
        try:
            actions_ok = watcher.act(area_auto, verdicts_ok, {})
        finally:
            watcher.new_session = real_new
            watcher.archive_session = real_archive
        check(bool(asked), "встроенную нейросеть не спросили о завершённой сессии")
        if asked:
            facts = asked[0]
            check(bool(facts.get("last_text")),
                  "вывод сессии не попал в факты — нейросеть его не видит: %s"
                  % sorted(facts.keys()))
            check(facts.get("verdict") == "готова",
                  "в фактах нет вердикта о завершении: %s" % facts.get("verdict"))
        act_done = next((a for a in actions_ok if a.get("session") == "sess-done"), {})
        check(bool(act_done.get("report")), "решение по завершённой сессии не попало в отчёт")
        report = act_done.get("report") or {}
        check(order == ["archive", "new"],
              "порядок «сначала архив старой, потом новая» нарушен: %s" % order)
        check(report.get("ok"), "перенос нитки не удался: %s" % report.get("why"))
        check("archive" in json.dumps(report, ensure_ascii=False, default=str),
              "в отчёте нет архива: %s" % report.get("steps"))

        # ⚠️ ПРОМТ КАРМАНА #184 (28.11) — канон обновлён: в **завершённой**
        # сессии «продолжать» на месте неисполнимо (сессия закрыта), поэтому
        # решение приводится к переносу нитки: паспорт → архив → новая сессия
        # с паспортом и «продолжи нитку». Раньше (промт #177) здесь ожидался
        # мягкий «Продолжай» в уже завершённую сессию — ровно то, из-за чего
        # МиниГен 27–28.11 слал «Продолжай» в сессию, которая уже сказала
        # «работа закончена» (живой случай промта #184). Незавершённая работа
        # продолжается, а не закрывается, — это проверяется ниже на зависшей
        # сессии и в приёмке #184.
        asked.clear()
        watcher.thread.decide = lambda facts, *a, **k: (
            asked.append(facts) or
            {"ok": True, "action": watcher.thread.CONTINUE,
             "instruction": "Доделай нитку.", "reason": "работа не закончена",
             "source": "Управляющий"})
        rec3 = Recorder()
        rec3.install(watcher)
        actions_busy = watcher.act(area_auto, verdicts_ok, {})
        act_busy = next((a for a in actions_busy if a.get("session") == "sess-done"), {})
        check(bool(act_busy.get("report")), "решение по сессии не попало в отчёт: %s" % act_busy)
        check(bool(asked), "по завершённой сессии нейросеть не спросили")
        check(rec3.archived == ["sess-done"],
              "завершённую сессию не архивировали: %s" % rec3.archived)
        nudges_done = [text for sid, text in rec3.sent if sid == "sess-done"]
        check(not nudges_done,
              "в завершённую сессию ушла команда: %s" % nudges_done)

        # ── 7) зависшая сессия: попытка продолжения и журнал ─────────────────
        print("7) зависшая сессия — попытка продолжения, решение в журнале")
        area_stuck = make_area(root, [{"id": "sess-stuck", "title": "нить владельца",
                                       "live": True}])
        area_stuck_d = {"slug": "test", "dir": area_stuck, "folder": str(folder),
                        "title": "тест", "driver": core.DRIVER_AUTOMATON}
        rec4 = Recorder()
        rec4.install(watcher)
        asked.clear()
        watcher.thread.decide = lambda facts, *a, **k: (
            asked.append(facts) or
            {"ok": True, "action": watcher.thread.CONTINUE,
             "instruction": "Продолжай нитку.", "reason": "ход завис",
             "source": "Управляющий"})
        state: dict = {}
        verdicts_stuck = watcher.survey(area_stuck_d, state)
        stuck = next((v for v in verdicts_stuck if v["session_id"] == "sess-stuck"), {})
        check(stuck.get("verdict") == "ход завис",
              "зависшая сессия не распознана: %s" % stuck)
        actions_stuck = watcher.act(area_stuck_d, verdicts_stuck, state)
        act_stuck = next((a for a in actions_stuck if a.get("session") == "sess-stuck"), {})
        check(bool(act_stuck), "по зависшей сессии нет никакого действия: %s" % actions_stuck)
        check(bool(asked), "по зависшей сессии нейросеть не спросили")
        logs = [text for _l, _k, text, _a in rec4.reports]
        check(any("зависшая сессия" in text for text in logs),
              "в отчёте нет слов о зависшей сессии: %s" % logs)
        check(any("не выполнялось" not in text and "зависшая сессия" in text
                  for text in logs), "в отчёте нет решения по зависшей сессии: %s" % logs)
        # ⚠️ Промт #184: незавершённая работа — сессия **продолжается**, а не
        # закрывается (и канон из блока 6 перенесён сюда: завершённая сессия
        # переносится, незавершённая — продолжается).
        check(not [s for s in rec4.archived if s == "sess-stuck"],
              "зависшую сессию архивировали вместо продолжения: %s" % rec4.archived)
        nudges_stuck = [text for sid, text in rec4.sent if sid == "sess-stuck"]
        check(bool(nudges_stuck), "зависшую сессию не продолжили: %s" % rec4.sent)
        check(not any(watcher.CLOSE_COMMAND in text for text in nudges_stuck),
              "зависшую сессию закрыли вместо продолжения: %s" % nudges_stuck)
        # попытка продолжения — не бесконечная: у попыток есть предел
        attempts = int((state.get("stuck") or {}).get("sess-stuck", {}).get("attempt") or 0)
        check(attempts >= 1, "попытка продолжения не записана: %s" % state.get("stuck"))
        limit = int(getattr(watcher, "STUCK_ATTEMPT_LIMIT", 0) or 0)
        check(limit > 0, "у зависшей сессии нет предела попыток")
        # реакция агента разбирается: сессия ожила — «работа идёт дальше»
        rec5 = Recorder()
        rec5.install(watcher)
        woke = dict(verdicts_stuck[0], verdict="работает",
                    last_text="продолжаю работу по нитке")
        actions_woke = watcher.act(area_stuck_d, [woke], state)
        act_woke = next((a for a in actions_woke if a.get("session") == "sess-stuck"), {})
        check("работа идёт дальше" in (act_woke.get("action") or ""),
              "реакция агента не разобрана: %s" % act_woke)

        # ── 8) на смену аккаунта — ровно одно «Продолжай» ──────────────────
        print("8) на смену аккаунта — ровно одно «Продолжай»")
        area_bal = make_area(root, [{"id": "sess-bal", "title": "нить владельца"}])
        area_bal_d = {"slug": "test", "dir": area_bal, "folder": str(folder),
                      "title": "тест", "driver": core.DRIVER_AUTOMATON}
        rec6 = Recorder()
        rec6.install(watcher)
        bal_state: dict = {}
        bal_verdicts = [{"session_id": "sess-bal", "title": "нить владельца",
                         "verdict": "кончились очки", "reason": "отказ провайдера 50110",
                         "updated_at_ms": 1, "last_turn": {"completed_at_ms": 1},
                         "fill": 10, "workspace_dir": str(folder)}]
        # лечащий обновил аккаунт — и только один раз
        watcher.core.AUTHEAL_STATE = root / "autheal-state.json"
        watcher.core.AUTHEAL_STATE.write_text(json.dumps(
            {"last_heal": {"test": time.time()}}), encoding="utf-8")
        watcher.act(area_bal_d, bal_verdicts, bal_state)
        watcher.act(area_bal_d, bal_verdicts, bal_state)
        continues = [text for sid, text in rec6.sent if sid == "sess-bal"]
        check(len(continues) == 1,
              "на смену аккаунта ушло «Продолжай» %d раз (ждали ровно одно): %s"
              % (len(continues), continues))

        # ── 9) один водитель на проект: вторая сессия не поверх живой ──────
        print("9) один водитель на проект: вторая сессия не поднимается")
        area_live = make_area(root, [{"id": "sess-live", "title": "нить владельца",
                                      "live": True}])
        area_live_d = {"slug": "test", "dir": area_live, "folder": str(folder),
                       "title": "тест", "driver": core.DRIVER_AUTOMATON}
        launched: list[list] = []

        class FakeProc:
            pid = 4242

            def poll(self):
                return None

        old_mcode = core.mcode_run
        core.mcode_run = lambda args, area_dir, **k: (
            launched.append(list(args)) or FakeProc())
        try:
            res = real_new_session(area_live_d, "Продолжи нитку", folder=str(folder))
            check(not res.get("ok"), "вторая сессия поднялась при живой первой: %s" % res)
            check(not launched, "при живой сессии всё равно запустили новую: %s" % launched)
        finally:
            core.mcode_run = old_mcode

    if failures:
        print("❌ приёмка переключателей в списке областей (#177): FAIL (%d)"
              % len(failures))
        return 1
    print("✅ приёмка переключателей в списке областей (#177): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
