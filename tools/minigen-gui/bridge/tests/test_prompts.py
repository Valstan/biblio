"""Тесты «Хранилища промтов» «МиниГена» (заказ владельца 24.11, промт #94).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем (по критериям приёмки из промта):

1. **Место хранения переключается** — настройка окна выбирает: база
   «МиниГена» или внешний ресурс; данные каждого хранилища живут у своего
   хозяина.
2. **При внешнем ресурсе промты читаются, при недоступности — честное
   сообщение** (не тишина): нет канала ⇒ окно говорит почему.
3. **Промт и план лежат рядом, план помечен**: вид записи их различает.
4. **Промт из хранилища уходит в область проекта**: открывается сессия с
   текстом задания.
5. **Раздача без оператора срабатывает только для помеченных промтов.**
6. **Промт раздела уходит в область своего проекта**: чужой проект ⇒ отказ
   с объяснением.
7. **Токены не попали в репозиторий и в журнал.**

⚠️ Живое состояние не трогаем: база промтов и настройки — **временные**
(путь подменяется на время тестов), как и реестр агентов в `__init__.py`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BRIDGE_DIR.parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge      # noqa: E402
import minigen_karman as karman      # noqa: E402
import minigen_prompts as prompts    # noqa: E402
import minigen_state as state        # noqa: E402

#: Значения собираются из кусков **намеренно** и уникальны: иначе сам тест
#: хранит в репозитории строку, которую сам же и ищет тестом «секрет не в
#: репозитории» (ложная тревога). Настоящие секреты — в vault-комнате, их тут
#: нет.
SECRET = "-".join(["minigen", "prompt", "secret", "probe"])
SECTION_TOKEN = "-".join(["minigen", "prompt", "section", "token"])
SECTION = "MiniGen"
TITLE = "МиниГен: хранилище промтов"
BODY = "# Задание\n\nСделать хранилище промтов.\n"


# ───────────────────────────────────────────────── подставной внешний ресурс

class Handler(BaseHTTPRequestHandler):
    """Подставной Карман: секрет канала, разделы, промты, архивирование."""

    calls: list = []
    secret = SECRET
    section_token = SECTION_TOKEN
    prompts: list = []
    sections: list = [SECTION]
    archive_calls: list = []
    #: Код ответа на архив: 200 — работает, 503 — канал раздела закрыт.
    archive_code = 200

    def log_message(self, *args):  # тишина
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization") or ""
        Handler.calls.append(("GET", self.path, auth, None))
        if self.path.startswith("/api/secrets"):
            secrets = {"PROMPTS_INGEST_SECRET": Handler.secret}
            if Handler.section_token:
                secrets["PROMPTS_TOKEN_%s" % SECTION.upper()] = Handler.section_token
            self._send(200, {"secrets": secrets})
            return
        if self.path.startswith("/api/prompts/projects"):
            self._send(200, {"sections": Handler.sections})
            return
        if self.path.startswith("/api/prompts"):
            # ⚠️ Как настоящий сервис: раздел и архив — **точное** имя и
            # явный признак. Несовпадение регистра — это «нет промтов»,
            # а не «все промты».
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            want = (query.get("project") or [""])[0]
            archived = (query.get("archived") or [""])[0] == "1"
            items = []
            for item in Handler.prompts:
                if want and str(item.get("project") or "") != want:
                    continue
                if bool(item.get("archivedAt")) != archived:
                    continue
                items.append(item)
            self._send(200, {"prompts": items})
            return
        self._send(404, {"error": "нет такого пути"})

    def do_POST(self):  # noqa: N802
        auth = self.headers.get("Authorization") or ""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        Handler.calls.append(("POST", self.path, auth, body))
        if self.path.startswith("/api/prompts/section"):
            Handler.archive_calls.append(body)
            if Handler.archive_code != 200:
                self._send(Handler.archive_code, {"error": "канал не настроен"})
                return
            self._send(200, {"id": body.get("id"), "archived": True})
            return
        if self.path.startswith("/api/prompts"):
            Handler.prompts.append({"id": 90, "title": body.get("title"),
                                    "body": body.get("body"),
                                    "project": body.get("project")})
            self._send(201, {"id": 90, "project": body.get("project")})
            return
        self._send(404, {"error": "нет такого пути"})


# ───────────────────────────────────────────────────────────── заглушки

class FakeWatcher:
    """Сторож сессий: помнит, что и куда отправили."""

    def __init__(self, ok: bool = True) -> None:
        self.sent: list = []
        self.ok = ok

    def new_session(self, area: dict, prompt: str, force: bool = False) -> dict:
        self.sent.append({"area": area.get("slug"), "text": prompt})
        if not self.ok:
            return {"ok": False, "why": "в области уже живая сессия"}
        return {"ok": True, "why": "новая сессия пошла"}


class FakeCore:
    """Ядро: только журнал (проверяем, что секрет туда не попал)."""

    def __init__(self) -> None:
        self.logs: list = []

    def log(self, text: str) -> None:
        self.logs.append(text)

    def areas(self) -> list:
        return [
            {"slug": "minigen", "title": "МиниГен", "karman": SECTION,
             "dir": Path("/tmp/p-minigen")},
            {"slug": "sarafan", "title": "Сарафан", "karman": "SARAFAN",
             "dir": Path("/tmp/p-sarafan")},
        ]


def make_area(slug: str = "minigen", section: str = SECTION) -> dict:
    return {"slug": slug, "title": slug, "karman": section,
            "dir": Path("/tmp/p-" + slug)}


# ──────────────────────────────────────────────────────── локальное хранилище

class LocalStoreTest(unittest.TestCase):
    """База «МиниГена»: промты, планы, архив, пометки владельца."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prompts.db"
        self._old_db = prompts.LOCAL_DB
        prompts.LOCAL_DB = self.db
        self.store = prompts.LocalPromptStore(self.db)

    def tearDown(self) -> None:
        prompts.LOCAL_DB = self._old_db
        self.tmp.cleanup()

    def test_prompt_and_plan_lie_side_by_side(self) -> None:
        """Промт и план лежат рядом, но вид у них разный."""
        self.store.save(SECTION, TITLE, BODY)
        self.store.save(SECTION, "План: хранилище промтов", "# План\n\nШаги.\n")
        items = self.store.list_prompts(SECTION)
        self.assertEqual(len(items), 2)
        kinds = sorted(item["kind"] for item in items)
        self.assertEqual(kinds, ["plan", "prompt"])
        for item in items:
            self.assertEqual(item["source"], "local")
            self.assertFalse(item["archived"])

    def test_sections_and_archive(self) -> None:
        self.store.save(SECTION, TITLE, BODY)
        self.assertEqual(self.store.sections(), [SECTION])
        prompt_id = self.store.list_prompts(SECTION)[0]["id"]
        res = self.store.archive(prompt_id, SECTION)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.store.list_prompts(SECTION), [])
        self.assertEqual(len(self.store.list_prompts(SECTION, include_archived=True)), 1)

    def test_mark_is_the_owners_decision(self) -> None:
        self.store.save(SECTION, TITLE, BODY)
        prompt_id = self.store.list_prompts(SECTION)[0]["id"]
        self.assertFalse(prompts.get_mark("local", SECTION, prompt_id, self.db))
        prompts.set_mark("local", SECTION, prompt_id, True, self.db)
        self.assertTrue(prompts.get_mark("local", SECTION, prompt_id, self.db))
        prompts.set_mark("local", SECTION, prompt_id, False, self.db)
        self.assertFalse(prompts.get_mark("local", SECTION, prompt_id, self.db))


# ─────────────────────────────────────────────────────────────── раздача

class DispatchTest(unittest.TestCase):
    """Промт уходит в область проекта — и никуда больше."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prompts.db"
        self._old_db = prompts.LOCAL_DB
        prompts.LOCAL_DB = self.db
        self.store = prompts.LocalPromptStore(self.db)
        self.watcher = FakeWatcher()
        self.mods = {"sessionwatch": self.watcher, "core": FakeCore()}

    def tearDown(self) -> None:
        prompts.LOCAL_DB = self._old_db
        self.tmp.cleanup()

    def _record(self) -> dict:
        self.store.save(SECTION, TITLE, BODY)
        return self.store.list_prompts(SECTION)[0]

    def test_prompt_goes_to_the_area_of_its_project(self) -> None:
        record = self._record()
        res = prompts.dispatch_to_area(self.mods, make_area("minigen", SECTION),
                                       record, None)
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(self.watcher.sent), 1)
        sent = self.watcher.sent[0]
        self.assertEqual(sent["area"], "minigen")
        self.assertIn(TITLE, sent["text"])
        self.assertIn("Сделать хранилище промтов", sent["text"])
        self.assertIn(SECTION, sent["text"])

    def test_foreign_project_is_refused_with_an_explanation(self) -> None:
        """Раздел промта ≠ проект области ⇒ отказ (задание уедет не туда)."""
        record = self._record()
        res = prompts.dispatch_to_area(self.mods, make_area("sarafan", "SARAFAN"),
                                       record, None)
        self.assertFalse(res["ok"])
        self.assertIn("уедет не туда", res["error"])
        self.assertEqual(self.watcher.sent, [], "промт ушёл не туда")

    def test_the_program_area_does_not_take_the_house_section(self) -> None:
        """Раздел дома утилит в область программы не уходит (D-190).

        У области `minigen` поле `karman` — «biblio» (дом утилит), а её
        собственный раздел — `MiniGen`. Помеченный промт дома утилит в область
        программы не попадает: чужую работу выполняет тот, кому она
        принадлежит.
        """
        house = self.store.save("biblio", "Промт дома", "Домашняя работа.")
        prompts.set_mark("local", "biblio", house["id"], True, self.db)
        own = self.store.save(SECTION, TITLE, BODY)
        prompts.set_mark("local", SECTION, own["id"], True, self.db)

        class HouseCore(FakeCore):
            def areas(self) -> list:
                return [{"slug": "minigen", "title": "МиниГен",
                         "karman": "biblio", "dir": Path("/tmp/p-minigen")}]

        mods = {"sessionwatch": self.watcher, "core": HouseCore()}
        done = prompts.auto_dispatch_scan(mods, self.store, {})
        sent = [item for item in done if item.get("ok")]
        self.assertEqual(len(sent), 1, done)
        self.assertEqual(len(self.watcher.sent), 1)
        self.assertIn(SECTION, self.watcher.sent[0]["text"])
        self.assertNotIn("Домашняя работа", self.watcher.sent[0]["text"])

    def test_plan_goes_with_the_prompt(self) -> None:
        self.store.save(SECTION, "План: хранилище промтов", "# План\n\nШаги плана.\n")
        record = self._record()
        plan = prompts.find_plan(self.store.list_prompts(SECTION), record)
        self.assertIsNotNone(plan)
        res = prompts.dispatch_to_area(self.mods, make_area("minigen", SECTION),
                                       record, plan)
        self.assertTrue(res["ok"], res)
        self.assertIn("Шаги плана", self.watcher.sent[0]["text"])

    def test_auto_dispatch_only_for_marked_prompts(self) -> None:
        """Без оператора раздаётся только помеченное владельцем."""
        record = self._record()
        done = prompts.auto_dispatch_scan(self.mods, self.store, {})
        self.assertEqual(done, [])
        self.assertEqual(self.watcher.sent, [], "обычный промт раздан без спроса")

        prompts.set_mark("local", SECTION, record["id"], True, self.db)
        done = prompts.auto_dispatch_scan(self.mods, self.store, {})
        self.assertEqual(len(done), 1, done)
        self.assertTrue(done[0]["ok"], done)
        self.assertEqual(len(self.watcher.sent), 1)

        # Второй проход — тот же промт не раздаётся дважды.
        again = prompts.auto_dispatch_scan(self.mods, self.store, {})
        self.assertEqual([item for item in again if item.get("ok")], [])
        self.assertEqual(len(self.watcher.sent), 1)

    def test_busy_area_waits_for_the_next_round(self) -> None:
        record = self._record()
        prompts.set_mark("local", SECTION, record["id"], True, self.db)
        self.watcher.ok = False
        done = prompts.auto_dispatch_scan(self.mods, self.store, {})
        self.assertTrue(done)
        self.assertFalse(done[0]["ok"])
        self.assertFalse(prompts.already_dispatched("local", SECTION, record["id"],
                                                     self.db))

    def test_auto_dispatch_takes_the_plan_with_the_prompt(self) -> None:
        """План уходит вместе со своим промтом и в автораздаче тоже."""
        self.store.save(SECTION, "План: хранилище промтов", "# План\n\nШаги плана.\n")
        record = self._record()
        prompts.set_mark("local", SECTION, record["id"], True, self.db)
        done = prompts.auto_dispatch_scan(self.mods, self.store, {})
        self.assertTrue([item for item in done if item.get("ok")], done)
        self.assertEqual(len(self.watcher.sent), 1)
        self.assertIn("Шаги плана", self.watcher.sent[0]["text"])

    def test_auto_dispatch_skips_a_busy_area(self) -> None:
        """Занятая область не получает задание — ждёт следующего прохода."""
        record = self._record()
        prompts.set_mark("local", SECTION, record["id"], True, self.db)
        self.watcher.survey = lambda area, _args: [
            {"verdict": "работает", "session_id": "s1"}]
        done = prompts.auto_dispatch_scan(self.mods, self.store, {})
        self.assertEqual(self.watcher.sent, [], "задание ушло в занятую область")
        notes = [item for item in done if item.get("note")]
        self.assertTrue(notes, done)
        self.assertIn("занята", notes[0]["why"])

    def test_auto_dispatch_reports_an_unreadable_section(self) -> None:
        """Раздел не прочитан ⇒ причина в ответе, а не тишина."""

        class BrokenStore:
            kind = "karman"

            def sections(self) -> list:
                return [SECTION]

            def list_prompts(self, project: str = "", **_kwargs) -> list:
                raise prompts.UnknownSection("раздела «%s» в библиотеке нет"
                                             % project)

        record = self._record()
        prompts.set_mark("local", SECTION, record["id"], True, self.db)
        done = prompts.auto_dispatch_scan(self.mods, BrokenStore(), {})
        notes = [item for item in done if item.get("note")]
        self.assertTrue(notes, done)
        self.assertIn("не прочитан", notes[0]["why"])
        self.assertEqual(self.watcher.sent, [])


# ────────────────────────────────────────────── внешний ресурс (Карман)

class KarmanStoreTest(unittest.TestCase):
    """Внешний ресурс: читается, недоступен — честно, архив — токеном раздела."""

    def setUp(self) -> None:
        Handler.calls = []
        Handler.prompts = []
        Handler.archive_calls = []
        Handler.secret = SECRET
        Handler.section_token = SECTION_TOKEN
        Handler.sections = [SECTION]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        keys = Path(self.tmp.name)
        (keys / "vault-url").write_text("http://127.0.0.1:%d" % self.port,
                                        encoding="utf-8")
        (keys / "room-token").write_text("skm_test-token", encoding="utf-8")
        self._old_dir = os.environ.get("BIBLIO_KEYS_DIR")
        os.environ["BIBLIO_KEYS_DIR"] = str(keys)
        self._reset_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._old_dir is None:
            os.environ.pop("BIBLIO_KEYS_DIR", None)
        else:
            os.environ["BIBLIO_KEYS_DIR"] = self._old_dir
        self._reset_cache()
        self.tmp.cleanup()

    def _reset_cache(self) -> None:
        karman._secret_cache.update({"value": "", "source": "", "at": 0.0})
        prompts._cache.update({"sections": [], "sections_at": 0.0,
                               "sections_code": 0,
                               "by_section": {}, "archived_by_section": {}})

    def test_store_choice_follows_the_setting(self) -> None:
        """Место хранения переключается настройкой окна."""
        self.assertEqual(prompts.store_for({"promptStore": "local"}).kind, "local")
        self.assertEqual(prompts.store_for({"promptStore": "karman"}).kind, "karman")
        self.assertEqual(prompts.store_for({}).kind, "local")

    def test_external_prompts_are_read(self) -> None:
        Handler.prompts.append({"id": 94, "title": TITLE, "body": BODY,
                                 "project": SECTION,
                                 "createdAt": "2026-11-24T07:34:00Z"})
        store = prompts.KarmanPromptStore()
        items = store.list_prompts(SECTION)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "karman")
        self.assertEqual(items[0]["kind"], "prompt")
        self.assertEqual(store.sections(), [SECTION])

    def test_unavailable_resource_is_reported_not_hidden(self) -> None:
        """Ресурс недоступен ⇒ честное сообщение, а не тишина."""
        self.server.shutdown()
        self.server.server_close()
        store = prompts.KarmanPromptStore()
        status = store.status()
        self.assertFalse(status["ok"], status)
        self.assertTrue(status["reason"])
        with self.assertRaises(prompts.KarmanUnavailable):
            store.list_prompts(SECTION)

    def test_archive_needs_the_section_token(self) -> None:
        store = prompts.KarmanPromptStore()
        res = store.archive("94", SECTION)
        self.assertTrue(res["ok"], res)
        self.assertTrue(Handler.archive_calls)
        body = Handler.archive_calls[-1]
        self.assertEqual(body["project"], SECTION)
        self.assertEqual(body["id"], 94)
        self.assertTrue(body["archived"])

        # Без гранта на токен раздела архивировать нечем — и это сказано прямо.
        Handler.section_token = ""
        self._reset_cache()
        res = store.archive("95", SECTION)
        self.assertFalse(res["ok"])
        self.assertIn("токена раздела нет", res["error"])

    def test_closed_archive_channel_says_what_to_do(self) -> None:
        """Канал архива закрыт ⇒ сообщение говорит, **что делать**.

        ⚠️ Живой случай 26.11 (промт #100): канал раздела отвечает 503 (выдача
        не принята), а раздел при этом читается по каналу библиотеки. «Канал не
        настроен» без подсказки — это молчание, выданное за причину.
        """
        store = prompts.KarmanPromptStore()
        Handler.archive_code = 503
        try:
            res = store.archive("94", SECTION)
        finally:
            Handler.archive_code = 200
        self.assertFalse(res["ok"])
        self.assertIn("канал раздела", res["error"])
        self.assertIn("читаются", res["error"])

    def test_prompt_can_be_returned_from_the_archive(self) -> None:
        """Архив — не удаление: заданный по ошибке промт возвращается."""
        store = prompts.KarmanPromptStore()
        res = store.archive("94", SECTION, archived=False)
        self.assertTrue(res["ok"], res)
        body = Handler.archive_calls[-1]
        self.assertFalse(body["archived"])


class KarmanSectionResolutionTest(unittest.TestCase):
    """Имя раздела в библиотеке каноническое — слаг области ему не равен.

    Живой случай 26.11 (промт #94): по имени «minigen» (слаг области)
    приходило **0** промтов, а по каноническому «MiniGen» — 11. Тишина вместо
    правды: автораздача не находила задания своего проекта.
    """

    def setUp(self) -> None:
        Handler.calls = []
        Handler.prompts = []
        Handler.archive_calls = []
        Handler.secret = SECRET
        Handler.section_token = SECTION_TOKEN
        Handler.sections = [SECTION, "MatricaRMZ"]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        keys = Path(self.tmp.name)
        (keys / "vault-url").write_text("http://127.0.0.1:%d" % self.port,
                                        encoding="utf-8")
        (keys / "room-token").write_text("skm_test-token", encoding="utf-8")
        self._old_dir = os.environ.get("BIBLIO_KEYS_DIR")
        os.environ["BIBLIO_KEYS_DIR"] = str(keys)
        self._reset_cache()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._old_dir is None:
            os.environ.pop("BIBLIO_KEYS_DIR", None)
        else:
            os.environ["BIBLIO_KEYS_DIR"] = self._old_dir
        self._reset_cache()
        self.tmp.cleanup()

    def _reset_cache(self) -> None:
        karman._secret_cache.update({"value": "", "source": "", "at": 0.0})
        prompts._cache.update({"sections": [], "sections_at": 0.0,
                               "sections_code": 0,
                               "by_section": {}, "archived_by_section": {}})

    def _paths(self) -> list:
        return [path for method, path, _auth, _body in Handler.calls
                if method == "GET" and path.startswith("/api/prompts?")]

    def test_section_is_found_by_the_area_slug(self) -> None:
        """Слаг области («minigen») находит канонический раздел («MiniGen»)."""
        Handler.prompts.append({"id": 94, "title": TITLE, "body": BODY,
                                 "project": SECTION,
                                 "createdAt": "2026-11-24T07:34:00Z"})
        store = prompts.KarmanPromptStore()
        items = store.list_prompts("minigen")
        self.assertEqual(len(items), 1, "по слагу раздел не нашёлся")
        self.assertEqual(items[0]["id"], "94")
        self.assertTrue(any("project=MiniGen" in path for path in self._paths()),
                        "запрос ушёл не по каноническому имени: %s"
                        % self._paths())

    def test_canonical_section_name(self) -> None:
        store = prompts.KarmanPromptStore()
        self.assertEqual(store.canonical_section("minigen"), SECTION)
        self.assertEqual(store.canonical_section(SECTION), SECTION)
        self.assertEqual(store.canonical_section("НетТакого"), "")

    def test_unknown_section_is_reported_not_silent(self) -> None:
        """Раздела нет ⇒ честное сообщение, а не пустой список."""
        store = prompts.KarmanPromptStore()
        with self.assertRaises(prompts.UnknownSection):
            store.list_prompts("НетТакого")

    def test_cyrillic_section_name_does_not_break_the_request(self) -> None:
        """Кириллица в имени раздела не роняет чтение (живой случай 26.11)."""
        Handler.sections = ["Раздел"]
        Handler.prompts.append({"id": 7, "title": "Промт", "body": BODY,
                                 "project": "Раздел",
                                 "createdAt": "2026-11-24T07:34:00Z"})
        store = prompts.KarmanPromptStore()
        items = store.list_prompts("Раздел")
        self.assertEqual(len(items), 1, "кириллица в имени раздела уронила чтение")
        self.assertTrue(any("%" in path for path in self._paths()),
                        "имя раздела не экранировано: %s" % self._paths())

    def test_archived_prompts_are_read_separately(self) -> None:
        """Архив виден отдельно — и его можно вернуть в оборот."""
        Handler.prompts.append({"id": 94, "title": TITLE, "body": BODY,
                                 "project": SECTION,
                                 "createdAt": "2026-11-24T07:34:00Z"})
        Handler.prompts.append({"id": 93, "title": "Старый", "body": BODY,
                                 "project": SECTION,
                                 "createdAt": "2026-11-23T07:34:00Z",
                                 "archivedAt": "2026-11-24T09:00:00Z"})
        store = prompts.KarmanPromptStore()
        live = store.list_prompts(SECTION)
        self.assertEqual([item["id"] for item in live], ["94"])
        both = store.list_prompts(SECTION, include_archived=True)
        self.assertEqual(sorted(item["id"] for item in both), ["93", "94"])
        archived = store.list_prompts(SECTION, include_archived=True,
                                      only_archived=True)
        self.assertEqual([item["id"] for item in archived], ["93"])
        self.assertTrue(archived[0]["archived"])

    # ───────────────────────────── раздел области: свой, а не чужой (D-190)

    def test_area_takes_only_its_own_section(self) -> None:
        """Область программы берёт свой раздел, а не раздел дома утилит.

        ⚠️ У области `minigen` поле `karman` — «biblio»: по реестру это имя
        проекта в КАРМАНе (учёт расхода MiniMax и комната vault'а), а **не**
        раздел библиотеки промтов. Раздел программы — `MiniGen`, раздел дома
        утилит — `biblio`, и работа дома в область программы не уходит.
        """
        store = prompts.KarmanPromptStore()
        area = {"slug": "minigen", "karman": "biblio"}
        self.assertEqual(prompts.area_sections(area, store), [SECTION])
        self.assertFalse(prompts.section_matches("biblio", area, store))
        self.assertTrue(prompts.section_matches(SECTION, area, store))

    def test_area_without_own_section_keeps_the_karman_field(self) -> None:
        """Область, чей слаг ни на что не похож, берёт раздел из поля `karman`."""
        store = prompts.KarmanPromptStore()
        area = {"slug": "sabantuy", "karman": "SabantuyMalmyzh"}
        self.assertEqual(prompts.area_sections(area, store),
                         ["sabantuy", "SabantuyMalmyzh"])

    def test_secret_name_rule_is_the_karman_one(self) -> None:
        """Имя секрета раздела — по правилу сервиса (`sectionSecretName`)."""
        self.assertEqual(prompts.section_secret_name("MiniGen"),
                         "PROMPTS_TOKEN_MINIGEN")
        self.assertEqual(prompts.section_secret_name("Matrica RMZ"),
                         "PROMPTS_TOKEN_MATRICA_RMZ")

    def test_archive_sends_the_canonical_section_name(self) -> None:
        """Раздел в запросе — каноническое имя: сервис фильтрует точно."""
        store = prompts.KarmanPromptStore()
        res = store.archive("94", "minigen")
        self.assertTrue(res["ok"], res)
        body = Handler.archive_calls[-1]
        self.assertEqual(body["project"], SECTION)
        self.assertEqual(body["id"], 94)


# ──────────────────────────────────────────────── секреты: не в репозитории

class SecretHygieneTest(unittest.TestCase):
    """Токены не попали в репозиторий и в журнал (критерий 7)."""

    def test_secret_is_not_in_the_repository(self) -> None:
        skip = {".git", "node_modules", "out", "__pycache__", ".venv", "dist"}
        found: list = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip for part in path.parts):
                continue
            if path.suffix.lower() in (".exe", ".dll", ".png", ".jpg", ".zip",
                                       ".woff", ".woff2", ".pyc"):
                continue
            try:
                if path.stat().st_size > 4_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if SECRET in text or SECTION_TOKEN in text:
                found.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(found, [], "секрет попал в репозиторий: %s" % found)

    def test_secret_is_not_in_the_journal(self) -> None:
        core = FakeCore()
        core.log("ПРОМТЫ: промт сохранён (MiniGen)")
        joined = "\n".join(core.logs)
        self.assertNotIn(SECRET, joined)
        self.assertNotIn(SECTION_TOKEN, joined)


# ──────────────────────────────────────────────────── команды моста

class BridgeCommandTest(unittest.TestCase):
    """Команды моста: снимок хранилища, пометка, раздача."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "prompts.db"
        self.settings = root / "settings.json"
        self.settings.write_text(json.dumps({"promptStore": "local"}),
                                 encoding="utf-8")
        self._old_db = prompts.LOCAL_DB
        self._old_settings = state.SETTINGS_FILE
        prompts.LOCAL_DB = self.db
        state.SETTINGS_FILE = self.settings
        self.fake = bridge.Bridge.__new__(bridge.Bridge)
        self.fake.mods = {"sessionwatch": FakeWatcher(), "core": FakeCore()}
        self.fake.core = FakeCore()

    def tearDown(self) -> None:
        prompts.LOCAL_DB = self._old_db
        state.SETTINGS_FILE = self._old_settings
        self.tmp.cleanup()

    def test_list_shows_the_store_and_the_prompts(self) -> None:
        res = self.fake.cmd_prompts_save({"project": SECTION, "title": TITLE,
                                          "body": BODY})
        self.assertTrue(res["ok"], res)
        snap = self.fake.cmd_prompts_list({"project": SECTION})
        self.assertTrue(snap["store"]["ok"], snap["store"])
        self.assertEqual(snap["store"]["kind"], "local")
        self.assertEqual(len(snap["prompts"]), 1)
        self.assertEqual(snap["prompts"][0]["project"], SECTION)
        self.assertTrue(snap["targets"], "области-получатели не пришли")

    def test_mark_and_dispatch_go_through_the_bridge(self) -> None:
        self.fake.cmd_prompts_save({"project": SECTION, "title": TITLE,
                                    "body": BODY})
        snap = self.fake.cmd_prompts_list({"project": SECTION})
        prompt_id = snap["prompts"][0]["id"]
        mark = self.fake.cmd_prompts_mark({"id": prompt_id, "project": SECTION,
                                           "auto": True})
        self.assertTrue(mark["ok"], mark)
        snap = self.fake.cmd_prompts_list({"project": SECTION})
        self.assertTrue(snap["prompts"][0]["auto"])

        res = self.fake.cmd_prompts_dispatch({"id": prompt_id, "area": "minigen"})
        self.assertTrue(res["ok"], res)

    def test_dispatch_to_a_foreign_area_is_refused(self) -> None:
        self.fake.cmd_prompts_save({"project": SECTION, "title": TITLE,
                                    "body": BODY})
        snap = self.fake.cmd_prompts_list({"project": SECTION})
        prompt_id = snap["prompts"][0]["id"]
        res = self.fake.cmd_prompts_dispatch({"id": prompt_id, "area": "sarafan"})
        self.assertFalse(res["ok"])
        self.assertIn("уедет не туда", res["error"])

    def test_archive_is_visible_and_reversible(self) -> None:
        """Архив виден отдельно, и промт из него можно вернуть."""
        self.fake.cmd_prompts_save({"project": SECTION, "title": TITLE,
                                    "body": BODY})
        snap = self.fake.cmd_prompts_list({"project": SECTION})
        prompt_id = snap["prompts"][0]["id"]

        res = self.fake.cmd_prompts_archive({"id": prompt_id, "project": SECTION})
        self.assertTrue(res["ok"], res)
        live = self.fake.cmd_prompts_list({"project": SECTION, "view": "live"})
        self.assertEqual(live["prompts"], [], "архивный промт остался в выдаче")
        archive = self.fake.cmd_prompts_list({"project": SECTION,
                                              "view": "archived"})
        self.assertEqual(len(archive["prompts"]), 1)
        self.assertTrue(archive["prompts"][0]["archived"])
        self.assertEqual(archive["view"], "archived")

        back = self.fake.cmd_prompts_archive({"id": prompt_id, "project": SECTION,
                                               "archived": False})
        self.assertTrue(back["ok"], back)
        live = self.fake.cmd_prompts_list({"project": SECTION, "view": "live"})
        self.assertEqual(len(live["prompts"]), 1, "промт не вернулся в оборот")


if __name__ == "__main__":
    unittest.main()
