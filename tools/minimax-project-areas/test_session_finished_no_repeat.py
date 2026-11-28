# -*- coding: utf-8 -*-
"""Приёмка: сторож сессий не повторяет команду в закрытой сессии (промт КАРМАНА #184).

Живой случай, который чинится (27–28.11): агент закончил сессию и напечатал
итоговую сводку, а МиниГен продолжал слать ему «Продолжай» и «заверши сессию»
— 27 команд подряд в одну и ту же сессию (журнал `minigen-carousel.log`:
«команда закрытия сессии mvs_47c60cc5 — ход пошёл», раз в полторы минуты).

Что проверяем (критерии приёмки промта #184):

1. **В закрытую (завершённую) сессию команда не отправляется**, и в журнале
   видно «сессия уже закрыта — команда не отправлялась».
2. **Повтор одной и той же команды в одной сессии невозможен.**
3. **Управляющий получает текст последнего сообщения сессии**, и решение
   опирается на него (текст виден в запросе к модели).
4. **Сессия завершена → архив → новая сессия с паспортом нитки и «продолжи
   нитку».**
5. **Сессия не завершена → мягкое «Продолжай», сессия не закрывается.**
6. **На смену аккаунта уходит ровно одно «Продолжай»** (повторов нет).
7. **Решение лечения не переспрашивается на каждом проходе**, но при
   изменившейся ситуации принимается новое.
8. **При недоступной модели работает правило-фолбэк** и оно не требует
   завершения без отчёта о выполнении.
9. **Сторож падает** (приёмка падает), если в закрытую сессию снова уходит
   команда или если решение лечения переспрашивается на каждом проходе —
   проверено **временной правкой**: сломанная копия сторожа действительно
   шлёт команду в закрытую сессию (значит, проверка не проходит по неверной
   причине).

Каждый пункт — в **своей области** (своя база рантайма): иначе сессии соседних
пунктов мешают друг другу (сессия одной проверки лечится вместе с другой).

Запуск: python3 tools/minimax-project-areas/test_session_finished_no_repeat.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
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

#: Итоговая сводка агента — длинная, чтобы было видно: в фактах уходит весь
#: текст, а не первые 400 знаков.
FINAL_SUMMARY = (
    "Итог сессии\n"
    "-----------\n"
    "Сделано: промт КАРМАНА #182 выполнен и слит, приёмка зелёная.\n"
    "Не сделано: раскатка на машину остаётся за владельцем.\n"
    "Дальше: сверить origin/master и закрыть смену.\n"
    "Сессия закрыта, незакрытых дел нет."
)


def run_git(folder: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(folder)] + list(args),
                          capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def make_repo(root: pathlib.Path, name: str = "repo-nitka") -> pathlib.Path:
    """Репозиторий нитки — паспорт читает из него (что сделано, что осталось)."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-b", "master")
    run_git(repo, "config", "user.email", "test@example.invalid")
    run_git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("основа\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "основа")
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "SESSION_HANDOFF.md").write_text(
        "# SESSION_HANDOFF\n\n## Что сделано\n\n- основа нитки\n\n"
        "## Что дальше\n\n- доделать нитку\n\n"
        "## Критерии приёмки\n\n- гейт зелёный\n",
        encoding="utf-8")
    run_git(repo, "add", "docs/SESSION_HANDOFF.md")
    run_git(repo, "commit", "-m", "handoff")
    run_git(repo, "branch", "origin/master")
    return repo


def make_area(root: pathlib.Path, name: str = "p-test") -> pathlib.Path:
    """Область с базой рантайма (сессий пока нет — добавляем по одной)."""
    area = root / name
    (area / "v2" / "sqlite").mkdir(parents=True, exist_ok=True)
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
    return area


def add_session(area: pathlib.Path, repo: pathlib.Path, sid: str, kind: str,
                title: str = "нить", error_code=None, error_message: str = "") -> None:
    """Сессия в базе рантайма.

    `kind`:
      * ``finished`` — ход завершён, на него есть финальный ответ ⇒ сессия
        **закрыта** (работа закончена);
      * ``abandoned`` — ход завершён, финального ответа нет ⇒ нить брошена,
        сессия открыта (её можно оживлять);
      * ``running`` — ход идёт;
      * ``balance`` — отказ провайдера по очкам (лечит автомат аккаунтов).
    """
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    now = int(time.time() * 1000)
    try:
        con.execute("insert into local_runtime_sessions values (?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, "idle", 0, "conversation", title, str(repo),
                     now - 3600000, now - 600000, error_code, error_message,
                     '{"effectiveModelContextWindow": 1000000}'))
        if kind == "running":
            con.execute("insert into local_runtime_turn_ingress values (?,?,?,?,?,?)",
                        ("turn-" + sid, sid, "accepted", now - 60000, None, "{}"))
        else:
            con.execute("insert into local_runtime_turn_ingress values (?,?,?,?,?,?)",
                        ("turn-" + sid, sid, "completed", now - 900000, now - 600000, "{}"))
            con.execute("insert into local_runtime_session_agent_state values (?,?,?)",
                        (sid, "completed", now - 600000))
        con.execute("insert into local_runtime_token_usage values (?,?,?,?,?,?)",
                    (sid, now - 600000, 500000, 1000, 10000, 0))
        con.execute("insert into local_runtime_message_rows "
                    "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                    (sid, "user", now - 3600000,
                     json.dumps({"msg_content": "Сделай задачу нитки"},
                                ensure_ascii=False, separators=(",", ":"))))
        if kind == "finished":
            # Финальный ответ хода (msg_type=1) — работа закончена.
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (sid, "assistant", now - 600000,
                         json.dumps({"msg_content": FINAL_SUMMARY, "msg_type": 1},
                                    ensure_ascii=False, separators=(",", ":"))))
        else:
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (sid, "assistant", now - 700000,
                         json.dumps({"msg_content": "начал работу, контекст близок",
                                     "msg_type": 2}, ensure_ascii=False,
                                    separators=(",", ":"))))
        con.commit()
    finally:
        con.close()


def add_compaction(area: pathlib.Path, session_id: str) -> None:
    """Событие сжатия контекста рантайма (вердикт «скомпактилась»)."""
    log_dir = area / "v2" / "observability" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S", time.localtime(time.time() - 60))
    line = ('2026-11-28 [%s] {"session_id": "%s", "event": '
            '"context_compaction_checkpoint_generated"}' % (stamp, session_id))
    (log_dir / "runtime-test.log").write_text(line + "\n", encoding="utf-8")


def add_turn(area: pathlib.Path, session_id: str, turn_id: str) -> None:
    """Новый ход сессии — ситуация изменилась."""
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    now = int(time.time() * 1000)
    try:
        con.execute("insert into local_runtime_turn_ingress values (?,?,?,?,?,?)",
                    (turn_id, session_id, "completed", now + 1000, now + 2000, "{}"))
        con.commit()
    finally:
        con.close()


class FakeProc:
    """Процесс `mcode exec`, который «пошёл»."""

    pid = 4242

    def poll(self):
        return None


class Recorder:
    """Ловит всё, что сторож делает с сессией.

    ⚠️ Важно: `send_to_session` **не подменяется** — иначе проверка стражи
    «сессия закрыта» прошла бы по неверной причине. Подменяется только
    запуск процесса (`core.mcode_run`).
    """

    def __init__(self) -> None:
        self.sent: list[tuple] = []        # (session_id, текст)
        self.archived: list[str] = []
        self.opened: list[str] = []
        self.opened_in: list[str] = []
        self.logs: list[str] = []
        self.reports: list[tuple] = []
        self.ask_calls: list[dict] = []
        self.answer: dict | None = None

    def install(self, watcher) -> None:
        rec = self

        def fake_run(args, area_dir, timeout=300, background=False, logfile=None,
                     model="", effort=""):
            rec.sent.append((args[2] if len(args) > 2 else "", args[-1]))
            return FakeProc()

        def fake_archive(area_dir, session_id):
            rec.archived.append(session_id)
            return {"ok": True, "why": "архивирована"}

        def fake_new(area, prompt, force=False, folder="", by_decision=False):
            rec.opened.append(prompt)
            rec.opened_in.append(folder)
            return {"ok": True, "why": "новая сессия пошла"}

        def fake_ask(facts, area="", timeout=120):
            rec.ask_calls.append(facts)
            if rec.answer is None:
                return {"ok": False, "why": "модель недоступна"}
            return dict(rec.answer)

        watcher.core.mcode_run = fake_run
        watcher.core.log = lambda msg, *a, **k: rec.logs.append(str(msg))
        watcher.core.report = lambda level, kind, text, area="": rec.reports.append(
            (level, kind, text))
        watcher.core.refresh_env = lambda: {}
        watcher.archive_session = fake_archive
        watcher.new_session = fake_new
        watcher.wait_closed = lambda *a, **k: True
        watcher.thread.ask_manager = fake_ask
        watcher.thread.open_prs = lambda folder, branch: []

    def sent_texts(self) -> list:
        return [text for _sid, text in self.sent]

    def journal_has(self, needle: str) -> bool:
        return any(needle in line for line in self.logs)


def load_watcher(passport_file: pathlib.Path, sends_file: pathlib.Path,
                 broken: str = ""):
    """Сторож с паспортами ниток и лекаркой команд во временном каталоге.

    `broken` — «временная правка»: что вырезать из кода, чтобы проверить,
    что приёмка **падает** без этой защиты.
    """
    os.environ["MINIGEN_PASSPORTS_FILE"] = str(passport_file)
    source = (HERE / "minimax-sessionwatch.py").read_text(encoding="utf-8-sig")
    if broken == "closed":
        # Ломаем стражи «сессия уже закрыта» — приёмка должна это заметить.
        source = source.replace('if st.get("closed"):', 'if False:')
    elif broken == "cooldown":
        # Ломаем «решение не переспрашивается на каждом проходе».
        source = source.replace(
            "if last_at and int(time.time()) - last_at < DECISION_COOLDOWN and last_sig == sig:",
            "if False:")
    tmp = pathlib.Path(tempfile.gettempdir()) / (
        "sessionwatch-broken-%s.py" % (broken or "ok"))
    tmp.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "watcher_184_under_test%s" % (broken or "_ok"), str(tmp))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    # Лекарка команд — во временный каталог: живое состояние машины не трогаем.
    mod.core.SESSION_SENDS_STATE = sends_file
    return mod


def show(acts: list) -> None:
    """Что именно сделал сторож — словами (в отчёте приёмки это видно)."""
    for a in acts:
        extra = ""
        res = a.get("result")
        if isinstance(res, dict) and res.get("why"):
            extra = " — %s" % res.get("why")
        print("     · %s%s" % (a.get("action"), extra))


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)

        def scenario(name: str):
            """Своя область (и свой репозиторий) на каждый пункт."""
            repo = make_repo(root / name, name)
            area_dir = make_area(root / name, name)
            area = {"slug": name, "dir": area_dir, "folder": str(repo),
                    "title": "ТЕСТ", "autoheal": True}
            watcher = load_watcher(root / name / "passports.json",
                                   root / name / "sends.json")
            rec = Recorder()
            rec.install(watcher)
            return watcher, rec, area, area_dir, repo

        # ── 1) Закрытая сессия: команда не уходит, цепочка «архив → новая» ──
        print("1) закрытая сессия: команда не отправляется, нитка переносится")
        watcher, rec, area, area_dir, repo = scenario("s1")
        add_session(area_dir, repo, "sess-fin", "finished", title="промт #182")
        rec.answer = {"ok": True, "action": watcher.thread.CONTINUE,
                      "instruction": "доделай остаток",
                      "reason": "вывод сессии: работа закончена"}
        state = {}
        verdicts = watcher.survey(area, state)
        by_id = {v["session_id"]: v for v in verdicts}
        v = by_id.get("sess-fin") or {}
        check(v.get("verdict") == "готова",
              "сессия с финальным ответом должна быть «готова», а не %s"
              % v.get("verdict"))
        check(v.get("closed") is True, "вердикт не помечает сессию закрытой")
        acts = watcher.act(area, verdicts, state)
        show(acts)
        check(not rec.sent_texts(),
              "в закрытую сессию ушла команда: %s" % rec.sent_texts()[:1])
        check(rec.journal_has(watcher.CLOSED_SESSION_NOTE),
              "в журнале нет «%s»" % watcher.CLOSED_SESSION_NOTE)
        check(rec.archived == ["sess-fin"],
              "завершённая сессия не архивирована: %s" % rec.archived)
        check(len(rec.opened) == 1,
              "после завершения не открыта новая сессия: %s" % len(rec.opened))
        if rec.opened:
            check("Продолжи нитку" in rec.opened[0],
                  "новая сессия не получила «продолжи нитку»")
            check("Репозиторий" in rec.opened[0] and "Что сделано" in rec.opened[0],
                  "новая сессия не получила паспорт нитки")

        # ── 2) Повтор одной и той же команды в одной сессии невозможен ──
        print("2) повтор той же команды в одной сессии невозможен")
        watcher, rec, area, area_dir, repo = scenario("s2")
        add_session(area_dir, repo, "sess-open", "abandoned", title="нить брошена")
        first = watcher.send_to_session(area, {"session_id": "sess-open",
                                               "workspace_dir": str(repo)},
                                        "Продолжай нитку.", background=True,
                                        tag="проверка")
        second = watcher.send_to_session(area, {"session_id": "sess-open",
                                                "workspace_dir": str(repo)},
                                         "Продолжай нитку.", background=True,
                                         tag="проверка")
        check(first.get("ok") is True, "первая отправка не прошла: %s" % first)
        check(second.get("ok") is False,
              "повтор той же команды ушёл: %s" % second)
        check(watcher.REPEAT_NOTE in str(second.get("why") or ""),
              "в ответе нет «%s»: %s" % (watcher.REPEAT_NOTE, second.get("why")))
        check(rec.journal_has(watcher.REPEAT_NOTE),
              "в журнале нет «%s»" % watcher.REPEAT_NOTE)

        # ── 3) Управляющий видит вывод сессии ──
        print("3) управляющий получает текст последнего сообщения сессии")
        watcher, rec, area, area_dir, repo = scenario("s3")
        add_session(area_dir, repo, "sess-out", "finished", title="нить с выводом")
        rec.answer = {"ok": True, "action": watcher.thread.CONTINUE,
                      "instruction": "продолжай", "reason": "вывод сессии"}
        state = {}
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        check(rec.ask_calls, "управляющего не спросили вовсе")
        if rec.ask_calls:
            facts = rec.ask_calls[-1]
            check("last_output" in facts, "в фактах нет вывода сессии (last_output)")
            check("Итог сессии" in str(facts.get("last_output") or ""),
                  "вывод сессии в фактах обрезан или пуст: %s"
                  % str(facts.get("last_output") or "")[:80])
            # …и он виден в запросе к модели.
            sys.path.insert(0, str(HERE.parent / "minigen-gui" / "bridge"))
            try:
                import minigen_treatment as treatment
            except Exception as exc:  # noqa: BLE001
                check(False, "не удалось взять minigen_treatment: %s" % exc)
                treatment = None
            if treatment is not None:
                prompt = treatment.user_prompt(facts)
                check("вывод последнего сообщения сессии" in prompt,
                      "в запросе к модели нет вывода сессии")
                check("Итог сессии" in prompt,
                      "текст вывода сессии не попал в запрос к модели")

        # ── 4) Не завершена → мягкое «Продолжай», сессия не закрывается ──
        print("4) незавершённая сессия: мягкое «Продолжай», без закрытия")
        watcher, rec, area, area_dir, repo = scenario("s4")
        add_session(area_dir, repo, "sess-open", "abandoned", title="нить брошена")
        rec.answer = {"ok": True, "action": watcher.thread.CONTINUE,
                      "instruction": "доделай остаток", "reason": "отчёта нет"}
        state = {}
        verdicts = watcher.survey(area, state)
        by_id = {v["session_id"]: v for v in verdicts}
        check(by_id.get("sess-open", {}).get("verdict") == "брошена",
              "сессия без финального ответа должна быть «брошена», а не %s"
              % by_id.get("sess-open", {}).get("verdict"))
        acts = watcher.act(area, verdicts, state)
        show(acts)
        check(rec.sent_texts(), "мягкое «Продолжай» не отправлено")
        check(not rec.archived, "незавершённую сессию архивировали")
        check(not rec.opened, "незавершённую сессию перенесли в новую")

        # ── 5) На смену аккаунта — ровно одно «Продолжай» ──
        print("5) смена аккаунта: ровно одно «Продолжай», повторов нет")
        watcher, rec, area, area_dir, repo = scenario("s5")
        add_session(area_dir, repo, "sess-bal", "balance", title="очки кончились",
                    error_code=50110, error_message="insufficient balance")
        healed = {"last_heal": {area["slug"]: time.time()}}
        real_read_state = watcher.core.read_state

        def fake_read_state(path):
            # Подменяем ТОЛЬКО состояние автомата аккаунтов: лекарка команд
            # и всё остальное читается как обычно.
            if "autheal" in str(path):
                return healed
            return real_read_state(path)

        watcher.core.read_state = fake_read_state
        state = {}
        verdicts = watcher.survey(area, state)
        for _ in range(3):                       # три прохода подряд
            acts = watcher.act(area, verdicts, state)
        show(acts)
        balance_sends = [t for t in rec.sent_texts() if "Продолжай нитку" in t]
        check(len(balance_sends) == 1,
              "на смену аккаунта ушло «Продолжай» %d раз (ожидано 1)"
              % len(balance_sends))

        # ── 6) Решение не переспрашивается на каждом проходе ──
        print("6) решение не переспрашивается впустую; при изменении — новое")
        watcher, rec, area, area_dir, repo = scenario("s6")
        add_session(area_dir, repo, "sess-comp", "abandoned", title="контекст полон")
        add_compaction(area_dir, "sess-comp")
        rec.answer = {"ok": True, "action": watcher.thread.FINISH,
                      "instruction": "закройся", "reason": "контекст полон"}
        # Ротация не удаётся ⇒ сессия не архивируется ⇒ ситуация не меняется.
        watcher.wait_closed = lambda *a, **k: False
        state = {}
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        first_asks = len(rec.ask_calls)
        check(first_asks == 1, "управляющего спросили %d раз (ожидано 1)" % first_asks)
        watcher.act(area, verdicts, state)       # тот же проход, ситуация та же
        check(len(rec.ask_calls) == first_asks,
              "решение переспросили на том же проходе: %d" % len(rec.ask_calls))
        # Ситуация изменилась: агент ответил новым ходом.
        add_turn(area_dir, "sess-comp", "turn-sess-comp-2")
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        check(len(rec.ask_calls) == first_asks + 1,
              "при изменившейся ситуации новое решение не принято: %d"
              % len(rec.ask_calls))

        # ── 7) Фолбэк: модель недоступна — правило, и без отчёта не завершаем ──
        print("7) недоступная модель: правило-фолбэк, завершения без отчёта нет")
        watcher, rec, area, area_dir, repo = scenario("s7")
        # Вердикт «скомпактилась» — решение по нему принимает управляющий.
        add_session(area_dir, repo, "sess-fb", "abandoned", title="нить")
        add_compaction(area_dir, "sess-fb")
        rec.answer = None                          # модель недоступна
        state = {}
        verdicts = watcher.survey(area, state)
        acts = watcher.act(area, verdicts, state)
        show(acts)
        check(rec.ask_calls, "управляющего не спросили (а должно быть видно, что спросили)")
        check(not rec.archived, "фолбэк архивировал сессию без отчёта")
        check(not any("ИТОГОВУЮ СВОДКУ" in t for t in rec.sent_texts()),
              "фолбэк потребовал завершения без отчёта")
        check(not rec.opened, "фолбэк перенёс нитку без решения управляющего")

        # ── 8) Сторож падает (временная правка) ──
        print("8) временная правка: без стражи приёмка обязана падать")
        watcher, rec, area, area_dir, repo = scenario("s8")
        broken = load_watcher(root / "s8" / "passports-broken.json",
                              root / "s8" / "sends-broken.json", broken="closed")
        rec2 = Recorder()
        rec2.install(broken)
        add_session(area_dir, repo, "sess-broken", "finished", title="нить")
        res = broken.send_to_session(area, {"session_id": "sess-broken",
                                             "workspace_dir": str(repo)},
                                     "Продолжай нитку.", background=True)
        check(res.get("ok") is True,
              "сломаная копия сторожа НЕ отправила команду в закрытую сессию — "
              "значит, проверка проходит по неверной причине")
        check(rec2.sent, "сломаная копия ничего не отправила — проверка фиктивна")
        check(not rec2.journal_has(broken.CLOSED_SESSION_NOTE),
              "сломаная копия всё равно написала в журнал защиту")

        watcher, rec, area, area_dir, repo = scenario("s8b")
        broken2 = load_watcher(root / "s8b" / "passports-broken.json",
                               root / "s8b" / "sends-broken.json", broken="cooldown")
        rec3 = Recorder()
        rec3.install(broken2)
        add_session(area_dir, repo, "sess-cool", "abandoned", title="нить")
        add_compaction(area_dir, "sess-cool")
        rec3.answer = {"ok": True, "action": broken2.thread.FINISH,
                       "instruction": "закройся", "reason": "контекст полон"}
        broken2.wait_closed = lambda *a, **k: False
        state = {}
        verdicts = broken2.survey(area, state)
        broken2.act(area, verdicts, state)
        broken2.act(area, verdicts, state)
        check(len(rec3.ask_calls) >= 2,
              "сломаная копия не переспросила решение на каждом проходе — "
              "значит, проверка проходит по неверной причине")

    if failures:
        print("❌ приёмка «закрытая сессия — команда не отправляется» (#184): FAIL (%d)"
              % len(failures))
        return 1
    print("✅ приёмка «закрытая сессия — команда не отправляется» (#184): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
