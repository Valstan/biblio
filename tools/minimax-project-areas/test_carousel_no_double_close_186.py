# -*- coding: utf-8 -*-
"""Приёмка: ночная карусель не командует закрытую сессию (промт КАРМАНА #186).

Живой случай, который чинится (27–28.11.2026): сессия `mvs_47c60cc5` («ЗАДАНИЕ
ОТ УПРАВЛЯЮЩЕГО — промт КАРМАНА #182») получила **36 команд закрытия**, 35 из
них — в цикле с 27.11 22:55 до 28.11 00:09, раз в ~1,5 минуты, — притом что её
работа была закончена (оба PR смержены, сводка о выполнении — в сессии есть).
Виновна была **ночная карусель полос**, а не сторож сессий: её каркас лежал
вне репозиториев, и правило «не командуй закрытую сессию» до неё не доходило.

Что проверяем (критерии приёмки промта #186):

1. **Сессия, чья работа уже закончена, не получает команду закрытия** — ни от
   карусели, ни от сторожа. Для карусели «работа закончена» — это её собственный
   список сданных промтов (`ls["done"]`).
2. **Сессия в состоянии «закрыта» архивируется один раз и повторно не
   подхватывается** (фаза закрытия достижима).
3. **Одно место правила:** сторож и карусель читают **одно** состояние
   (`session-sends-state.json`) ⇒ команду закрытия в одну сессию уходит
   **один раз**, кто бы её ни послал.
4. **В отчёте видно, что говорит встроенная нейросеть программы** (вид
   `model`), а в журнале — **кто принял решение** (модель или правило) и **кто
   исполнил**. Строка «по решению „Управляющий“» — только когда решение приняла
   модель.
5. **Кружение нитки:** сводка изменилась, а состояние работы (git) — нет ⇒
   новая сессия не создаётся (решает состояние работы, а не проза сводки).
6. **Фолбэк** не требует завершения без отчёта о выполнении и не пишет в
   закрытую сессию.

Каждый пункт — в **своей области** (своя база рантайма): иначе сессии соседних
пунктов мешают друг другу.

Запуск: python3 tools/minimax-project-areas/test_carousel_no_double_close_186.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time as time_module

HERE = pathlib.Path(__file__).resolve().parent
CAROUSEL = HERE / "carousel" / "matrica-night-carousel.py"

#: Схема базы рантайма — как у живого рантайма (включая `record_json`:
#: без него не работает архивирование сессии).
SCHEMA = """
create table if not exists local_runtime_sessions (
  session_id text primary key, status text, archived integer default 0,
  session_kind text, title text, workspace_dir text, created_at_ms integer,
  updated_at_ms integer, error_code integer, error_message text,
  extra_data_json text, record_json text);
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

#: Итоговая сводка агента (как в живой сессии: работа закончена).
FINAL_SUMMARY = (
    "Итог сессии\n"
    "-----------\n"
    "Сделано: промт КАРМАНА #182 выполнен и слит, приёмка зелёная.\n"
    "Не сделано: раскатка на машину остаётся за владельцем.\n"
    "Дальше: сверить origin/master и закрыть смену.\n"
    "Сессия закрыта, незакрытых дел нет."
)

#: Другая сводка — работа та же, проза другая (проверка кружения нитки).
OTHER_SUMMARY = (
    "Итог сессии\n"
    "-----------\n"
    "Сделано: всё то же самое, второй раз пересказал.\n"
    "Не сделано: ничего.\n"
    "Дальше: ничего — уже всё сделано.\n"
    "Сессия закрыта, незакрытых дел нет."
)

#: Заголовок сессии карусели: в нём — номер промта и маркер «Управляющего».
#: ⚠️ Сессии с таким заголовком сторож сессий НЕ трогает («сессия Управляющего
#: — автомат её не трогает») ⇒ для проверок сторожа берём обычный заголовок.
TITLE = ("ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — промт КАРМАНА #182, полоса 1 "
         "«МиниГен (разработка)».")
#: Обычный заголовок сессии (без маркера «Управляющего») — для проверок сторожа.
TITLE_PLAIN = "промт КАРМАНА #182"


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


def add_session(area: pathlib.Path, repo: pathlib.Path, sid: str,
                kind: str = "finished", title: str = TITLE,
                summary: str = FINAL_SUMMARY, error_code=None,
                error_message: str = "") -> None:
    """Сессия в базе рантайма.

    `kind`:
      * ``finished`` — ход завершён, на него есть финальный ответ ⇒ работа
        закончена (именно такой была живая сессия `mvs_47c60cc5`);
      * ``running`` — ход идёт (агент работает — закрывать нельзя);
      * ``abandoned`` — ход завершён, финального ответа нет.
    """
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    now = int(time_module.time() * 1000)
    try:
        con.execute("insert into local_runtime_sessions values "
                    "(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, "idle", 0, "conversation", title, str(repo),
                     now - 3600000, now - 600000, error_code, error_message,
                     '{"effectiveModelContextWindow": 1000000}', "{}"))
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
            con.execute("insert into local_runtime_message_rows "
                        "(session_id, role, created_at_ms, data_json) values (?,?,?,?)",
                        (sid, "assistant", now - 600000,
                         json.dumps({"msg_content": summary, "msg_type": 1},
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


def add_turn(area: pathlib.Path, session_id: str, turn_id: str) -> None:
    """Новый завершённый ход сессии (например, ход «закрывающей» команды)."""
    db = area / "v2" / "sqlite" / "runtime-state.sqlite"
    con = sqlite3.connect(str(db))
    now = int(time_module.time() * 1000)
    try:
        con.execute("insert into local_runtime_turn_ingress values (?,?,?,?,?,?)",
                    (turn_id, session_id, "completed", now + 1000, now + 2000, "{}"))
        con.commit()
    finally:
        con.close()


def add_compaction(area: pathlib.Path, session_id: str) -> None:
    """Событие сжатия контекста рантайма (вердикт «скомпактилась»)."""
    log_dir = area / "v2" / "observability" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time_module.strftime("%H:%M:%S", time_module.localtime(time_module.time() - 60))
    line = ('2026-11-28 [%s] {"session_id": "%s", "event": '
            '"context_compaction_checkpoint_generated"}' % (stamp, session_id))
    (log_dir / "runtime-test.log").write_text(line + "\n", encoding="utf-8")


class FakeProc:
    """Процесс `mcode exec`, который «пошёл»."""

    pid = 4242

    def poll(self):
        return None


class FakeTime:
    """Время без ожидания (сторож спит по 3 и по 10 секунд)."""

    def __getattr__(self, name):
        return getattr(time_module, name)

    def sleep(self, seconds):        # noqa: D401 — так и надо
        return None


class Recorder:
    """Ловит всё, что уходит в сессии, и всё, что пишется в журнал/отчёты.

    ⚠️ Важно: `send_to_session` **не подменяется** — иначе проверка стражи
    «работа уже закончена» прошла бы по неверной причине. Подменяется только
    запуск процесса (`core.mcode_run`).
    """

    def __init__(self) -> None:
        self.sent: list[tuple] = []        # (session_id, текст)
        self.archived: list[str] = []
        self.opened: list[str] = []
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

        def fake_ask(facts, area="", timeout=120):
            rec.ask_calls.append(facts)
            if rec.answer is None:
                return {"ok": False, "why": "модель недоступна"}
            return dict(rec.answer)

        def fake_new(area, prompt, force=False, folder="", by_decision=False):
            rec.opened.append(prompt)
            return {"ok": True, "why": "новая сессия пошла"}

        watcher.core.mcode_run = fake_run
        watcher.core.log = lambda msg, *a, **k: rec.logs.append(str(msg))
        watcher.core.report = lambda level, kind, text, area="": rec.reports.append(
            (level, kind, text))
        watcher.core.refresh_env = lambda: {}
        watcher.new_session = fake_new
        watcher.wait_closed = lambda *a, **k: True
        watcher.thread.ask_manager = fake_ask
        watcher.thread.open_prs = lambda folder, branch: []
        watcher.time = FakeTime()
        # Архивирование — настоящее (оно пишет в базу рантайма), но со счётом:
        # «архивирована один раз» — проверяемый результат.
        real_archive = watcher.archive_session

        def counting_archive(area_dir, session_id):
            rec.archived.append(session_id)
            return real_archive(area_dir, session_id)

        watcher.archive_session = counting_archive

    def sent_texts(self) -> list:
        return [text for _sid, text in self.sent]

    def journal_has(self, needle: str) -> bool:
        return any(needle in line for line in self.logs)

    def report_kinds(self) -> list:
        return [kind for _level, kind, _text in self.reports]


def load_carousel(tmp: pathlib.Path, broken: str = ""):
    """Каркас карусели — из репозитория (источник истины), с временным каталогом.

    `broken` — «временная правка»: что вырезать из кода, чтобы проверить,
    что приёмка **падает** без этой защиты.
    """
    source = CAROUSEL.read_text(encoding="utf-8-sig")
    original = source
    if broken == "finished":
        # Ломаем проверку «работа сессии уже закончена» — приёмка должна это
        # заметить (иначе проверка проходит по неверной причине).
        # ⚠️ Проверка стоит в ДВУХ местах (защита в два слоя): в разборе полосы
        #    (`handle_lane`) и в самой команде (`close_session`). Чтобы получить
        #    исходный дефект (команда закрытия в сессию с законченной работой),
        #    вырезаем оба.
        source = source.replace(
            "if (not pending and pid_done\n"
            "            and pid_done in [int(x) for x in (ls.get(\"done\") or [])]):",
            "if False:")
        source = source.replace(
            "if pid and pid in [int(x) for x in (done_prompts or [])]:",
            "if False:")
    if broken:
        # Сломанная копия лежит во временном каталоге ⇒ путь к каталогу
        # инструментов репозитория указываем явно (иначе каркас не найдёт
        # сторожа рядом с собой).
        source = source.replace(
            "REPO_TOOLS = Path(__file__).resolve().parent.parent",
            "REPO_TOOLS = Path(%r)" % str(HERE))
    if broken and source == original:
        raise SystemExit("временная правка «%s» не применилась — код поменялся, "
                         "приёмку надо поправить" % broken)
    path = CAROUSEL
    if broken:
        path = pathlib.Path(tempfile.gettempdir()) / ("carousel-186-broken-%s.py" % broken)
        path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "carousel_186%s" % (("_" + broken) if broken else ""), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOCAL = tmp
    mod.LOG = tmp / "carousel.log"
    mod.STATE = tmp / "carousel-state.json"
    return mod


def lane_state(done: list = None, pending: int = 0, cur: dict = None) -> dict:
    """Состояние полосы карусели (как в `default_state`)."""
    return {"n": 1, "slug": "test", "title": "ТЕСТ",
            "queue": [], "reserve": [],
            "cur": cur, "pending": pending, "pending_since": 0,
            "done": list(done or []), "failed": [], "tries": {},
            "not_before": 0, "dead_turn": ""}


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        car = load_carousel(tmp)
        watcher = car.sw
        rec = Recorder()
        rec.install(watcher)
        # Общее состояние — во временном каталоге: живое состояние машины не трогаем.
        car.core.SESSION_SENDS_STATE = tmp / "sends.json"
        watcher.core.SESSION_SENDS_STATE = car.core.SESSION_SENDS_STATE
        try:
            import os as os_module
            os_module.environ["MINIGEN_PASSPORTS_FILE"] = str(tmp / "passports.json")
        except Exception:  # noqa: BLE001
            pass

        def scenario(name: str):
            """Своя область (и свой репозиторий) на каждый пункт."""
            root = tmp / name
            repo = make_repo(root, "repo")
            area_dir = make_area(root, "p-test")
            car.core.AREAS_ROOT = root
            lane = {"n": 1, "slug": "test", "title": "ТЕСТ", "folder": str(repo),
                    "queue": [], "reserve": []}
            area = {"slug": "test", "dir": area_dir, "folder": str(repo),
                    "title": "ТЕСТ", "autoheal": True}
            return lane, area, area_dir, repo

        # ── 1) Работа закончена: команда закрытия НЕ уходит, сессия архивируется один раз ──
        print("1) работа уже закончена: команда закрытия не уходит, архив один раз")
        lane, area, area_dir, repo = scenario("s1")
        add_session(area_dir, repo, "sess-fin", "finished", title=TITLE)
        ls = lane_state(done=[182], pending=0)
        snap = car.lane_snapshot(lane)
        result = car.handle_lane(lane, ls, snap, time_module.time())
        check(result == "free",
              "полоса не освободилась после архива: %s" % result)
        check(not rec.sent_texts(),
              "в сессию с законченной работой ушла команда: %s" % rec.sent_texts()[:1])
        check(rec.archived == ["sess-fin"],
              "завершённая сессия не архивирована: %s" % rec.archived)
        check("команда закрытия сессии" not in (tmp / "carousel.log").read_text(encoding="utf-8"),
              "в журнале карусели есть команда закрытия для законченной работы")
        # Второй проход: сессия уже архивирована — подхвата её нельзя.
        rec.archived.clear()
        ls2 = lane_state(done=[182], pending=0)
        result = car.handle_lane(lane, ls2, car.lane_snapshot(lane), time_module.time())
        check(result == "free", "полоса не свободна на втором проходе: %s" % result)
        check(not rec.archived,
              "сессия архивирована повторно: %s" % rec.archived)
        check(not rec.sent_texts(),
              "на втором проходе ушла команда: %s" % rec.sent_texts()[:1])

        # ── 2) Обычный порядок: команда закрытия уходит ОДИН раз, фаза «закрываем» достижима ──
        print("2) обычный порядок: команда закрытия одна, сессия архивирована один раз")
        lane, area, area_dir, repo = scenario("s2")
        add_session(area_dir, repo, "sess-run", "finished", title=TITLE)
        ls = lane_state(done=[], pending=182)
        car.handle_lane(lane, ls, car.lane_snapshot(lane), time_module.time())
        check(len(rec.sent_texts()) == 1,
              "команда закрытия ушла %d раз (ожидана 1)" % len(rec.sent_texts()))
        check((ls.get("cur") or {}).get("phase") == "closing",
              "фаза «закрываем» недостижима: %s" % (ls.get("cur") or {}))
        # Ход «закрывающей» команды завершился ⇒ сессия архивируется (один раз).
        add_turn(area_dir, "sess-run", "turn-close-1")
        result = car.handle_lane(lane, ls, car.lane_snapshot(lane), time_module.time())
        check(result == "free", "после закрытия полоса не свободна: %s" % result)
        check(rec.archived == ["sess-run"],
              "сессия после закрытия не архивирована: %s" % rec.archived)
        check(len(rec.sent_texts()) == 1,
              "команда закрытия ушла повторно: %d" % len(rec.sent_texts()))

        # ── 3) Одно место правила: команда закрытия в одну сессию — один раз (кто ни послал) ──
        print("3) одно правило: команду закрытия в одну сессию шлют один раз")
        lane, area, area_dir, repo = scenario("s3")
        add_session(area_dir, repo, "sess-both", "abandoned", title=TITLE_PLAIN)
        # Сторож отправил команду закрытия (в сессию, которая ещё жива).
        first = watcher.send_to_session(area, {"session_id": "sess-both",
                                               "workspace_dir": str(repo)},
                                        watcher.CLOSE_COMMAND, background=True,
                                        force=True, is_close=True, tag="закрытие:тест",
                                        why="команда закрытия по решению «Управляющий»")
        check(first.get("ok") is True, "сторож не отправил команду закрытия: %s" % first)
        # Карусель хочет закрыть ту же сессию — нельзя: команда закрытия уже уходила.
        before = len(rec.sent)
        ok = car.close_session(lane, "sess-both", done_prompts=[])
        check(not ok, "карусель отправила команду закрытия повторно")
        check(len(rec.sent) == before,
              "карусель отправила команду закрытия в ту же сессию: %s" % rec.sent[before:])
        check(watcher.CLOSE_ONCE_NOTE in (tmp / "carousel.log").read_text(encoding="utf-8"),
              "в журнале нет «%s»" % watcher.CLOSE_ONCE_NOTE)
        # И наоборот: карусель отправила — сторож уже не может.
        lane2, area2, area_dir2, repo2 = scenario("s3b")
        add_session(area_dir2, repo2, "sess-both2", "abandoned", title=TITLE_PLAIN)
        car.close_session(lane2, "sess-both2", done_prompts=[])
        before = len(rec.sent)
        second = watcher.send_to_session(area2, {"session_id": "sess-both2",
                                                 "workspace_dir": str(repo2)},
                                         watcher.CLOSE_COMMAND, background=True,
                                         force=True, is_close=True, tag="закрытие:тест",
                                         why="команда закрытия по решению «Управляющий»")
        check(not second.get("ok"), "сторож отправил команду закрытия повторно")
        check(len(rec.sent) == before,
              "сторож отправил команду закрытия в ту же сессию: %s" % rec.sent[before:])
        # Лекарка — одна на всех: тот же файл состояния.
        ledger = watcher.sends_ledger()
        check("sess-both" in ledger and "sess-both2" in ledger,
              "лекарка команд не общая: %s" % list(ledger.keys()))

        # ── 4) Две роли: в отчёте видно нейросеть, в журнале — кто решил и кто исполнил ──
        print("4) две роли: отчёт говорит «нейросеть», журнал — кто решил и кто исполнил")
        lane, area, area_dir, repo = scenario("s4")
        add_session(area_dir, repo, "sess-model", "abandoned", title=TITLE_PLAIN)
        add_compaction(area_dir, "sess-model")
        rec.answer = {"ok": True, "action": watcher.thread.CONTINUE,
                      "instruction": "продолжай", "reason": "вывод сессии"}
        state = {}
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        check(watcher.core.KIND_MODEL in rec.report_kinds(),
              "в отчёте нет вида «работа встроенной нейросети» (%s): %s"
              % (watcher.core.KIND_MODEL, rec.report_kinds()))
        model_reports = [text for _level, kind, text in rec.reports
                         if kind == watcher.core.KIND_MODEL]
        check(model_reports and "Управляющий" in model_reports[-1],
              "отчёт о решении нейросети не называет «Управляющий»: %s" % model_reports[-1:])
        check(rec.journal_has("решение принял «Управляющий»"),
              "в журнале нет «решение принял «Управляющий»»: %s" % rec.logs[-3:])
        check(rec.journal_has("исполнил «сторож сессий»"),
              "в журнале нет «исполнил «сторож сессий»»: %s" % rec.logs[-3:])

        # Правило-фолбэк: решение приняло правило — «Управляющий» в журнале быть не должно.
        lane, area, area_dir, repo = scenario("s4b")
        add_session(area_dir, repo, "sess-rule", "abandoned", title=TITLE_PLAIN)
        add_compaction(area_dir, "sess-rule")
        rec.answer = None                      # модель недоступна ⇒ правило-фолбэк
        rec.logs.clear()
        rec.reports.clear()
        state = {}
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        check(rec.journal_has("правило-фолбэк"),
              "в журнале нет имени настоящего источника решения: %s" % rec.logs[-3:])
        check(not rec.journal_has("решение принял «Управляющий»"),
              "решение по правилу названо решением «Управляющего» (ложь): %s" % rec.logs[-3:])
        check(watcher.core.KIND_MODEL not in rec.report_kinds(),
              "решение правила-фолбэка помечено как решение нейросети: %s"
              % rec.report_kinds())

        # ── 5) Кружение нитки: сводка изменилась, состояние работы — нет ──
        print("5) кружение нитки: сводка изменилась, работа та же ⇒ сессии нет")
        lane, area, area_dir, repo = scenario("s5")
        add_session(area_dir, repo, "sess-rot1", "finished", title=TITLE_PLAIN)
        rec.answer = {"ok": True, "action": watcher.thread.CONTINUE,
                      "instruction": "продолжи", "reason": "работа закончена"}
        state = {}
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        check(len(rec.opened) == 1,
              "первый перенос не открыл новую сессию: %d" % len(rec.opened))
        # Вторая завершённая сессия той же нитки: сводка ДРУГАЯ, работа та же.
        add_session(area_dir, repo, "sess-rot2", "finished", title=TITLE_PLAIN,
                    summary=OTHER_SUMMARY)
        rec.opened.clear()
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        check(not rec.opened,
              "нить крутится: сводка изменилась, работа — нет, а сессия новая: %s"
              % rec.opened[:1])
        check(rec.journal_has("нитка не продвинулась"),
              "в журнале нет «нитка не продвинулась»: %s" % rec.logs[-3:])

        # ── 6) Фолбэк: без отчёта о выполнении завершения не требуем и в закрытую сессию не пишем ──
        print("6) фолбэк: завершения без отчёта нет, в закрытую сессию команда не уходит")
        lane, area, area_dir, repo = scenario("s6")
        add_session(area_dir, repo, "sess-closed", "finished", title=TITLE_PLAIN)
        rec.answer = None                      # модель недоступна ⇒ правило-фолбэк
        rec.logs.clear()
        rec.sent.clear()
        res = watcher.send_to_session(area, {"session_id": "sess-closed",
                                             "workspace_dir": str(repo)},
                                      "Продолжай нитку.", background=True,
                                      tag="проверка")
        check(not res.get("ok"), "в закрытую сессию ушла команда: %s" % res)
        check(not rec.sent, "в закрытую сессию ушла команда: %s" % rec.sent[:1])
        check(watcher.CLOSED_SESSION_NOTE in str(res.get("why") or ""),
              "в ответе нет «%s»: %s" % (watcher.CLOSED_SESSION_NOTE, res.get("why")))
        # Правило-фолбэк без отчёта о выполнении — «продолжать», не «завершать».
        fb = watcher.thread.fallback_decision({"has_report": False})
        check(fb.get("action") == watcher.thread.CONTINUE,
              "фолбэк без отчёта требует завершения: %s" % fb)

        # ── 7) Смена аккаунта: ровно одно «Продолжай» на смену, тег счётчик не ломает ──
        print("7) смена аккаунта: ровно одно «Продолжай» на смену (тег не ломает счётчик)")
        lane, area, area_dir, repo = scenario("s7")
        add_session(area_dir, repo, "sess-bal", "abandoned", title=TITLE_PLAIN,
                    error_code=50110, error_message="insufficient balance")
        healed = {"last_heal": {area["slug"]: time_module.time()}}
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
            watcher.act(area, verdicts, state)
        balance_sends = [t for t in rec.sent_texts() if "Продолжай нитку" in t]
        check(len(balance_sends) == 1,
              "на смену аккаунта ушло «Продолжай» %d раз (ожидано 1)"
              % len(balance_sends))
        # ⚠️ НОВАЯ смена аккаунта — тег другой ⇒ «Продолжай» обязан уйти ещё раз
        # (иначе тег сломал счётчик смен аккаунта).
        healed["last_heal"][area["slug"]] = time_module.time() + 10
        state = {}
        verdicts = watcher.survey(area, state)
        watcher.act(area, verdicts, state)
        balance_sends = [t for t in rec.sent_texts() if "Продолжай нитку" in t]
        check(len(balance_sends) == 2,
              "после новой смены аккаунта «Продолжай» не ушёл (тег сломал счётчик): %d"
              % len(balance_sends))
        watcher.core.read_state = real_read_state

        # ── 8) Временная правка: без проверки «работа закончена» приёмка падает ──
        print("8) временная правка: без проверки «работа закончена» приёмка падает")
        broken = load_carousel(tmp, broken="finished")
        rec2 = Recorder()
        rec2.install(broken.sw)
        broken.core.SESSION_SENDS_STATE = tmp / "sends-broken.json"
        broken.sw.core.SESSION_SENDS_STATE = broken.core.SESSION_SENDS_STATE
        root = tmp / "s7"
        repo = make_repo(root, "repo")
        area_dir = make_area(root, "p-test")
        broken.core.AREAS_ROOT = root
        lane = {"n": 1, "slug": "test", "title": "ТЕСТ", "folder": str(repo),
                "queue": [], "reserve": []}
        add_session(area_dir, repo, "sess-broken", "finished", title=TITLE)
        ls = lane_state(done=[182], pending=0)
        broken.handle_lane(lane, ls, broken.lane_snapshot(lane), time_module.time())
        check(rec2.sent_texts(),
              "сломаная копия карусели НЕ отправила команду закрытия — "
              "значит, проверка проходит по неверной причине")
        check(any("ИТОГОВУЮ СВОДКУ" in t for t in rec2.sent_texts()),
              "сломаная копия отправила не ту команду: %s" % rec2.sent_texts()[:1])

    if failures:
        print("❌ приёмка «карусель не командует закрытую сессию» (#186): FAIL (%d)"
              % len(failures))
        return 1
    print("✅ приёмка «карусель не командует закрытую сессию» (#186): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
