"""Тесты родословной копий «МиниГена» (24.11, заказ владельца).

Заказ: «в версии минигена можно заложить кодированную информацию, от какой
версии минигена он получил форк… отдельные семейства отслеживать, чтобы по
версии минигена можно было отследить, по какой ветке развития он пошел, с кем
перемешался, какие гены от кого в нем присутствуют».

Проверяем:

- **экземпляр** непрозрачный и устойчивый: id не выводится из машины, одно
  и то же при повторных обращениях;
- **летопись append-only**: записи не переписываются, новые — первыми;
- **принятие гена** повышает поколение и запоминает родителя;
- **в версию программы** кладётся только родословная — без путей и имён
  машин (D-038).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_lineage as lineage  # noqa: E402


class LineageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._old_instance = lineage.INSTANCE_FILE
        self._old_ledger = lineage.LEDGER_FILE
        self._old_version = lineage.program_version
        lineage.INSTANCE_FILE = Path(self.tmp.name) / "instance.json"
        lineage.LEDGER_FILE = Path(self.tmp.name) / "lineage.jsonl"
        lineage.program_version = lambda: {"commit": "abc1234", "installed_at": 1700000000}

    def tearDown(self) -> None:
        lineage.INSTANCE_FILE = self._old_instance
        lineage.LEDGER_FILE = self._old_ledger
        lineage.program_version = self._old_version
        self.tmp.cleanup()

    def test_instance_is_created_once_and_opaque(self) -> None:
        first = lineage.instance()
        second = lineage.instance()
        self.assertEqual(first["instance_id"], second["instance_id"])
        self.assertRegex(first["instance_id"], r"^mg-[0-9a-f]{8}$")
        self.assertEqual(first["family"], "minigen")
        self.assertEqual(first["generation"], 1)
        self.assertEqual(first["fork_of"]["commit"], "abc1234")

    def test_instance_id_does_not_contain_machine_data(self) -> None:
        data = lineage.instance()
        blob = json.dumps(data, ensure_ascii=False).lower()
        for leak in (str(Path.home()).lower(), "users", "\\", "/"):
            self.assertNotIn(leak, blob.lower(),
                             "в данных копии не должно быть данных машины: %s" % leak)

    def test_ledger_is_append_only_and_newest_first(self) -> None:
        lineage.record("первое", note="a")
        lineage.record("второе", note="b")
        entries = lineage.ledger(limit=10)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["event"], "второе")
        self.assertEqual(entries[1]["event"], "первое")
        # файл — построчный, его не переписывают
        lines = lineage.LEDGER_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_adopt_raises_generation_and_remembers_parent(self) -> None:
        lineage.adopt("gene-42", from_instance="mg-abcdef12",
                      from_commit="deadbeef", note="починка сети")
        data = lineage.instance()
        self.assertEqual(data["generation"], 2)
        self.assertEqual(data["parent"]["instance"], "mg-abcdef12")
        self.assertEqual(data["parent"]["gene"], "gene-42")
        self.assertEqual(len(data["genes"]), 1)
        events = [e["event"] for e in lineage.ledger(limit=10)]
        self.assertIn("adopt", events)

    def test_stamp_adds_lineage_without_paths(self) -> None:
        version = lineage.stamp({"commit": "abc1234", "source": "D:/some/repo"})
        block = version["lineage"]
        self.assertEqual(block["family"], "minigen")
        self.assertEqual(block["generation"], 1)
        self.assertIn("instance_id", block)
        self.assertIn("fork_of", block)
        blob = json.dumps(block, ensure_ascii=False)
        self.assertNotIn("D:/some/repo", blob)
        self.assertNotIn("Users", blob)
        self.assertIsNone(re.search(r"[A-Za-z]:\\\\", blob))

    def test_genealogy_has_instance_and_history(self) -> None:
        lineage.record("событие", note="x")
        tree = lineage.genealogy()
        self.assertIn("instance", tree)
        self.assertIn("history", tree)
        self.assertTrue(tree["history"])


if __name__ == "__main__":
    unittest.main()
