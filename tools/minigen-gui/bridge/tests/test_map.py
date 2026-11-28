"""Тесты карты родословной (заказ владельца 24.11).

Карта — это паутина копий «МиниГена» и генов между ними: кто родился от кого,
кто какой ген создал, кто чей взял, кто у кого брал. Источник — **пул
наработок** (общий репозиторий): каждая копия дописывает туда свои связи.

Проверяем на подставном пуле:

- карта собирается: копии, гены, связи;
- «у кого взяли» считается по чужим внедрениям;
- отозванный ген виден и связан с заменой;
- без пула карта не падает (пустая, но про свою копию);
- рождение копии пишется в пул (событие `born`).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_map as map_mod  # noqa: E402


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
                     encoding="utf-8")


class MapTest(unittest.TestCase):
    """Карта паутины копий и генов."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pool = self.root / "genepool"
        self.local = self.root / "local"
        self._old_pool = map_mod.POOL_DIR
        self._old_local = map_mod.LOCAL
        self._old_pool_dir = map_mod.pool_dir
        map_mod.POOL_DIR = self.pool
        map_mod.LOCAL = self.local
        # ⚠️ Подменяем именно **резолвер** пула, а не POOL_DIR: в локальном
        # режиме (сети нет) пул — папка `genepool-local`, и карта обязана
        # читать тот же пул, что и модуль генов. Подмена POOL_DIR тут не
        # сработала бы (машина владельца может быть в локальном режиме).
        map_mod.pool_dir = lambda: self.pool
        # 1) пул: две копии, два гена, отзыв и замена
        _write_lines(self.pool / "lineage" / "mg-a.jsonl", [
            {"ts": 1000, "event": "born", "instance": "mg-a", "fork_commit": "aaa1111"},
            {"ts": 2000, "event": "published", "instance": "mg-a", "gene": "gene-mg-a-001"},
            {"ts": 3000, "event": "published", "instance": "mg-a", "gene": "gene-mg-a-002"},
            {"ts": 4000, "event": "retracted", "instance": "mg-a", "gene": "gene-mg-a-001",
             "why": "заменён на gene-mg-a-002", "replaced_by": "gene-mg-a-002"},
        ])
        _write_lines(self.pool / "lineage" / "mg-b.jsonl", [
            {"ts": 5000, "event": "born", "instance": "mg-b", "fork_commit": "bbb2222"},
            {"ts": 6000, "event": "adopted", "instance": "mg-b", "gene": "gene-mg-a-002"},
        ])
        _write_json(self.pool / "genes" / "mg-a" / "gene-mg-a-002" / "manifest.json", {
            "id": "gene-mg-a-002",
            "title": "Раскатка чистит старые бандли",
            "summary": "после раскатки остаются мёртвые бандли",
            "author": "mg-a",
            "base_commit": "ccc3333",
            "created_at": 2000,
        })
        # 2) локальная родословная: мы — это mg-b
        _write_json(self.local / "instance.json", {
            "instance_id": "mg-b",
            "family": "minigen",
            "generation": 2,
            "birth": 5000,
            "fork_of": {"commit": "bbb2222", "installed_at": 4000},
            "parent": {"instance": "mg-a", "gene": "gene-mg-a-002", "commit": "ccc3333"},
            "genes": [{"gene": "gene-mg-a-002", "from": "mg-a", "commit": "ccc3333", "ts": 6000}],
            "copy_repo": "minigen-b",
            "upstream": "Valstan/biblio",
        })

    def tearDown(self) -> None:
        map_mod.POOL_DIR = self._old_pool
        map_mod.LOCAL = self._old_local
        map_mod.pool_dir = self._old_pool_dir
        self.tmp.cleanup()

    # ── сборка карты ──────────────────────────────────────────────────────
    def test_copies_and_genes_are_collected(self) -> None:
        result = map_mod.build()
        instances = [item["instance"] for item in result["copies"]]
        self.assertIn("mg-a", instances)
        self.assertIn("mg-b", instances)
        gene_ids = [item["id"] for item in result["genes"]]
        self.assertIn("gene-mg-a-002", gene_ids)
        self.assertGreaterEqual(result["stats"]["copies"], 2)

    def test_edges_show_made_took_and_born(self) -> None:
        result = map_mod.build()
        kinds = {edge["kind"] for edge in result["edges"]}
        self.assertIn("made", kinds)
        self.assertIn("took", kinds)
        self.assertIn("born", kinds)

    def test_taken_by_counts_the_other_copies(self) -> None:
        result = map_mod.build()
        author = next(item for item in result["copies"] if item["instance"] == "mg-a")
        self.assertIn("mg-b", author["taken_by"])
        taker = next(item for item in result["copies"] if item["instance"] == "mg-b")
        self.assertIn("gene-mg-a-002", taker["genes_taken"])

    def test_retracted_gene_points_to_its_replacement(self) -> None:
        result = map_mod.build()
        first = next(item for item in result["genes"] if item["id"] == "gene-mg-a-001")
        self.assertEqual(first["state"], "retracted")
        self.assertEqual(first["replaced_by"], "gene-mg-a-002")
        self.assertIn("заменён", first["why_retracted"])

    def test_self_is_marked(self) -> None:
        result = map_mod.build()
        myself = [item for item in result["copies"] if item["is_self"]]
        self.assertEqual(len(myself), 1)
        self.assertEqual(myself[0]["instance"], "mg-b")
        self.assertEqual(result["self"]["instance"], "mg-b")

    def test_map_without_pool_does_not_fail(self) -> None:
        map_mod.pool_dir = lambda: self.root / "нет-пула"
        result = map_mod.build()
        self.assertFalse(result["pool_ready"])
        self.assertEqual(result["stats"]["genes"], 0)
        # про свою копию карта всё равно что-то знает
        self.assertEqual(result["self"]["instance"], "mg-b")

    # ── рождение копии пишется в пул ─────────────────────────────────────
    def test_birth_is_written_to_the_pool(self) -> None:
        import minigen_genes as genes
        old_pool = genes.pool_dir
        old_instance = genes.lineage.instance
        genes.pool_dir = lambda: self.pool
        genes.lineage.instance = lambda: {"instance_id": "mg-b"}
        try:
            ok = genes.record_birth("bbb2222", repo="minigen-b")
        finally:
            genes.pool_dir = old_pool
            genes.lineage.instance = old_instance
        self.assertTrue(ok)
        line = (self.pool / "lineage" / "mg-b.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event": "born"', line)
        self.assertIn("bbb2222", line)


if __name__ == "__main__":
    unittest.main()
