# -*- coding: utf-8 -*-
"""Приёмка: водитель сессий — один на проект (промт КАРМАНА #174).

Зачитка владельца (26.11, 19:00): «один автомат» и аккаунтов, и сессий —
из-за этого чужую сессию «оживляли» посреди работы, а под ней появлялась
вторая («продолжи нитку»). Разделили: **автомат аккаунтов** (лечащий) и
**автомат управления сессиями** (сторож сессий); между ними — правило
**«один водитель на проект»**.

Проверяем на живом коде сторожа (настоящая база рантайма во временном
каталоге):

1. **переключатель «водитель проекта»**: значение проекта (`driver`) важнее
   правила; правило по умолчанию — «если в области работает Управляющий, водит
   он, иначе автомат»;
2. **при водителе «Внешнее управление» автомат в области НЕ действует**, но
   решение пишется в отчёт словами («водитель — Внешнее управление, действие
   не выполнялось»). ⚠️ Имя водителя взято **из ядра**
   (`core.driver_title(...)`): промт КАРМАНА #177 переименовал «Управляющий» в
   «Внешнее управление», и отчёт не должен разойтись с окном.
3. **сессию Управляющего автомат не трогает** — не оживляет, не сбрасывает,
   не ротирует (как служебные пробы);
4. **вторая сессия не поднимается, пока в проекте живая сессия** (своя или
   чужая); единственное исключение — решение «Управляющего» «новая сессия с
   продолжением нитки», и тогда порядок «архив старой → новая».

Запуск: python3 tools/minimax-project-areas/test_session_driver_rules.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent

SCHEMA = """
create table if not exists local_runtime_sessions (
  session_id text primary key, status text, archived integer default 0,
  session_kind text, title text, workspace_dir text, created_at_ms integer,
  updated_at_ms integer, error_code integer, error_message text,
  extra_data_json text);
create table if not exists local_runtime_turn_ingress (
  turn_id text, session_id text, status text, accepted_at_ms integer,
  completed_at_ms integer, input_json text);
create table if not exists local_runtime_message_rows (
  id integer primary key autoincrement, session_id text, role text,
  created_at_ms integer, data_json text);
create table if not exists local_runtime_session_agent_state (
  session_id text, terminal_outcome text, updated_at_ms integer);
create table if not exists local_runtime_token_usage (
  session_id text, ts integer, input_tokens integer, output_tokens integer,
  cache_read_tokens integer, reasoning_tokens integer);
create table if not exists local_runtime_background_tasks (
  record_json text, created_at_ms integer, kind text, status text,
  owner_session_id text);
"""

#: Заголовок сессии «Управляющего» — как у владельца в окне.
MANAGER_TITLE = "ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — промт КАРМАНА 172"


def load_watcher(passport_file: pathlib.Path):
    """Сторож с паспортами ниток, уведёнными во временный каталог."""
    os.environ["MINIGEN_PASSPORTS_FILE"] = str(passport_file)
    spec = importlib.util.spec_from_file_location(
        "watcher_174_under_test", str(HERE / "minimax-sessionwatch.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


def make_area(root: pathlib.Path, sessions: list[dict]) -> pathlib.Path:
    """Область с базой рантайма.

    Сессия «живая» — когда ход идёт (`accepted` без завершения) либо статус
    «started». Прочие сессии живыми не считаются: простой на минутах не должен
    замораживать проект навсегда (D-249 ⑤).
    """
    area = root / "p-test"
    (area / "v2" / "sqlite").mkdir(parents=True, exist_ok=True)
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        now = int(time.time() * 1000)
        for item in sessions:
            live = bool(item.get("live"))
            status = item.get("status") or ("started" if live else "idle")
            turn_status = "accepted" if live else "completed"
            completed = None if live else now - 600000
            con.execute("insert into local_runtime_sessions values (?,?,?,?,?,?,?,?,?,?,?)",
                        (item["id"], status, 0, "conversation", item.get("title", "нить"),
                         item.get("folder", ""), now - 3600000, now - 600000, None, "",
                         '{"effectiveModelContextWindow": 1000000}'))
            con.execute("insert into local_runtime_turn_ingress values (?,?,?,?,?,?)",
                        ("turn-" + item["id"], item["id"], turn_status,
                         now - 900000, completed, "{}"))
            con.execute("insert into local_runtime_session_agent_state values (?,?,?)",
                        (item["id"], "completed", now - 600000))
            con.execute("insert into local_runtime_token_usage values (?,?,?,?,?,?)",
                        (item["id"], now - 600000, 500000, 1000, 10000, 0))
            # Реплика ассистента БЕЗ финального ответа (msg_type=1) — отчёта о
            # выполнении нет ⇒ нитка «брошена» ⇒ её надо оживить.
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (item["id"], "user", now - 3600000,
                         json.dumps({"msg_content": "Сделай задачу нитки"}, ensure_ascii=False)))
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (item["id"], "assistant", now - 700000,
                         json.dumps({"msg_content": "начал работу, не закончил",
                                     "msg_type": 2}, ensure_ascii=False)))
            # Финальный ответ хода (msg_type=1) — если он есть, работа видна
            # как завершённая (промт КАРМАНА #177, п. 6: завершение
            # определяется по выводу сессии).
            if item.get("final"):
                # ⚠️ Без пробела после двоеточия: сторож ищет финальный ответ
                # по шаблону `"msg_type":1` (без пробела).
                con.execute("insert into local_runtime_message_rows "
                            "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                            (item["id"], "assistant", now - 500000,
                             json.dumps({"msg_content": item["final"], "msg_type": 1},
                                        ensure_ascii=False, separators=(",", ":"))))
        con.commit()
    finally:
        con.close()
    return area


class Recorder:
    """Ловит всё, что сторож делает с сессией."""

    def __init__(self) -> None:
        #: что отправили и **в какую** сессию (id, текст)
        self.sent: list[tuple] = []
        self.archived: list[str] = []
        self.opened: list[str] = []
        self.by_decision: list[bool] = []
        self.reports: list[tuple] = []

    def install(self, watcher) -> None:
        rec = self

        def fake_send(area, sess, text, background=True, force=False):
            rec.sent.append(((sess or {}).get("session_id") or "", text))
            return {"ok": True, "why": "ход пошёл"}

        def fake_archive(area_dir, session_id):
            rec.archived.append(session_id)
            return {"ok": True, "why": "архивирована"}

        def fake_new(area, prompt, force=False, folder="", by_decision=False):
            rec.opened.append(prompt)
            rec.by_decision.append(bool(by_decision))
            return {"ok": True, "why": "новая сессия пошла"}

        watcher.send_to_session = fake_send
        watcher.archive_session = fake_archive
        watcher.new_session = fake_new
        watcher.core.log = lambda *a, **k: None
        watcher.core.report = lambda level, kind, text, area="": rec.reports.append(
            (level, kind, text, area))


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        passport_file = root / "thread-passports.json"
        watcher = load_watcher(passport_file)
        core = watcher.core
        # Настоящая функция «поднять новую сессию» — её проверяем отдельно
        # (подмены ниже её не трогают).
        real_new_session = watcher.new_session

        # ── 1) правило по умолчанию: Управляющий в области ⇒ водит он ──────
        print("1) переключатель «водитель проекта» и правило по умолчанию")
        area_dir = make_area(root, [
            {"id": "sess-mgr", "title": MANAGER_TITLE, "live": True},
            {"id": "sess-work", "title": "нить владельца"},
        ])
        folder = root / "project"
        folder.mkdir(parents=True, exist_ok=True)
        area = {"slug": "test", "dir": area_dir, "folder": str(folder),
                "title": "тест", "driver": ""}

        drv = core.driver_of(area)
        check(drv["driver"] == core.DRIVER_MANAGER,
              "правило по умолчанию не увидело Управляющего: %s" % drv)
        check(drv["source"] == "правило", "источник водителя не «правило»: %s" % drv)

        area_switch = dict(area, driver="automaton")
        drv2 = core.driver_of(area_switch)
        check(drv2["driver"] == core.DRIVER_AUTOMATON,
              "переключатель проекта не перебил правило: %s" % drv2)
        check(drv2["source"] == "переключатель",
              "источник водителя не «переключатель»: %s" % drv2)

        area_none = dict(area, driver="none")
        drv3 = core.driver_of(area_none)
        check(drv3["driver"] == core.DRIVER_NONE,
              "переключатель «никто» не сработал: %s" % drv3)

        # ── 2) водитель «Управляющий» — автомат не действует, но пишет ────
        print("2) при водителе «Управляющий» действие не выполняется, но видно")
        rec = Recorder()
        rec.install(watcher)
        area_manager = dict(area, driver="manager")
        verdicts = watcher.survey(area_manager, {})
        by_id = {v["session_id"]: v for v in verdicts}
        check(by_id.get("sess-work", {}).get("verdict") == "брошена",
              "вердикт нитки не «брошена»: %s" % by_id.get("sess-work"))
        actions = watcher.act(area_manager, verdicts, {})
        work = next((a for a in actions if a.get("session") == "sess-work"), {})
        check("не выполнялось" in (work.get("action") or ""),
              "действие не помечено как невыполненное: %s" % work)
        check(work.get("driver") == core.DRIVER_MANAGER,
              "в действии нет водителя: %s" % work)
        check(not rec.sent, "сторож всё же что-то отправил в сессию: %s" % rec.sent)
        check(not rec.archived, "сторож всё же архивировал сессию: %s" % rec.archived)
        lines = [text for _l, _k, text, _a in rec.reports]
        check(any("водитель — %s" % core.driver_title(core.DRIVER_MANAGER) in text
                  and "не выполнялось" in text
                  for text in lines),
              "в отчёте нет «водитель — %s, действие не выполнялось»: %s"
              % (core.driver_title(core.DRIVER_MANAGER), lines))
        check(any(area_manager["slug"] == slug for _l, _k, _t, slug in rec.reports),
              "строка отчёта не помечена областью: %s" % rec.reports)

        # ── 3) сессию Управляющего автомат не трогает ───────────────────────
        print("3) сессия Управляющего — не оживляем, не сбрасываем, не ротируем")
        rec2 = Recorder()
        rec2.install(watcher)
        area_auto = dict(area, driver="automaton")
        verdicts2 = watcher.survey(area_auto, {})
        mgr = next((v for v in verdicts2 if v["session_id"] == "sess-mgr"), {})
        check(mgr.get("verdict") == "управляющий",
              "вердикт сессии Управляющего не «управляющий»: %s" % mgr)
        actions2 = watcher.act(area_auto, verdicts2, {})
        mgr_action = next((a for a in actions2 if a.get("session") == "sess-mgr"), {})
        check("не трогаем" in (mgr_action.get("action") or ""),
              "действие по сессии Управляющего не «не трогаем»: %s" % mgr_action)
        to_mgr = [text for sid, text in rec2.sent if sid == "sess-mgr"]
        check(not to_mgr, "в сессию Управляющего ушло сообщение: %s" % to_mgr)
        check("sess-mgr" not in rec2.archived,
              "сессия Управляющего архивирована: %s" % rec2.archived)

        # ── 4) при живой сессии вторая не поднимается ──────────────────────
        print("4) при живой сессии в проекте вторая не поднимается")
        # Запуск `mcode` подменяем: тест проверяет ЗАПРЕТ, а не запуск хода.
        launched: list[list] = []

        class FakeProc:
            pid = 4242

            def poll(self):
                return None

        def fake_mcode_run(args, area_dir, timeout=300, background=False,
                           logfile=None, model="", effort=""):
            launched.append(list(args))
            return FakeProc()

        class FakeTime:
            def sleep(self, seconds: float) -> None:
                return None

            def time(self) -> float:
                return 1000.0

        old_run = core.mcode_run
        old_time = watcher.time
        core.mcode_run = fake_mcode_run
        watcher.time = FakeTime()
        try:
            res = real_new_session(area_auto, "Продолжи нитку", folder=str(folder))
            check(not res.get("ok"), "вторая сессия поднялась при живой первой: %s" % res)
            check("живая сессия" in (res.get("why") or ""),
                  "причина отказа не про живую сессию: %s" % res.get("why"))
            check(not launched,
                  "при живой сессии всё равно запустили новую: %s" % launched)

            # Флагом запрет не обходится: «правило одного водителя» — не флаг.
            res_force = real_new_session(area_auto, "Продолжи нитку", force=True,
                                         folder=str(folder))
            check(not res_force.get("ok"),
                  "флаг обошёл запрет второй сессии: %s" % res_force)
            check(not launched,
                  "флаг обошёл запрет второй сессии (запуск): %s" % launched)

            # Исключение — решение «Управляющего»: право есть, порядок — в
            # `apply_decision` (там «сначала архив старой, потом новая»).
            res_dec = real_new_session(area_auto, "Продолжи нитку", by_decision=True,
                                       folder=str(folder))
            check(res_dec.get("ok") is True,
                  "решение «Управляющего» не смогло поднять новую сессию: %s" % res_dec)
            check(len(launched) == 1,
                  "решение «Управляющего» подняло сессий не одну: %s" % launched)
        finally:
            core.mcode_run = old_run
            watcher.time = old_time

        # Порядок «архив старой → новая» — в решении, а не в флаге.
        order: list[str] = []
        rec3 = Recorder()
        rec3.install(watcher)
        old_new = watcher.new_session
        old_archive = watcher.archive_session

        def order_new(area, prompt, force=False, folder="", by_decision=False):
            order.append("new")
            rec3.opened.append(prompt)
            rec3.by_decision.append(bool(by_decision))
            return {"ok": True, "why": "новая сессия пошла"}

        def order_archive(area_dir, session_id):
            order.append("archive")
            rec3.archived.append(session_id)
            return {"ok": True, "why": "архивирована"}

        watcher.new_session = order_new
        watcher.archive_session = order_archive
        watcher.wait_closed = lambda *a, **k: True
        watcher.send_to_session = lambda *a, **k: {"ok": True, "why": "ход пошёл"}
        watcher.thread.ask_manager = lambda facts, area="", timeout=120: {
            "ok": True, "action": "новая_сессия", "instruction": "Продолжи нитку.",
            "reason": "контекст сжат"}
        watcher.thread.open_prs = lambda folder, branch: []
        sess = {"session_id": "sess-work", "title": "нить владельца",
                "workspace_dir": str(folder)}
        decision = {"action": "новая_сессия", "source": "Управляющий",
                    "instruction": "Продолжи нитку.", "reason": "контекст сжат"}
        report = watcher.apply_decision(area_auto, sess, {"verdict": "скомпактилась"},
                                        decision, {"thread_id": "t-1", "worktree": str(folder),
                                                   "repo": "r", "branch": "b"}, {})
        check(order.index("archive") < order.index("new"),
              "порядок нарушен: %s" % order)
        check(rec3.by_decision == [True],
              "перенос нитки без права на исключение: %s" % rec3.by_decision)
        check(report.get("ok"), "перенос нитки не удался: %s" % report.get("why"))
        watcher.new_session = old_new
        watcher.archive_session = old_archive

    if failures:
        print("❌ приёмка водителя сессий (#174): FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка водителя сессий (#174): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
