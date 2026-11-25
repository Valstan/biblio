"""Локальная база «МиниГена» — приёмка по промту КАРМАНА #92.

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем (по критериям приёмки из промта):

1. **База создаётся сама** — при её отсутствии программа работает.
2. **Данные переживают перезапуск** — файл базы и есть источник правды.
3. **Выгрузка даёт один файл, который читается обратно** — содержимое совпало.
4. **Импорт базы из старой версии не теряет данные** — чужой вид записи лежит
   в «неразобранном», а не пропал.
5. **Спорные записи спрашивают оператора** — конфликт не решается молча.
6. **Смена версии программы не ломает старую базу** — база из более новой
   программы открывается как есть.
7. **Инструменты после импорта работают** — каталог на месте, опасный
   импорт требует подтверждения.

Плюс перенос старой базы хранилища промтов (`prompts.db`) в универсальную
базу: это и есть настоящий «база из старой версии «МиниГена»».

⚠️ Живое состояние не трогаем: путь базы — **временный** (подменяется на время
тестов), как и настройки.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge      # noqa: E402
import minigen_db as db              # noqa: E402
import minigen_prompts as prompts    # noqa: E402
import minigen_state as state        # noqa: E402
import minigen_tools as tools        # noqa: E402


class DbBase(unittest.TestCase):
    """Общее: временная база, настоящий путь программы не трогаем."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "minigen.db"
        self._old_db = state.DB_FILE
        state.DB_FILE = self.db

    def tearDown(self) -> None:
        state.DB_FILE = self._old_db
        self.tmp.cleanup()

    def put(self, kind: str, key: str, payload: dict) -> None:
        con = db.connect(self.db)
        try:
            db.put_record(con, kind, key, payload)
        finally:
            con.close()


# ─────────────────────────────────────────── приёмка 1 и 2: база и данные

class BaseLifeTest(DbBase):
    def test_base_is_created_itself(self) -> None:
        """База создаётся сама — программа работает и при её отсутствии."""
        self.assertFalse(self.db.exists())
        con = db.connect(self.db)
        try:
            self.assertTrue(self.db.exists())
            self.assertEqual(db.schema_version(con), db.SCHEMA_VERSION)
        finally:
            con.close()

    def test_data_survives_restart(self) -> None:
        """Данные переживают перезапуск: файл — источник правды."""
        self.put("prompt", "p1", {"title": "Промт", "body": "текст"})
        # «перезапуск» — открываем базу заново, как это делает программа
        con = db.connect(self.db)
        try:
            rec = db.get_record(con, "prompt", "p1")
        finally:
            con.close()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["payload"]["title"], "Промт")
        self.assertTrue(rec["created_at"])

    def test_soft_delete_keeps_the_record(self) -> None:
        """Мягкое удаление: запись помечена, но не исчезла из базы."""
        con = db.connect(self.db)
        try:
            db.put_record(con, "prompt", "p1", {"title": "Промт"})
            self.assertTrue(db.delete_record(con, "prompt", "p1"))
            self.assertIsNone(db.get_record(con, "prompt", "p1"))
            gone = db.get_record(con, "prompt", "p1", include_deleted=True)
            self.assertIsNotNone(gone)
            self.assertTrue(gone["deleted_at"])
        finally:
            con.close()


# ───────────────────────────────────── приёмка 3: выгрузка и обратное чтение

class ExportImportTest(DbBase):
    def test_export_is_one_file_and_reads_back(self) -> None:
        """Выгрузка — один файл; содержимое совпало после импорта."""
        self.put("prompt", "p1", {"title": "Промт", "body": "текст"})
        self.put("plan", "p2", {"title": "План", "body": "шаги"})
        dump = self.root / "dump.minigen.json"

        res = db.export_db(dump, db_path=self.db)
        self.assertTrue(res["ok"])
        self.assertTrue(dump.is_file())
        self.assertEqual(res["total"], 2)

        fresh = self.root / "fresh.db"
        report = db.apply_import(dump, {}, db_path=fresh)
        self.assertTrue(report["ok"])
        self.assertEqual(report["inserted"], 2)

        con = db.connect(fresh)
        try:
            first = db.get_record(con, "prompt", "p1")
            second = db.get_record(con, "plan", "p2")
        finally:
            con.close()
        self.assertEqual(first["payload"]["body"], "текст")
        self.assertEqual(second["payload"]["title"], "План")

    def test_export_file_is_json_and_has_a_manifest(self) -> None:
        """Файл выгрузки — читаемый JSON с манифестом и суммами."""
        self.put("prompt", "p1", {"title": "Промт"})
        dump = self.root / "dump.minigen.json"
        db.export_db(dump, db_path=self.db)
        body = json.loads(dump.read_text(encoding="utf-8"))
        self.assertEqual(body["format"], db.FORMAT)
        self.assertEqual(body["manifest"]["total"], 1)
        self.assertEqual(body["manifest"]["counts_by_kind"]["prompt"], 1)
        self.assertTrue(body["manifest"]["checksum"])
        self.assertIn("prompt|p1", body["manifest"]["checksums"])

    def test_broken_file_is_reported_not_imported(self) -> None:
        """Файл не из этой программы — честная ошибка, а не тишина."""
        alien = self.root / "alien.json"
        alien.write_text(json.dumps({"format": "что-то другое"}), encoding="utf-8")
        plan = db.plan_import(alien, db_path=self.db)
        self.assertFalse(plan["ok"])
        self.assertIn("не выгрузка", plan["error"])


# ────────────────────────────── приёмка 4: чужая версия не теряет данные

class ForeignVersionTest(DbBase):
    def test_unknown_kind_is_kept_as_unparsed(self) -> None:
        """Чужой вид записи не выбрасывается — лежит в «неразобранном»."""
        self.put("gene-pool", "g1", {"manifest": "чужое"})
        dump = self.root / "dump.minigen.json"
        db.export_db(dump, db_path=self.db)

        fresh = self.root / "fresh.db"
        plan = db.plan_import(dump, db_path=fresh)
        self.assertIn("gene-pool", plan["unknown_kinds"])
        self.assertTrue(plan["questions"])

        report = db.apply_import(dump, {}, db_path=fresh)
        self.assertEqual(report["unparsed"], 1)

        con = db.connect(fresh)
        try:
            stored = db.get_record(con, db.KIND_UNPARSED, "gene-pool|g1")
        finally:
            con.close()
        self.assertIsNotNone(stored)
        # запись лежит целиком, с пометкой вида
        self.assertEqual(stored["payload"]["origin"]["kind"], "gene-pool")
        self.assertEqual(stored["payload"]["record"]["payload"]["manifest"], "чужое")

    def test_unparsed_goes_into_the_next_export(self) -> None:
        """«Неразобранное» уезжает в следующей выгрузке — данные не пропали."""
        self.put("gene-pool", "g1", {"manifest": "чужое"})
        dump = self.root / "dump.minigen.json"
        db.export_db(dump, db_path=self.db)

        fresh = self.root / "fresh.db"
        db.apply_import(dump, {}, db_path=fresh)
        again = self.root / "again.minigen.json"
        db.export_db(again, db_path=fresh)
        body = json.loads(again.read_text(encoding="utf-8"))
        kinds = {item["kind"] for item in body["records"]}
        self.assertIn(db.KIND_UNPARSED, kinds)

    def test_unparsed_is_promoted_when_kind_becomes_known(self) -> None:
        """Когда программа заучила вид — запись ложится по местам."""
        self.put("gene-pool", "g1", {"manifest": "чужое"})
        dump = self.root / "dump.minigen.json"
        db.export_db(dump, db_path=self.db)
        fresh = self.root / "fresh.db"
        db.apply_import(dump, {}, db_path=fresh)

        # вида ещё нет в реестре — лежит в неразобранном
        con = db.connect(fresh)
        try:
            self.assertEqual(db.promote_unparsed(con), 0)
            self.assertIsNotNone(db.get_record(con, db.KIND_UNPARSED, "gene-pool|g1"))
        finally:
            con.close()

        # программа заучила вид — запись переложена
        saved = dict(db.KINDS)
        db.KINDS["gene-pool"] = {"title": "Ген", "hint": "наработка"}
        try:
            con = db.connect(fresh)
            try:
                moved = db.promote_unparsed(con)
            finally:
                con.close()
        finally:
            db.KINDS.clear()
            db.KINDS.update(saved)

        self.assertEqual(moved, 1)
        con = db.connect(fresh)
        try:
            self.assertIsNone(db.get_record(con, db.KIND_UNPARSED, "gene-pool|g1"))
            placed = db.get_record(con, "gene-pool", "g1")
        finally:
            con.close()
        self.assertIsNotNone(placed)
        self.assertEqual(placed["payload"]["manifest"], "чужое")


# ──────────────────────────────── приёмка 5: конфликт решает оператор

class ConflictTest(DbBase):
    def _other_dump(self, payload: dict) -> Path:
        other = self.root / "other.db"
        con = db.connect(other)
        try:
            db.put_record(con, "prompt", "p1", payload)
        finally:
            con.close()
        dump = self.root / "other.minigen.json"
        db.export_db(dump, db_path=other)
        return dump

    def test_conflict_asks_instead_of_overwriting(self) -> None:
        """Конфликт — это вопрос оператору, а не молчаливая перезапись."""
        self.put("prompt", "p1", {"title": "старое"})
        dump = self._other_dump({"title": "новое"})

        plan = db.plan_import(dump, db_path=self.db)
        self.assertEqual(plan["counts"]["conflict"], 1)
        self.assertTrue(plan["questions"])
        self.assertIn("уже есть", plan["questions"][0])

        report = db.apply_import(dump, {}, db_path=self.db)
        self.assertEqual(report["unresolved"], ["prompt|p1"])

        con = db.connect(self.db)
        try:
            rec = db.get_record(con, "prompt", "p1")
        finally:
            con.close()
        # своя запись не тронута
        self.assertEqual(rec["payload"]["title"], "старое")

    def test_operator_decision_is_applied(self) -> None:
        """Решение оператора применяется: «взять из файла»."""
        self.put("prompt", "p1", {"title": "старое"})
        dump = self._other_dump({"title": "новое"})
        report = db.apply_import(dump, {"prompt|p1": "file"}, db_path=self.db)
        self.assertEqual(report["overwritten"], 1)

        con = db.connect(self.db)
        try:
            rec = db.get_record(con, "prompt", "p1")
        finally:
            con.close()
        self.assertEqual(rec["payload"]["title"], "новое")

    def test_operator_can_keep_own(self) -> None:
        """Решение оператора «оставить своё» — запись из файла не кладётся."""
        self.put("prompt", "p1", {"title": "старое"})
        dump = self._other_dump({"title": "новое"})
        report = db.apply_import(dump, {"prompt|p1": "keep"}, db_path=self.db)
        self.assertEqual(report["kept"], 1)

        con = db.connect(self.db)
        try:
            rec = db.get_record(con, "prompt", "p1")
        finally:
            con.close()
        self.assertEqual(rec["payload"]["title"], "старое")

    def test_operator_can_keep_both(self) -> None:
        """Решение «положить рядом» — обе записи живут, своя не потеряна."""
        self.put("prompt", "p1", {"title": "старое"})
        dump = self._other_dump({"title": "новое"})
        report = db.apply_import(dump, {"prompt|p1": "copy"}, db_path=self.db)
        self.assertEqual(report["copied"], 1)

        con = db.connect(self.db)
        try:
            records = db.list_records(con, kind="prompt")
        finally:
            con.close()
        titles = sorted(item["payload"]["title"] for item in records)
        self.assertEqual(titles, ["новое", "старое"])


# ───────────────────────── приёмка 6: смена версии программы не ломает базу

class VersionChangeTest(DbBase):
    def test_future_database_opens_as_is(self) -> None:
        """База из более новой программы открывается и не понижается."""
        con = db.connect(self.db)
        try:
            con.execute("update schema_meta set value=? where key=?",
                        ("99", "schema_version"))
            con.commit()
        finally:
            con.close()

        con = db.connect(self.db)
        try:
            self.assertEqual(db.schema_version(con), 99)
            # запись в чужую базу проходит: контейнер совместим
            db.put_record(con, "prompt", "p1", {"title": "Промт"})
        finally:
            con.close()
        self.assertEqual(db.status(path=self.db)["schema_version"], 99)

    def test_old_database_is_opened_not_recreated(self) -> None:
        """Старая база открывается, а не пересоздаётся: данные на месте."""
        self.put("prompt", "p1", {"title": "Промт"})
        con = db.connect(self.db)
        try:
            db.put_record(con, "prompt", "p2", {"title": "Второй"})
        finally:
            con.close()
        # «новая программа» открывает ту же базу
        con = db.connect(self.db)
        try:
            self.assertIsNotNone(db.get_record(con, "prompt", "p1"))
            self.assertIsNotNone(db.get_record(con, "prompt", "p2"))
        finally:
            con.close()


# ──────────────────────────────── приёмка 7: инструменты работают и вредны

class ToolsTest(DbBase):
    def test_db_tools_are_in_the_catalog(self) -> None:
        """Инструменты базы есть в каталоге нейросети."""
        catalog = tools.by_name()
        for name in ("db.status", "db.records", "db.export", "db.inspect",
                     "db.import"):
            self.assertIn(name, catalog)

    def test_import_tool_is_dangerous(self) -> None:
        """Импорт меняет данные — значит, только через подтверждение."""
        catalog = tools.by_name()
        self.assertTrue(catalog["db.import"]["dangerous"])
        self.assertTrue(catalog["db.export"]["dangerous"])
        self.assertFalse(catalog["db.inspect"]["dangerous"])
        self.assertFalse(catalog["db.status"]["dangerous"])

    def test_bridge_knows_the_db_commands(self) -> None:
        """Команды базы зарегистрированы в реестре моста."""
        fake = bridge.Bridge.__new__(bridge.Bridge)
        handlers = bridge.Bridge._build_handlers(fake)
        for cmd in ("db.status", "db.records", "db.export", "db.inspect",
                    "db.import"):
            self.assertIn(cmd, handlers)

    def test_bridge_export_and_import(self) -> None:
        """Команды моста выгружают и заливают базу."""
        fake = bridge.Bridge.__new__(bridge.Bridge)
        handlers = bridge.Bridge._build_handlers(fake)
        self.put("prompt", "p1", {"title": "Промт"})

        dump = self.root / "dump.minigen.json"
        res = handlers["db.export"]({"path": str(dump)})
        self.assertTrue(res["ok"])
        self.assertTrue(dump.is_file())

        other = self.root / "other.db"
        state.DB_FILE = other
        report = handlers["db.import"]({"path": str(dump), "decisions": {}})
        self.assertTrue(report["ok"])
        self.assertEqual(report["inserted"], 1)
        self.assertNotIn("_body", report)


# ────────────────────────── перенос старой базы хранилища промтов

class LegacyStoreTest(DbBase):
    """Старая база (`prompts.db`) — настоящая «база из старой версии»."""

    def _legacy_db(self) -> Path:
        path = self.root / "prompts.db"
        con = sqlite3.connect(str(path))
        try:
            prompts.legacy_ensure_schema(con)
            con.execute(
                "insert into records (id, project, title, body, kind, archived,"
                " created_at) values (?,?,?,?,?,?,?)",
                ("a1", "MiniGen", "Промт", "текст", "prompt", 0, 1700000000000))
            con.execute(
                "insert into records (id, project, title, body, kind, archived,"
                " created_at) values (?,?,?,?,?,?,?)",
                ("a2", "MiniGen", "План: шаги", "шаги", "plan", 0, 1700000000000))
            con.execute(
                "insert into marks (source, project, id, auto, at)"
                " values (?,?,?,?,?)",
                ("local", "MiniGen", "a1", 1, 1700000000000))
            con.commit()
        finally:
            con.close()
        return path

    def test_legacy_store_is_migrated(self) -> None:
        """Данные из старой базы переезжают в универсальную — один раз."""
        legacy = self._legacy_db()
        result = prompts.migrate_legacy_store(legacy, self.db)
        self.assertTrue(result["ok"])
        # два промта (промт и план) и одна отметка владельца
        self.assertEqual(result["migrated"], 3)

        con = db.connect(self.db)
        try:
            self.assertIsNotNone(db.get_record(con, "prompt", "a1"))
            self.assertIsNotNone(db.get_record(con, "plan", "a2"))
            mark = db.get_record(con, "prompt-mark", "local|MiniGen|a1")
        finally:
            con.close()
        self.assertTrue(mark["payload"]["auto"])

        # второй раз перенос не повторяется (идемпотентно)
        again = prompts.migrate_legacy_store(legacy, self.db)
        self.assertEqual(again["migrated"], 0)

    def test_legacy_file_is_untouched(self) -> None:
        """Старый файл остаётся на месте — это запасная копия."""
        legacy = self._legacy_db()
        before = legacy.read_bytes()
        prompts.migrate_legacy_store(legacy, self.db)
        self.assertTrue(legacy.is_file())
        self.assertEqual(legacy.read_bytes(), before)

    def test_prompt_store_reads_the_universal_base(self) -> None:
        """Хранилище промтов читает универсальную базу."""
        store = prompts.LocalPromptStore(self.db)
        res = store.save("MiniGen", "Промт", "текст")
        self.assertTrue(res["ok"])
        items = store.list_prompts("MiniGen")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Промт")

        con = db.connect(self.db)
        try:
            self.assertIsNotNone(db.get_record(con, "prompt", res["id"]))
        finally:
            con.close()

    def test_prompts_survive_an_export_and_import(self) -> None:
        """Промты уезжают в файл выгрузки и возвращаются в другой базе."""
        store = prompts.LocalPromptStore(self.db)
        store.save("MiniGen", "Промт", "текст")
        dump = self.root / "dump.minigen.json"
        db.export_db(dump, db_path=self.db)

        other = self.root / "other.db"
        report = db.apply_import(dump, {}, db_path=other)
        self.assertEqual(report["inserted"], 1)
        moved = prompts.LocalPromptStore(other)
        items = moved.list_prompts("MiniGen")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["body"], "текст")


if __name__ == "__main__":
    unittest.main()
