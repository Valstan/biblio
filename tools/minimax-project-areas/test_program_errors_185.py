# -*- coding: utf-8 -*-
"""Приёмка: свои критические ошибки уходят «Управляющему» как задача (промт КАРМАНА #185).

Заказ владельца (зачитка 28.11): «МиниГен должен ловить свои критические ошибки и
отдавать их встроенному Управляющему как задачу: починить код программы и выпустить
обновление — и не досылать одну и ту же ошибку, пока Управляющий её чинит
(полчаса), но другую ошибку отправлять сразу».

Что проверяем (критерии приёмки промта #185):

1. **Критическая ошибка программы дошла до Управляющего и видна в его чате как
   задача**, а не как строчка в ленте отчётов.
2. **В задаче сказано, откуда сообщение**: «ошибка программы МиниГен» (чинить код
   и обновлять программу) либо «вывод сессии другого агента» (ответить агенту
   или управлять им).
3. **Одна и та же ошибка за полчаса уходит один раз; другая — сразу.**
4. **Видно, что ошибка «в работе»** и когда окно откроется снова.
5. **Порог «критической» ошибки**: сетевые сбои, отказ вендора и ошибки чужих
   машин в задачу **не** попадают.
6. **Доставка отчётов не сломана**: непрочитанные отчёты по-прежнему
   подкладываются в чат, а шум (опрос статуса, чтение отчётов) в задачу не
   попадает.
7. **Правка кода идёт в ветке своей нитки** (в задаче назван `code.branch` со
   своим worktree, в системном промпте — запрет трогать чужие деревья и
   `master`).
8. **Задача доводится до обновления**: в системном промпте есть цепочка
   «правка → PR → обновление», а обновление **пишет отчёт** «обновление вышло».
9. **Сторож падает** (приёмка падает), если одна и та же ошибка ушла дважды за
   полчаса или если в задаче не назван источник — проверено **временной
   правкой**: сломанная копия действительно отправляет повтор и действительно
   не называет источник (значит, проверка не проходит по неверной причине).

Живое состояние машины не трогаем: «ящик отчётов», состояние ошибок и история
чата — во временном каталоге.

Запуск: python3 tools/minimax-project-areas/test_program_errors_185.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
BRIDGE = HERE.parent / "minigen-gui" / "bridge"
GUI = HERE.parent / "minigen-gui"

if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import minigen_bridge as bridge_mod        # noqa: E402
import minigen_chat_store as chat_store    # noqa: E402
import minigen_errors as errors_mod        # noqa: E402
import minigen_state as state              # noqa: E402
import minigen_tools as tools              # noqa: E402


def load_core():
    """Боевое ядро из репозитория (как на машине, только из исходников)."""
    spec = importlib.util.spec_from_file_location(
        "mg_core_185_under_test", str(HERE / "minigen_core.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_errors(broken: str = ""):
    """Модуль ошибок — из репозитория или **сломанная копия**.

    `broken` — «временная правка»: что вырезать, чтобы приёмка **падала**.
    """
    source = (BRIDGE / "minigen_errors.py").read_text(encoding="utf-8")
    if broken == "window":
        # Ломаем окно «полчаса»: повтор той же ошибки уходит сразу.
        source = source.replace("if last and (now - last) < window:",
                                "if False:")
    elif broken == "source":
        # Ломаем различение источников: любая задача говорит «ошибка программы».
        source = source.replace("if source == SOURCE_AGENT:", "if False:")
    if broken and source == (BRIDGE / "minigen_errors.py").read_text(encoding="utf-8"):
        raise SystemExit("временная правка «%s» не применилась — код поменялся, "
                         "приёмку надо поправить" % broken)
    tmp = pathlib.Path(tempfile.gettempdir()) / (
        "minigen-errors-185-%s.py" % (broken or "ok"))
    tmp.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "minigen_errors_185_%s" % (broken or "ok"), str(tmp))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoreProxy:
    """Боевое ядро из репозитория — но без обращения к реестру машины."""

    def __init__(self, core) -> None:
        self._core = core
        self.written: list[tuple] = []

    def __getattr__(self, name):
        return getattr(self._core, name)

    def refresh_env(self) -> dict:
        return {}

    def report(self, level: str, kind: str, text: str, area: str = "",
               source: str = "") -> None:
        self.written.append((level, kind, text, area, source))
        self._core.report(level, kind, text, area=area, source=source)


class FakeAgent:
    """Модель: помнит, что ей отдали (подсказку хода)."""

    def __init__(self) -> None:
        self.note = ""
        self.task = ""

    def ask(self, history, task, area_slug="", model="", note="", blocks=None,
            council_note=""):
        self.task = task
        self.note = note
        return {"status": "done", "text": "ответ модели", "calls": [],
                "history": list(history)}

    def pending_snapshot(self):
        return None


def write_report(core, level: str, kind: str, text: str, ts: int,
                 area: str = "", source: str = "") -> None:
    """Запись в «ящик отчётов» — напрямую (время задано явно)."""
    record = {"ts": int(ts), "level": level, "kind": kind, "area": area,
              "source": source, "text": text}
    with core.REPORTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_bridge(core, tmp: pathlib.Path):
    """Мост без тяжёлой инициализации: только то, что нужно для проверки."""
    bridge = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
    bridge.core = core
    bridge.handlers = {}
    bridge.chat_history = []
    bridge.agent = FakeAgent()
    bridge._report_call = lambda cmd, result: None
    bridge._errors_module = errors_mod
    return bridge


def task_count(text: str) -> int:
    """Сколько задач в тексте (задача — с заголовком)."""
    return text.count(errors_mod.TASK_MARK)


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    saved_seen = bridge_mod.REPORTS_SEEN
    saved_history = chat_store.HISTORY_FILE
    saved_settings = state.read_settings
    saved_state_file = errors_mod.STATE_FILE

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        core = load_core()
        core.REPORTS = root / "reports.jsonl"
        core.JOURNAL = root / "journal.log"
        proxy = CoreProxy(core)
        bridge_mod.REPORTS_SEEN = root / "reports-seen.json"
        chat_store.HISTORY_FILE = root / "chat-history.json"
        errors_mod.STATE_FILE = root / "errors-state.json"
        state.read_settings = lambda: {}
        bridge = make_bridge(proxy, root)
        base = core.now_ms()

        # ── 1) Сбой программы доходит до Управляющего как задача ──
        print("1) критическая ошибка программы доходит до Управляющего как задача")
        write_report(core, "error", "crash", "мост не поднялся: FileNotFoundError",
                     core.now_ms() - 1000, source="minigen")
        # Сбой — это задача, а не строчка в ленте отчётов.
        feed = bridge._unread_reports()
        check("мост не поднялся" not in feed,
              "сбой программы попал в ленту отчётов — это не задача: %s" % feed[:200])
        # Видно в чате: задача — отдельная реплика и уходит в ход.
        bridge.cmd_chat_send({"text": "что случилось?"})
        tasks = bridge.agent.note
        check(task_count(tasks) == 1,
              "задача по сбою программы не дошла до Управляющего: %s" % tasks[:120])
        check("мост не поднялся" in tasks,
              "в задаче нет текста ошибки: %s" % tasks[:200])
        items = chat_store.load()
        blob = json.dumps(items, ensure_ascii=False)
        check("ЗАДАЧА УПРАВЛЯЮЩЕМУ" in blob,
              "задачи не видно в чате — она не дошла до «Управляющего»")

        # ── 2) Источник назван прямо ──
        print("2) в задаче назван источник: программа или вывод сессии агента")
        check("ошибка программы МиниГен" in tasks,
              "в задаче нет слов «ошибка программы МиниГен»: %s" % tasks[:200])
        check("починить код" in tasks and "выпустить обновление" in tasks,
              "в задаче нет указания починить код и выпустить обновление")
        write_report(core, "error", "crash", "ход агента упал: внутренняя ошибка",
                     core.now_ms(), area="brain2", source="agent")
        agent_task = bridge._program_tasks()
        check(task_count(agent_task) == 1,
              "задача по выводу сессии агента не дошла: %s" % agent_task[:120])
        check("вывод сессии другого агента" in agent_task,
              "в задаче нет слов «вывод сессии другого агента»: %s" % agent_task[:200])
        check("ответить агенту или управлять им" in agent_task,
              "в задаче нет указания ответить агенту или управлять им")
        check("починить код" not in agent_task,
              "выводу сессии агента приписана правка кода: %s" % agent_task[:200])

        # ── 3) Одна и та же ошибка — раз в полчаса; другая — сразу ──
        print("3) повтор той же ошибки не уходит; другая уходит сразу")
        # Двадцать одинаковых ошибок подряд. Каждая — **после** предыдущей
        # отправки (иначе проверялась бы не окно, а «запись уже уходила»).
        repeats = ""
        for _ in range(20):
            write_report(core, "error", "crash", "мост не поднялся: FileNotFoundError",
                         core.now_ms(), source="minigen")
            time.sleep(0.01)
            repeats += bridge._program_tasks()
        check(task_count(repeats) == 0,
              "та же ошибка ушла Управляющему ещё раз: %d задач(и)"
              % task_count(repeats))
        check(task_count(tasks) == 1,
              "Управляющий получил по одной ошибке не одну задачу, а %d"
              % task_count(tasks))
        write_report(core, "error", "crash", "демон «сторож сессий» упал",
                     core.now_ms(), source="minigen")
        other = bridge._program_tasks()
        check(task_count(other) == 1,
              "другая ошибка не ушла сразу: %s" % other[:200])
        check("демон" in other, "в задаче нет текста другой ошибки: %s" % other[:200])

        # ── 4) Видно: ошибка «в работе», окно откроется через полчаса ──
        print("4) по отпечатку видно: когда уходила и что она в работе")
        view = errors_mod.view()
        check(view, "состояние ошибок пусто — отпечаток не виден")
        entry = next((item for item in view
                      if "демон" in str(item.get("text") or "")), None)
        check(entry is not None, "в состоянии нет ошибки «демон упал»")
        if entry:
            check(entry.get("in_work") is True,
                  "ошибка не помечена «в работе»: %s" % entry)
            check(int(entry.get("last_sent_ms") or 0) > 0,
                  "не видно, когда ошибка последний раз уходила")
            check(int(entry.get("unblock_ms") or 0)
                  - int(entry.get("last_sent_ms") or 0) == 30 * 60 * 1000,
                  "окно не полчаса: %s" % entry)
        # Через полчаса окно открывается снова (окно укорачиваем — ждать
        # полчаса в приёмке незачем).
        errors_mod.WINDOW_MS = 50
        try:
            time.sleep(0.12)
            write_report(core, "error", "crash", "демон «сторож сессий» упал",
                         core.now_ms(), source="minigen")
            reopened = bridge._program_tasks()
        finally:
            errors_mod.WINDOW_MS = 30 * 60 * 1000
        check(task_count(reopened) >= 1,
              "через полчаса окно не открылось: %s" % reopened[:200])
        check(reopened.count("демон «сторож сессий» упал") == 1,
              "через полчаса та же ошибка ушла не один раз: %s" % reopened[:300])

        # ── 5) Порог «критической» ошибки ──
        print("5) сети, вендор и чужие сессии в задачу не попадают")
        for kind, text, area, source in (
                ("network", "ни один путь не отвечает", "", "minigen"),
                ("heal", "аккаунт получен, очки кончились", "", "minigen"),
                ("tool", "areas.restart: отказ", "", "minigen"),
                ("session", "сессия ждёт владельца", "brain2", "agent")):
            write_report(core, "error", kind, text, core.now_ms(),
                         area=area, source=source)
        noise = bridge._program_tasks()
        check(task_count(noise) == 0,
              "в задачу попал не критический отчёт: %s" % noise[:200])
        feed2 = bridge._unread_reports()
        check("ни один путь не отвечает" in feed2,
              "сетевой сбой пропал из ленты отчётов: %s" % feed2[:200])

        # ── 6) Доставка отчётов не сломана, шум в задачу не попадает ──
        print("6) отчёты по-прежнему подкладываются, шум в задачу не идёт")
        check("Отчёты программы" in feed2,
              "непрочитанные отчёты не подкладываются в чат: %s" % feed2[:120])
        bridge.core = proxy
        bridge.handlers = {"update.status": lambda args: {"ok": True},
                           "reports.list": lambda args: {"reports": []}}
        before = len(proxy.written)
        bridge.dispatch("update.status", {})
        bridge.dispatch("reports.list", {})
        check(len(proxy.written) == before,
              "опрос статуса и чтение отчётов отчитываются в «ящик отчётов»: %s"
              % proxy.written[before:])
        check(task_count(bridge._program_tasks()) == 0,
              "шум попал в задачу")

        # ── 7) Правка кода идёт в ветке своей нитки ──
        print("7) правка кода — в ветке своей нитки, чужие деревья не трогаем")
        prompt = (GUI / "docs" / "knowledge-pack.md").read_text(encoding="utf-8")
        check("code.branch" in prompt and "code.pr" in prompt,
              "в системном промпте нет цепочки правки (ветка → PR)")
        check("master" in prompt,
              "в системном промпте нет запрета трогать master")
        check("ветк" in prompt,
              "в системном промпте нет слова о ветке своей нитки")

        # ── 8) Задача доводится до обновления, и в отчёте видно, что оно вышло ──
        print("8) задача доводится до обновления; обновление пишет отчёт")
        check("update.start" in prompt,
              "в системном промпте нет шага «выпустить обновление»")
        import minigen_update as update_mod
        written: list[tuple] = []
        update_mod._report("обновление вышло: программа обновлена до abc123",
                           core=type("C", (), {"report": staticmethod(
                               lambda level, kind, text, area="", source="":
                               written.append((level, kind, text)))})())
        check(written, "обновление не пишет отчёт в «ящик отчётов»")
        check(written and "обновление вышло" in written[0][2],
              "в отчёте нет слов «обновление вышло»: %s" % (written[:1],))

        # ── 9) Сторож падает (временная правка) ──
        print("9) временная правка: без защиты приёмка обязана падать")
        saved_module = bridge_mod.errors_mod
        for broken, why in (("window", "повтор той же ошибки ушёл дважды за полчаса"),
                            ("source", "в задаче не назван источник")):
            broken_mod = load_errors(broken=broken)
            broken_mod.STATE_FILE = root / ("errors-state-%s.json" % broken)
            bridge_mod.errors_mod = broken_mod
            probe_core = load_core()
            probe_core.REPORTS = root / ("reports-%s.jsonl" % broken)
            probe_core.JOURNAL = root / "journal.log"
            probe = make_bridge(CoreProxy(probe_core), root)
            stamp = probe_core.now_ms()
            if broken == "window":
                write_report(probe_core, "error", "crash", "мост упал", stamp - 1000,
                             source="minigen")
                probe._program_tasks()          # первая отправка
                repeated = ""
                for _ in range(20):
                    write_report(probe_core, "error", "crash", "мост упал",
                                 probe_core.now_ms(), source="minigen")
                    time.sleep(0.01)
                    repeated += probe._program_tasks()
                check(task_count(repeated) > 1,
                      "сломаная копия не отправила повтор — проверка фиктивна (%s)"
                      % why)
            else:
                write_report(probe_core, "error", "crash", "ход агента упал",
                             stamp, area="brain2", source="agent")
                text = probe._program_tasks()
                check("вывод сессии другого агента" not in text,
                      "сломаная копия всё равно назвала источник — проверка фиктивна")
                check("починить код" in text,
                      "сломаная копия не спутала источники — проверка фиктивна")
            bridge_mod.errors_mod = saved_module

        # ── 10) Словарь один: мост и ядро ──
        print("10) словарь видов и источников один: ядро и мост")
        check(core.KIND_CRASH == errors_mod.KIND_CRASH,
              "вид сбоя в ядре и в мосте разный: %s / %s"
              % (core.KIND_CRASH, errors_mod.KIND_CRASH))
        check(core.SOURCE_PROGRAM == errors_mod.SOURCE_PROGRAM,
              "источник «программа» в ядре и в мосте разный")
        check(core.SOURCE_AGENT == errors_mod.SOURCE_AGENT,
              "источник «агент» в ядре и в мосте разный")
        check("crash" in (core.KIND_CRASH or ""),
              "в ядре нет вида «crash»")
        tool = tools.by_name().get("errors.state")
        check(tool is not None, "в каталоге нет инструмента errors.state")
        if tool:
            check(tool["command"] == "errors.list",
                  "инструмент errors.state смотрит не в ту команду")

    bridge_mod.REPORTS_SEEN = saved_seen
    chat_store.HISTORY_FILE = saved_history
    state.read_settings = saved_settings
    errors_mod.STATE_FILE = saved_state_file

    if failures:
        print("❌ приёмка «свои критические ошибки — Управляющему» (#185): FAIL (%d)"
              % len(failures))
        return 1
    print("✅ приёмка «свои критические ошибки — Управляющему» (#185): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
