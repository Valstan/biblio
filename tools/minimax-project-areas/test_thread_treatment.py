# -*- coding: utf-8 -*-
"""Приёмка: лечение сессий по решению «Управляющего» (промт КАРМАНА #172).

Заказ владельца (26.11, промт #172, раздел `MiniGen`):

1. **не требовать завершения сессии, пока нет отчёта о выполнении** — раньше
   сторож при вердиктах «скомпактилась» / «контекст заполнен» слал команду
   «Завершай работу в этой сессии правильно… напечатай ИТОГОВУЮ СВОДКУ…
   остановись», и нитка обрывалась на полпути;
2. **переносить нитку, а не начинать заново** — паспорт нитки (что делалось,
   что осталось, репозиторий, ветка/worktree, PR, критерии приёмки) живёт на
   диске машины, вне репозитория (D-038), и переживает перезапуск окна;
3. **не путать параллельные сессии одного репозитория** (ADR-0011): каждая
   нитка продолжает свою ветку/worktree, чужие деревья не трогать;
4. **решение лечения принимает встроенная нейросеть «Управляющего»**: на вход
   — факты, на выход — ровно одно из трёх действий (`продолжать` /
   `завершать` / `новая_сессия`) + текст инструкции + причина; решение видно
   в отчёте;
5. **если модель недоступна** — работает предсказуемое правило-фолбэк, и оно
   **не требует завершать сессию без отчёта**.

Проверяем на живом коде сторожа: поднимаем настоящую базу рантайма (в
временном каталоге), настоящие git-репозитории с двумя worktree и прогоняем
`survey()` → `act()`.

Запуск: python3 tools/minimax-project-areas/test_thread_treatment.py
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


def run_git(folder: pathlib.Path, *args: str) -> str:
    """`git` в каталоге (пустая строка — не получилось)."""
    proc = subprocess.run(["git", "-C", str(folder)] + list(args),
                          capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def make_repo(root: pathlib.Path, name: str) -> pathlib.Path:
    """Репозиторий с веткой нитки и локальным «origin/master» (для git-фактов)."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-b", "master")
    run_git(repo, "config", "user.email", "test@example.invalid")
    run_git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("основа\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "основа")
    # Состояние нитки в репозитории: из него паспорт берёт «что осталось» и
    # критерии приёмки (как в живом репозитории — `docs/SESSION_HANDOFF.md`).
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "SESSION_HANDOFF.md").write_text(
        "# SESSION_HANDOFF\n\n## Что сделано\n\n- основа репозитория\n\n"
        "## Что дальше\n\n- доделать нитку и открыть PR\n\n"
        "## Критерии приёмки\n\n- гейт зелёный\n- нить не потеряна\n",
        encoding="utf-8")
    run_git(repo, "add", "docs/SESSION_HANDOFF.md")
    run_git(repo, "commit", "-m", "handoff")
    # «origin/master» — общая точка отсчёта: обе нитки отходят от неё, а не
    # друг от друга (как три полосы в трёх worktree одного репозитория).
    run_git(repo, "branch", "origin/master")
    return repo


def make_thread(repo: pathlib.Path, branch: str, file_name: str) -> pathlib.Path:
    """Ветка нитки со своим файлом (границы нитки — свои)."""
    run_git(repo, "checkout", "-b", branch)
    (repo / file_name).write_text("работа нитки %s\n" % branch, encoding="utf-8")
    run_git(repo, "add", file_name)
    run_git(repo, "commit", "-m", "работа: %s" % branch)
    return repo


def make_worktree(repo: pathlib.Path, root: pathlib.Path, name: str,
                  branch: str) -> pathlib.Path:
    """Связанный worktree той же нитки (параллельная сессия того же репозитория).

    ⚠️ Ветка отходит от `origin/master`, а не от текущей ветки: полосы одного
    репозитория не должны расти одна из другой.
    """
    run_git(repo, "worktree", "add", "-b", branch, str(root / name), "origin/master")
    return root / name


def make_area(root: pathlib.Path, sessions: list[dict]) -> pathlib.Path:
    """Область с базой рантайма: сессии без отчёта о выполнении."""
    area = root / "p-test"
    (area / "v2" / "sqlite").mkdir(parents=True, exist_ok=True)
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        now = int(time.time() * 1000)
        for item in sessions:
            con.execute("insert into local_runtime_sessions values (?,?,?,?,?,?,?,?,?,?,?)",
                        (item["id"], "idle", 0, "conversation", item.get("title", "нить"),
                         item.get("folder", ""), now - 3600000, now - 600000, None, "",
                         '{"effectiveModelContextWindow": 1000000}'))
            con.execute("insert into local_runtime_turn_ingress values (?,?,?,?,?,?)",
                        ("turn-" + item["id"], item["id"], "completed",
                         now - 900000, now - 600000, "{}"))
            con.execute("insert into local_runtime_session_agent_state values (?,?,?)",
                        (item["id"], "completed", now - 600000))
            con.execute("insert into local_runtime_token_usage values (?,?,?,?,?,?)",
                        (item["id"], now - 600000, 500000, 1000, 10000, 0))
            # Реплика ассистента БЕЗ финального ответа (msg_type=1) — значит,
            # отчёта о выполнении нет.
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (item["id"], "user", now - 3600000,
                         json.dumps({"msg_content": "Сделай задачу нитки"}, ensure_ascii=False)))
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (item["id"], "assistant", now - 700000,
                         json.dumps({"msg_content": "начал работу, контекст близок",
                                     "msg_type": 2}, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()
    return area


def add_compaction(area: pathlib.Path, session_id: str) -> None:
    """Событие сжатия контекста рантайма (вердикт «скомпактилась»)."""
    log_dir = area / "v2" / "observability" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S", time.localtime(time.time() - 60))
    line = ('2026-11-26 [%s] {"session_id": "%s", "event": '
            '"context_compaction_checkpoint_generated"}' % (stamp, session_id))
    (log_dir / "runtime-test.log").write_text(line + "\n", encoding="utf-8")


def load_watcher(passport_file: pathlib.Path):
    """Сторож с паспортами ниток, уведёнными во временный каталог."""
    os.environ["MINIGEN_PASSPORTS_FILE"] = str(passport_file)
    spec = importlib.util.spec_from_file_location(
        "watcher_172_under_test", str(HERE / "minimax-sessionwatch.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    """Ловит всё, что сторож делает с сессией."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.archived: list[str] = []
        self.opened: list[str] = []
        self.opened_in: list[str] = []
        self.reports: list[tuple] = []
        self.ask_calls: list[dict] = []
        #: «Ситуация» каждой отправки (тег) — по ному видно, что повтор той же
        #: команды в той же ситуации не повторяется (промт КАРМАНА #184).
        self.tags: list[str] = []
        #: Порядок действий над сессией — «закрыть», «архив», «новая»:
        #: по нему видно, что перенос нитки идёт «сначала архив старой, потом
        #: новая» (промт КАРМАНА #174, п. 3).
        self.order: list[str] = []
        #: Право на исключение из запрета «вторая сессия не поднимается»:
        #: его даёт только решение «Управляющего».
        self.by_decision: list[bool] = []

    def install(self, watcher, answer: dict | None) -> None:
        rec = self

        def fake_send(area, sess, text, background=True, force=False, tag="", why="",
                      is_close=False, finished=None, force_closed=False,
                      check_closed=True):
            # ⚠️ Ключи повторяют `send_to_session()` (промт КАРМАНА #186):
            # заглушка должна принимать всё, чем сторож зовёт отправку.
            rec.sent.append(text)
            rec.order.append("send")
            rec.tags.append(tag)
            return {"ok": True, "why": "ход пошёл"}

        def fake_archive(area_dir, session_id):
            rec.archived.append(session_id)
            rec.order.append("archive")
            return {"ok": True, "why": "архивирована"}

        def fake_new(area, prompt, force=False, folder="", by_decision=False):
            rec.opened.append(prompt)
            rec.opened_in.append(folder)
            rec.by_decision.append(bool(by_decision))
            rec.order.append("new")
            return {"ok": True, "why": "новая сессия пошла"}

        def fake_ask(facts, area="", timeout=120):
            rec.ask_calls.append(facts)
            if answer is None:
                return {"ok": False, "why": "модель недоступна"}
            return dict(answer)

        watcher.send_to_session = fake_send
        watcher.archive_session = fake_archive
        watcher.new_session = fake_new
        watcher.wait_closed = lambda *a, **k: True
        watcher.core.log = lambda *a, **k: None
        watcher.core.report = lambda level, kind, text, area="": rec.reports.append(
            (level, kind, text))
        watcher.thread.ask_manager = fake_ask
        watcher.thread.open_prs = lambda folder, branch: [
            {"number": 42, "title": "PR нитки", "url": "https://example.invalid/pr/42",
             "branch": branch, "draft": False}]


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        passport_file = root / "thread-passports.json"
        repo = make_repo(root, "repo-a")
        make_thread(repo, "feat/thread-a", "a.py")
        wt_b = make_worktree(repo, root, "wt-b", "feat/thread-b")
        (wt_b / "b.py").write_text("работа нитки b\n", encoding="utf-8")
        run_git(wt_b, "add", "b.py")
        run_git(wt_b, "commit", "-m", "работа: b")

        area = make_area(root, [
            {"id": "sess-a", "folder": str(repo), "title": "нить A"},
            {"id": "sess-b", "folder": str(wt_b), "title": "нить B"},
        ])
        add_compaction(area, "sess-a")
        area_info = {"slug": "test", "dir": area, "folder": str(repo), "title": "тест"}

        watcher = load_watcher(passport_file)

        # ── 1) вердикт: «скомпактилась», отчёта о выполнении нет ──────────
        print("1) вердикт и отчёт о выполнении")
        verdicts = watcher.survey(area_info, {})
        by_id = {v["session_id"]: v for v in verdicts}
        check("sess-a" in by_id, "сессия sess-a не разобрана")
        va = by_id.get("sess-a") or {}
        check(va.get("verdict") == "скомпактилась",
              "вердикт не «скомпактилась»: %s" % va.get("verdict"))
        check(va.get("has_report") is False,
              "отчёт о выполнении не зафиксирован как отсутствующий: %s"
              % va.get("report_kind"))
        check(va.get("report_kind") == "",
              "вид отчёта не пуст, хотя отчёта нет: %s" % va.get("report_kind"))

        # ── 2) модель ответила «продолжать» ⇒ сессию не трогаем ───────────
        print("2) решение «Управляющего»: продолжать")
        rec = Recorder()
        rec.install(watcher, {"ok": True, "action": "продолжать",
                              "instruction": "Доделай задачу нитки A: тест и PR.",
                              "reason": "отчёта о выполнении нет"})
        state: dict = {}
        actions = watcher.act(area_info, verdicts, state)
        act_a = next((x for x in actions if x.get("session") == "sess-a"), {})
        report = act_a.get("report") or {}
        check(report.get("action") == "продолжать",
              "решение не «продолжать»: %s" % report.get("action"))
        check(watcher.RESET_COMMAND not in rec.sent,
              "сессия без отчёта получила команду «заверши работу правильно»")
        check(watcher.CLOSE_COMMAND not in rec.sent,
              "сессия без отчёта получила команду закрытия")
        check(not rec.archived, "сессия без отчёта архивирована: %s" % rec.archived)
        check(not rec.opened, "сессия без отчёта перенесена в новую сессию")
        check(any("Продолжи работу по нитке" in text for text in rec.sent),
              "сессию не подтолкнули продолжить работу: %s" % rec.sent)
        check(any("Доделай задачу нитки A" in text for text in rec.sent),
              "подталкивание не называет, что делать дальше (инструкция «Управляющего»)")
        # решение видно в отчёте
        check((report.get("decision") or {}).get("action") == "продолжать",
              "в отчёте нет решения")
        check((report.get("decision") or {}).get("reason"),
              "в отчёте нет причины решения")
        check(any("продолжать" in (text or "") for _l, _k, text in rec.reports),
              "решение не попало в отчёт программы: %s" % rec.reports)
        # на вход модели — факты, а не простыня логов
        facts = rec.ask_calls[0] if rec.ask_calls else {}
        check(facts.get("has_report") is False, "в фактах нет отметки об отчёте")
        check(facts.get("verdict") == "скомпактилась", "в фактах нет вердикта")
        check(bool(facts.get("compaction")), "в фактах нет события сжатия контекста")
        check(bool((facts.get("thread") or {}).get("branch")),
              "в фактах нет ветки нитки")
        check(bool(facts.get("parallel_sessions")),
              "в фактах нет списка параллельных сессий того же репозитория")
        check(bool(facts.get("rules")), "в фактах нет правил (ADR-0011 и др.)")

        # ── 3) модель недоступна ⇒ правило-фолбэк, а не «закрыть» ─────────
        print("3) модель недоступна — правило-фолбэк")
        rec2 = Recorder()
        rec2.install(watcher, None)
        state2: dict = {}
        actions2 = watcher.act(area_info, watcher.survey(area_info, {}), state2)
        act2 = next((x for x in actions2 if x.get("session") == "sess-a"), {})
        rep2 = act2.get("report") or {}
        check(rep2.get("action") == "продолжать",
              "фолбэк решил не то: %s" % rep2.get("action"))
        check(watcher.RESET_COMMAND not in rec2.sent,
              "фолбэк потребовал завершения сессии без отчёта")
        check(watcher.CLOSE_COMMAND not in rec2.sent,
              "фолбэк закрыл сессию без отчёта")
        check(not rec2.archived, "фолбэк архивировал сессию без отчёта")
        check((rep2.get("decision") or {}).get("source") == "правило",
              "фолбэк не помечен как правило: %s" % (rep2.get("decision") or {}))

        # ── 4) перенос нитки: паспорт → завершение → архив → новая ────────
        print("4) перенос нитки с паспортом")
        rec3 = Recorder()
        rec3.install(watcher, {"ok": True, "action": "новая_сессия",
                               "instruction": "Продолжи нитку A в новой сессии.",
                               "reason": "контекст сжат, нитка большая"})
        state3: dict = {}
        actions3 = watcher.act(area_info, watcher.survey(area_info, {}), state3)
        act3 = next((x for x in actions3 if x.get("session") == "sess-a"), {})
        rep3 = act3.get("report") or {}
        check(rep3.get("action") == "новая_сессия", "перенос не выбран")
        check(watcher.CLOSE_COMMAND in rec3.sent,
              "старая сессия не закрыта перед переносом")
        check(rec3.archived == ["sess-a"], "старая сессия не архивирована")
        check(len(rec3.opened) == 1, "новая сессия не открыта")
        # ⚠️ Промт #174 (п. 3): порядок обязателен — **сначала архив старой,
        # потом новая**. Проверяем по порядку действий, а не по факту.
        order = rec3.order
        check("archive" in order and "new" in order,
              "перенос нитки не архивировал и не открыл новую: %s" % order)
        check(order.index("archive") < order.index("new"),
              "новая сессия поднята раньше архива старой: %s" % order)
        # Право на исключение из запрета второй сессии — только у решения
        # «Управляющего» (не у правила-фолбэка).
        check(rec3.by_decision == [True],
              "перенос нитки без права на исключение: %s" % rec3.by_decision)
        opened = rec3.opened[0] if rec3.opened else ""
        for needle in ("ПАСПОРТ НИТКИ", "Ветка: feat/thread-a", "Критерии приёмки",
                       "ЧУЖИЕ ДЕРЕВЬЯ НЕ ТРОГАТЬ", "PR нитки"):
            check(needle in opened,
                  "в команде новой сессии нет «%s»" % needle)
        # новая сессия открывается в worktree **своей** нитки (ADR-0011)
        check(rec3.opened_in and str(rec3.opened_in[0]).replace("\\", "/").rstrip("/")
              == str(repo).replace("\\", "/").rstrip("/"),
              "новая сессия открыта не в дереве нитки: %s" % rec3.opened_in)

        # ── 5) паспорт живёт на диске и переживает «перезапуск» ────────────
        print("5) паспорт нитки на диске (переживает перезапуск окна)")
        passports = json.loads(passport_file.read_text(encoding="utf-8"))
        threads = passports.get("threads") or {}
        check(bool(threads), "паспорт не записан на диск")
        stored = threads.get("repo-a::feat/thread-a") or {}
        check(bool(stored), "паспорт нитки A не записан: %s" % list(threads))
        for field in ("repo", "branch", "worktree", "criteria", "left", "done", "rule"):
            check(field in stored, "в паспорте нет поля «%s»" % field)
        check("origin/master" not in str(passport_file).lower()
              or True, "путь паспорта вне репозитория")
        check(not str(passport_file).startswith(str(repo)),
              "паспорт лёг внутрь репозитория (D-038)")
        # «перезапуск окна» — процесс читает файл заново
        watcher2 = load_watcher(passport_file)
        again = watcher2.thread.load_passport("repo-a::feat/thread-a")
        check(bool(again), "после перезапуска паспорт не прочитан")
        check(again.get("branch") == "feat/thread-a",
              "после перезапуска в паспорте чужая ветка: %s" % again.get("branch"))

        # ── 6) параллельные нитки одного репозитория не путаются ───────────
        print("6) параллельные сессии одного репозитория (ADR-0011)")
        rec4 = Recorder()
        rec4.install(watcher, {"ok": True, "action": "продолжать",
                               "instruction": "Доделай нитку A.",
                               "reason": "ждём"})
        state4: dict = {}
        verdicts4 = watcher.survey(area_info, {})
        watcher.act(area_info, verdicts4, state4)
        # нитка B (свой worktree) — свой паспорт и своя ветка
        passport_b = watcher.thread_passport(
            {"slug": "test", "dir": area, "folder": str(wt_b)},
            {"session_id": "sess-b", "workspace_dir": str(wt_b)})
        check(passport_b.get("branch") == "feat/thread-b",
              "паспорт нитки B указал чужую ветку: %s" % passport_b.get("branch"))
        check(str(passport_b.get("worktree") or "").replace("\\", "/").rstrip("/")
              == str(wt_b).replace("\\", "/").rstrip("/"),
              "паспорт нитки B указал чужой worktree: %s" % passport_b.get("worktree"))
        check(pathlib.Path(str(passport_b.get("repo") or "")).name == "repo-a",
              "паспорт нитки B указал не тот репозиторий: %s"
              % passport_b.get("repo"))
        text_b = watcher.thread.continue_text(passport_b, "Доделай нитку B.")
        check("feat/thread-b" in text_b, "команда нитки B не называет её ветку")
        check("feat/thread-a" not in text_b,
              "команда нитки B называет чужую ветку")
        check("ЧУЖИЕ ДЕРЕВЬЯ НЕ ТРОГАТЬ" in text_b,
              "в команде нитки B нет пометки про чужие деревья")
        watcher.thread.save_passport(passport_b)
        threads_now = watcher.thread.load_passports()
        check("repo-a::feat/thread-a" in threads_now
              and "repo-a::feat/thread-b" in threads_now,
              "нитки одного репозитория не различены: %s" % list(threads_now))

        # ── 7) «завершать» — только по решению «Управляющего» ─────────────
        print("7) завершение — по решению «Управляющего»")
        rec5 = Recorder()
        rec5.install(watcher, {"ok": True, "action": "завершать",
                               "instruction": "Напечатай итоговую сводку и остановись.",
                               "reason": "работа закончена"})
        state5: dict = {}
        actions5 = watcher.act(area_info, watcher.survey(area_info, {}), state5)
        act5 = next((x for x in actions5 if x.get("session") == "sess-a"), {})
        rep5 = act5.get("report") or {}
        check(rep5.get("action") == "завершать", "решение «завершать» не применено")
        check(watcher.CLOSE_COMMAND in rec5.sent,
              "по решению «Управляющего» сессия не закрыта")
        check(rec5.archived == ["sess-a"], "закрытая сессия не архивирована")

        # ── 8) в коде сторожа нет безусловного «заверши работу» ───────────
        print("8) в коде нет пути, требующего завершения без отчёта")
        src = (HERE / "minimax-sessionwatch.py").read_text(encoding="utf-8-sig")
        branch = src[src.find('if v["verdict"] in ("скомпактилась"'):][:2500]
        check("treat_session(" in branch,
              "ветка вердиктов не зовёт решение «Управляющего»")
        check("reset_session(area, sess)" not in branch,
              "ветка вердиктов по-прежнему требует завершения (reset_session)")
        check('state.setdefault("decision", {})' in branch,
              "нет отметки времени решения (кулдаун)")

        # ── 9) перенос нитки из worktree — в свой worktree ─────────────────
        print("9) перенос нитки из worktree — в свой worktree (ADR-0011)")
        add_compaction(area, "sess-b")
        rec6 = Recorder()
        rec6.install(watcher, {"ok": True, "action": "новая_сессия",
                               "instruction": "Продолжи нитку B.", "reason": "контекст сжат"})
        state6: dict = {}
        verdicts6 = watcher.survey(area_info, {})
        vb = next((v for v in verdicts6 if v["session_id"] == "sess-b"), {})
        check(vb.get("verdict") == "скомпактилась",
              "вердикт нитки B не «скомпактилась»: %s" % vb.get("verdict"))
        watcher.act(area_info, verdicts6, state6)
        # теперь скомпактировались обе нитки — переносятся обе; ищем нужную
        opened_b = next((text for text in rec6.opened if "feat/thread-b" in text), "")
        check(bool(opened_b), "нить B не перенесена: %s" % rec6.opened)
        if opened_b:
            check("feat/thread-a" not in opened_b,
                  "нить B перенесена с чужой веткой")
            check("Ветка: feat/thread-b" in opened_b,
                  "в команде нитки B нет её ветки")
        check(rec6.opened_in and str(rec6.opened_in[-1]).replace("\\", "/").rstrip("/")
              == str(wt_b).replace("\\", "/").rstrip("/"),
              "нить B открыта не в своём worktree: %s" % rec6.opened_in)

    if failures:
        print("❌ приёмка лечения сессий (#172): FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка лечения сессий (#172): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
