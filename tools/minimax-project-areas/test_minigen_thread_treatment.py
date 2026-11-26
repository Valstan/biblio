# -*- coding: utf-8 -*-
"""Приёмка промта КАРМАНА #172 — «лечение сессий: решение принимает “Управляющий”,
нитку переносим, а не теряем».

Заказ владельца (зачитка 26.11.2026): сторож сессий не должен требовать
завершения сессии, у которой **нет отчёта о выполнении**; перенос нитки идёт
**с паспортом** (что делалось, что осталось, репозиторий, ветка/worktree, PR,
критерии приёмки); решение о лечении принимает **встроенная нейросеть
«Управляющий»** — ровно одно из трёх действий, а если модель недоступна —
предсказуемое правило-фолбэк, и оно **не требует** завершения без отчёта.

Проверяем:

1. **вердикт «скомпактилась» без отчёта** ⇒ сессия НЕ получает команду
   «заверши работу правильно» и НЕ архивируется (и при решении модели
   «продолжать», и при недоступной модели);
2. **завершение/ротация — только при отчёте либо по решению «Управляющего»**;
3. **паспорт нитки**: на диске машины (вне репозитория), переживает
   «перезапуск» (его читает свежий процесс), и новая сессия получает его;
4. **параллельные нитки одного репозитория** не путаются: у каждой свой
   ветка/worktree, чужие деревья не трогаются (ADR-0011);
5. **решение «Управляющего»**: модель вернула «продолжать» ⇒ сессия не
   тронута; модель недоступна ⇒ правило-фолбэк, а не «закрыть».

Запуск: python tools/minimax-project-areas/test_minigen_thread_treatment.py
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent

#: Минимальная схема базы рантайма — только те таблицы, что читает сторож.
SCHEMA = """
create table local_runtime_sessions (
  session_id text primary key, status text, archived integer, session_kind text,
  title text, workspace_dir text, created_at_ms integer, updated_at_ms integer,
  error_code integer, error_message text, extra_data_json text, record_json text);
create table local_runtime_turn_ingress (
  turn_id text, session_id text, status text, accepted_at_ms integer,
  completed_at_ms integer, input_json text);
create table local_runtime_message_rows (
  id integer primary key autoincrement, session_id text, role text,
  created_at_ms integer, data_json text);
create table local_runtime_token_usage (
  session_id text, ts integer, input_tokens integer, output_tokens integer,
  cache_read_tokens integer, reasoning_tokens integer);
create table local_runtime_session_agent_state (
  session_id text, terminal_outcome text, updated_at_ms integer);
create table local_runtime_background_tasks (
  kind text, status text, owner_session_id text, record_json text,
  created_at_ms integer);
"""


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    """Записывает, что сторож делал с сессиями."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.opened: list[dict] = []
        self.facts: list[dict] = []
        self.asked: list[str] = []

    def send_to_session(self, area, sess, text, background=True, force=False):
        self.sent.append((sess.get("session_id") or "", text or ""))
        return {"ok": True, "why": "ход пошёл"}

    def archive_session(self, area_dir, session_id):
        self.archived.append(session_id)
        return {"ok": True, "why": "архивирована"}

    def new_session(self, area, prompt, force=False, cwd=""):
        self.opened.append({"prompt": prompt or "", "cwd": cwd or ""})
        return {"ok": True, "why": "новая сессия пошла"}

    def wait_closed(self, area, session_id, timeout=0):
        return True


def make_area(tmp: str, slug: str = "minigen", folder: str = "") -> dict:
    """Область с пустой базой рантайма."""
    area_dir = pathlib.Path(tmp) / ("p-" + slug)
    (area_dir / "v2" / "sqlite").mkdir(parents=True, exist_ok=True)
    db = area_dir / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return {"slug": slug, "dir": area_dir, "title": "тест", "autoheal": True,
            "folder": folder or str(area_dir)}


def add_session(area: dict, session_id: str, title: str, folder: str,
                report: bool = False, words: str = "делаю работу по нитке") -> dict:
    """Сессия с завершённым ходом; `report` — с отчётом о выполнении."""
    db = area["dir"] / "v2" / "sqlite" / "runtime-state.sqlite"
    now = int(time.time() * 1000)
    con = sqlite3.connect(str(db))
    con.execute(
        "insert or replace into local_runtime_sessions values (?,?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, "idle", 0, "conversation", title, folder, now - 60000,
         now - 1000, None, "", '{"effectiveModelContextWindow": 1000000}', "{}"))
    con.execute(
        "insert or replace into local_runtime_turn_ingress values (?,?,?,?,?,?)",
        ("turn-" + session_id, session_id, "completed", now - 30000, now - 20000, "{}"))
    if report:
        payload = json.dumps({"msg_type": 1, "turnId": "turn-" + session_id,
                               "msg_content": "Итоговая сводка: работа закончена, "
                                              "тесты зелёные."}, ensure_ascii=False)
    else:
        payload = json.dumps({"msg_type": 0, "turnId": "turn-" + session_id,
                              "msg_content": words}, ensure_ascii=False)
    con.execute(
        "insert into local_runtime_message_rows (session_id, role, created_at_ms, "
        "data_json) values (?,?,?,?)",
        (session_id, "assistant", now - 20000, payload))
    con.commit()
    con.close()
    return {"session_id": session_id, "title": title, "folder": folder,
            "workspace_dir": folder}


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    core = load_module("core_172", HERE / "minigen_core.py")
    watcher = load_module("watcher_172", HERE / "minimax-sessionwatch.py")
    # ⚠️ Ядро у сторожа — **то же самое** (`import minigen_core as core`):
    # берём его оттуда, иначе подмена путей отчётов не дойдёт до сторожа и
    # тест писал бы в живой «ящик отчётов» на машине владельца.
    core = watcher.core
    # паспорт — тот же модуль, что у сторожа (иначе тест писал бы в живой
    # «ящик паспортов» на машине владельца)
    passport = watcher.passport_mod
    if passport is None:
        print("❌ в стороже нет модуля паспорта (minigen_passport.py)")
        return 1

    rec = Recorder()

    with tempfile.TemporaryDirectory() as tmp:
        # отчёты и журнал — в папку теста, а не в живой «ящик отчётов»
        core.REPORTS = pathlib.Path(tmp) / "reports.jsonl"
        core.JOURNAL = pathlib.Path(tmp) / "journal.log"
        passport.PASSPORT_DIR = pathlib.Path(tmp) / "passports"

        watcher.send_to_session = rec.send_to_session
        watcher.archive_session = rec.archive_session
        watcher.new_session = rec.new_session
        watcher.wait_closed = rec.wait_closed
        watcher.probe_of = lambda area, sess: False

        def set_answer(answer):
            def ask(facts):
                rec.facts.append(facts)
                rec.asked.append(str(facts.get("session_id")))
                return answer
            watcher.ask_manager = ask

        # ── 1) вердикт «скомпактилась» БЕЗ отчёта ⇒ не завершаем
        print("1) «скомпактилась» без отчёта: ни завершения, ни архива")
        area = make_area(tmp, "minigen", folder=str(pathlib.Path(tmp) / "wt-main"))
        add_session(area, "s-no-report", "нитка без отчёта",
                    str(pathlib.Path(tmp) / "wt-main"))
        con = core.connect(core.area_db(area["dir"]))
        sess = {"session_id": "s-no-report", "title": "нитка без отчёта",
                "kind": "conversation", "status": "idle", "archived": False,
                "workspace_dir": str(pathlib.Path(tmp) / "wt-main"),
                "created_at_ms": 0, "updated_at_ms": int(time.time() * 1000),
                "error_code": None, "error_message": "", "extra": {},
                "extra_data_json": "{}"}
        verdict = watcher.classify(area, sess, con, {"s-no-report": 1})
        con.close()
        check(verdict["verdict"] == "скомпактилась",
              "вердикт не «скомпактилась»: %s" % verdict["verdict"])
        check(verdict["has_report"] is False,
              "отчёт не найден, а вердикт говорит, что он есть")
        set_answer({"ok": True, "action": "continue",
                    "instruction": "продолжи то, что не дописал",
                    "reason": "отчёта о выполнении нет"})
        rec.sent.clear()
        rec.archived.clear()
        rec.opened.clear()
        watcher.act(area, [verdict], {})
        check(not rec.archived, "сессия без отчёта архивирована: %s" % rec.archived)
        check(not rec.opened, "сессия без отчёта перенесена в новую: %s" % rec.opened)
        finish_words = ("заверши работу", "ИТОГОВУЮ СВОДКУ", "остановись")
        bad = [t for _, t in rec.sent if any(w.lower() in t.lower() for w in finish_words)]
        check(not bad, "сессия без отчёта получила команду завершения: %s" % bad)
        check(bool(rec.sent), "сессию совсем не тронули — а она стоит, работу надо продолжить")

        # ── 2) модель недоступна ⇒ правило-фолбэк, а не «закрыть»
        print("2) модель недоступна: правило-фолбэк не требует завершения")
        set_answer({"ok": False, "error": "программа не ответила"})
        rec.sent.clear()
        rec.archived.clear()
        rec.opened.clear()
        watcher.act(area, [verdict], {})
        check(not rec.archived, "фолбэк архивировал сессию без отчёта")
        bad = [t for _, t in rec.sent if any(w.lower() in t.lower() for w in finish_words)]
        check(not bad, "фолбэк отправил команду завершения: %s" % bad)
        check(rec.asked, "сторож не спросил «Управляющего» вовсе")

        # ── 3) отчёт есть ⇒ завершать можно
        print("3) отчёт о выполнении есть: завершение разрешено")
        area2 = make_area(tmp, "minigen2", folder=str(pathlib.Path(tmp) / "wt-two"))
        add_session(area2, "s-report", "нитка с отчётом",
                    str(pathlib.Path(tmp) / "wt-two"), report=True)
        con = core.connect(core.area_db(area2["dir"]))
        sess2 = {"session_id": "s-report", "title": "нитка с отчётом",
                 "kind": "conversation", "status": "idle", "archived": False,
                 "workspace_dir": str(pathlib.Path(tmp) / "wt-two"),
                 "created_at_ms": 0, "updated_at_ms": int(time.time() * 1000),
                 "error_code": None, "error_message": "", "extra": {},
                 "extra_data_json": "{}"}
        verdict2 = watcher.classify(area2, sess2, con, {"s-report": 1})
        con.close()
        check(verdict2["has_report"] is True, "отчёт не распознан")
        set_answer({"ok": False, "error": "программа не ответила"})
        rec.sent.clear()
        rec.archived.clear()
        rec.opened.clear()
        watcher.act(area2, [verdict2], {})
        check("s-report" in rec.archived,
              "сессия с отчётом не закрыта правилом-фолбэком: %s" % rec.archived)

        # ── 4) перенос нитки: паспорт → архив → новая сессия с паспортом
        print("4) перенос нитки: паспорт и новая сессия в своей ветке")
        area3 = make_area(tmp, "minigen3", folder=str(pathlib.Path(tmp) / "wt-three"))
        add_session(area3, "s-move", "нитка на перенос",
                    str(pathlib.Path(tmp) / "wt-three"))
        con = core.connect(core.area_db(area3["dir"]))
        sess3 = {"session_id": "s-move", "title": "нитка на перенос",
                 "kind": "conversation", "status": "idle", "archived": False,
                 "workspace_dir": str(pathlib.Path(tmp) / "wt-three"),
                 "created_at_ms": 0, "updated_at_ms": int(time.time() * 1000),
                 "error_code": None, "error_message": "", "extra": {},
                 "extra_data_json": "{}"}
        verdict3 = watcher.classify(area3, sess3, con, {"s-move": 1})
        con.close()
        set_answer({"ok": True, "action": "rotate", "instruction": "",
                    "reason": "сессия не отвечает"})
        rec.sent.clear()
        rec.archived.clear()
        rec.opened.clear()
        watcher.act(area3, [verdict3], {})
        check("s-move" in rec.archived, "старая сессия не архивирована")
        check(len(rec.opened) == 1, "новая сессия не открыта: %s" % rec.opened)
        prompt = rec.opened[0]["prompt"] if rec.opened else ""
        check("Продолжи нитку" in prompt,
              "новая сессия не получила команду «продолжи нитку»")
        check("wt-three" in prompt,
              "в паспорте нет worktree нитки:\n%s" % prompt[:400])
        # паспорт лежит на диске (вне репозитория) и переживает «перезапуск»
        saved = list(passport.PASSPORT_DIR.glob("**/*.json"))
        check(bool(saved), "паспорт нитки не записан на диск")
        if saved:
            fresh = load_module("passport_172_fresh", HERE / "minigen_passport.py")
            fresh.PASSPORT_DIR = passport.PASSPORT_DIR
            data = fresh.load("minigen3", "s-move")
            check(bool(data), "паспорт не прочитан свежим процессом")
            check(data.get("worktree", "").endswith("wt-three"),
                  "в паспорте не тот worktree: %s" % data.get("worktree"))
            check(str(passport.PASSPORT_DIR).lower().startswith(
                      str(pathlib.Path(tempfile.gettempdir())).lower()),
                  "паспорт лёг не в папку теста, а в живое место машины: %s"
                  % passport.PASSPORT_DIR)

        # ── 5) параллельные нитки одного репозитория не путаются
        print("5) параллельные нитки: у каждой своя ветка/worktree")
        area4 = make_area(tmp, "minigen4", folder=str(pathlib.Path(tmp) / "wt-a"))
        add_session(area4, "s-a", "нитка А", str(pathlib.Path(tmp) / "wt-a"))
        add_session(area4, "s-b", "нитка Б", str(pathlib.Path(tmp) / "wt-b"))
        con = core.connect(core.area_db(area4["dir"]))
        sess_a = {"session_id": "s-a", "title": "нитка А", "kind": "conversation",
                  "status": "idle", "archived": False,
                  "workspace_dir": str(pathlib.Path(tmp) / "wt-a"),
                  "created_at_ms": 0, "updated_at_ms": int(time.time() * 1000),
                  "error_code": None, "error_message": "", "extra": {},
                  "extra_data_json": "{}"}
        facts_a, passport_a = watcher.thread_facts(area4, sess_a, {"verdict": "x"}, con)
        con.close()
        check(any(item.get("session_id") == "s-b"
                  for item in facts_a["parallel_sessions"]),
              "параллельная нитка не видна в фактах: %s" % facts_a["parallel_sessions"])
        check(passport_a.get("worktree", "").endswith("wt-a"),
              "у нитки А чужой worktree: %s" % passport_a.get("worktree"))
        check("не трогать" in (passport_a.get("foreign_note") or "").lower(),
              "в паспорте нет пометки «чужие деревья не трогать»")
        # перенос нитки А не должен трогать дерево нитки Б
        set_answer({"ok": True, "action": "rotate", "instruction": "",
                    "reason": "перенос"})
        rec.sent.clear()
        rec.archived.clear()
        rec.opened.clear()
        watcher.act(area4, [{"session_id": "s-a", "title": "нитка А",
                              "verdict": "скомпактилась", "has_report": False,
                              "fill": 90.0, "reason": "пик контекста"}], {})
        check(rec.opened and rec.opened[0]["cwd"].endswith("wt-a"),
              "новая сессия открыта не в своей ветке: %s" % rec.opened)
        check(rec.opened and "wt-b" not in rec.opened[0]["prompt"],
              "в паспорт нитки А попало чужое дерево")

        # ── 6) решение «Управляющего»: модель сказала «продолжать» ⇒ не трогаем
        print("6) модель: «продолжать» ⇒ сессию не закрываем и не архивируем")
        area5 = make_area(tmp, "minigen5", folder=str(pathlib.Path(tmp) / "wt-five"))
        add_session(area5, "s-five", "нитка пятая", str(pathlib.Path(tmp) / "wt-five"))
        con = core.connect(core.area_db(area5["dir"]))
        sess5 = {"session_id": "s-five", "title": "нитка пятая",
                 "kind": "conversation", "status": "idle", "archived": False,
                 "workspace_dir": str(pathlib.Path(tmp) / "wt-five"),
                 "created_at_ms": 0, "updated_at_ms": int(time.time() * 1000),
                 "error_code": None, "error_message": "", "extra": {},
                 "extra_data_json": "{}"}
        verdict5 = watcher.classify(area5, sess5, con, {"s-five": 1})
        con.close()
        set_answer({"ok": True, "action": "continue",
                    "instruction": "доделай пункт 2 из критериев приёмки",
                    "reason": "отчёта нет"})
        rec.sent.clear()
        rec.archived.clear()
        rec.opened.clear()
        watcher.act(area5, [verdict5], {})
        check(not rec.archived, "сессия архивирована при решении «продолжать»")
        check(not rec.opened, "при решении «продолжать» открыта новая сессия")
        check(any("доделай пункт 2" in t for _, t in rec.sent),
              "инструкция «Управляющего» не дошла до сессии: %s" % rec.sent)

        # ── 7) решение видно в отчёте (кто, когда, по каким фактам, что сделано)
        print("7) решение видно в отчёте сторожа")
        reports = core.REPORTS.read_text(encoding="utf-8") if core.REPORTS.is_file() else ""
        check("решение лечения" in reports,
              "в отчётах нет решения о лечении:\n%s" % reports[-600:])
        check("Управляющего" in reports or "правило-фолбэк" in reports,
              "в отчёте не сказано, кто решил:\n%s" % reports[-600:])

    if failures:
        print("❌ приёмка лечения сессий (#172): FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка лечения сессий (#172): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
