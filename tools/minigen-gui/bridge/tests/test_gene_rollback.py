"""Откат гена, пометка «не ставить» и рейтинг (промт КАРМАНА #187).

Заказ владельца (28.11): «установил → не понравилось → вернуть всё как было»,
после отката ген помечается «не ставить», а рейтинг (звёздочки 1–10) виден
там, где решают, ставить ген или нет.

Проверяем на **настоящем** git-репозитории (никаких заглушек на состояние):

- **откат возвращает состояние «как было»**: тот же список изменённых файлов
  и тот же diff, что до внедрения гена;
- **откат записан в родословной**: видно, какой ген откатан и когда;
- **откатанный ген не предлагается**: его нет в чужих наработках, а в
  отдельном списке он виден с пометкой «уже ставили и откатывали»;
- **рейтинг считается по всем копиям**: число голосов и средняя оценка;
  голоса одной копии не конфликтуют при одновременной записи в пул;
- **внешний вид не меняется молча**: ген, трогающий оформление, помечен
  заранее, а без явного решения владельца он не внедряется.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_code as code     # noqa: E402
import minigen_genes as genes   # noqa: E402
import minigen_lineage as lineage  # noqa: E402


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def _diff_names(repo: Path, left: str, right: str) -> list[str]:
    """Список изменённых файлов между двумя состояниями ветки."""
    res = _git(["diff", "--name-only", left, right], repo)
    return [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]


def _diff_text(repo: Path, left: str, right: str) -> str:
    res = _git(["diff", left, right], repo)
    return res.stdout or ""


class GeneRollbackTest(unittest.TestCase):
    """Откат, пометки и рейтинг — на настоящем репозитории."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _git(["init", "-q"], self.repo)
        # ветка master — её ждёт `create_worktree`
        _git(["symbolic-ref", "HEAD", "refs/heads/master"], self.repo)
        _git(["config", "user.email", "test@local"], self.repo)
        _git(["config", "user.name", "test"], self.repo)
        (self.repo / "app.py").write_text("print('было')\n", encoding="utf-8")
        (self.repo / "ui").mkdir()
        (self.repo / "ui" / "styles.css").write_text("body { color: #111; }\n",
                                                     encoding="utf-8")
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "первый"], self.repo)

        self.pool = self.root / "pool"
        (self.pool / "genes").mkdir(parents=True)

        # подменяем пути модулей на временные (машина владельца не трогается)
        self._old = (code.REPO, code.WORKTREES, code.STATE_FILE,
                     genes.pool_dir, genes._local_mode, code.run_tests,
                     lineage.INSTANCE_FILE, lineage.LEDGER_FILE)
        code.REPO = self.repo
        code.WORKTREES = self.root / "worktrees"
        code.STATE_FILE = self.root / "state.json"
        genes.pool_dir = lambda: self.pool
        genes._local_mode = lambda: True
        code.run_tests = lambda target="all", worktree=None: {"ok": True, "steps": []}
        lineage.INSTANCE_FILE = self.root / "instance.json"
        lineage.LEDGER_FILE = self.root / "lineage.jsonl"
        lineage._write_json(lineage.INSTANCE_FILE, {
            "instance_id": "mg-abcdef12", "family": "minigen", "generation": 1,
            "birth": 1, "fork_of": {"commit": "abc1234", "installed_at": 1},
            "parent": {}, "genes": [],
        })

    def tearDown(self) -> None:
        (code.REPO, code.WORKTREES, code.STATE_FILE,
         genes.pool_dir, genes._local_mode, code.run_tests,
         lineage.INSTANCE_FILE, lineage.LEDGER_FILE) = self._old
        self.tmp.cleanup()

    # ── помощники ──────────────────────────────────────────────────────────

    def _make_gene(self, gene: str = "gene-mg-99999999-001",
                   author: str = "mg-99999999", name: str = "app.py",
                   touch: str | None = None) -> str:
        """Ген в пуле: патч с настоящим diff'ом по файлу `name`."""
        if touch:
            path = self.repo / touch
            path.write_text(path.read_text(encoding="utf-8") + "/* правка гена */\n",
                            encoding="utf-8")
        else:
            path = self.repo / name
            path.write_text("print('стало')\n", encoding="utf-8")
        patch = genes._git_bytes(["diff", "HEAD"], self.repo)
        _git(["checkout", "--", "."], self.repo)
        directory = self.pool / "genes" / author / gene
        directory.mkdir(parents=True, exist_ok=True)
        files = [touch or name]
        appearance = genes.appearance_files(files)
        (directory / "manifest.json").write_text(json.dumps({
            "id": gene, "title": "Наработка", "summary": "суть", "author": author,
            "family": "minigen", "created_at": 100, "base_commit": "abc1234",
            "files": files, "version": "abc1234",
            "appearance": {"touches": bool(appearance), "files": appearance},
        }, ensure_ascii=False), encoding="utf-8")
        (directory / "patch.diff").write_bytes(patch)
        (directory / "notes.md").write_text("зачем", encoding="utf-8")
        return gene

    # ── откат ──────────────────────────────────────────────────────────────

    def test_rollback_restores_state_before_the_gene(self) -> None:
        """Откат возвращает состояние программы к состоянию до внедрения."""
        gene = self._make_gene()
        before_files = _diff_names(self.repo, "master", "master")
        before_diff = _diff_text(self.repo, "master", "master")

        adopted = genes.adopt(gene)
        self.assertTrue(adopted.get("ok"), adopted.get("error"))
        branch = adopted["branch"]
        self.assertTrue(adopted.get("commit"), "внедрение должно помнить свой коммит")
        # ген стоит: состояние отличается от состояния до внедрения
        self.assertNotEqual(_diff_names(self.repo, "master", branch), before_files)

        rolled = genes.rollback(gene)
        self.assertTrue(rolled.get("ok"), rolled.get("error"))
        # ⚠️ критерий приёмки: тот же список изменённых файлов и тот же diff
        self.assertEqual(_diff_names(self.repo, "master", branch), before_files)
        self.assertEqual(_diff_text(self.repo, "master", branch), before_diff)
        # и содержимое файла — то же, что было до гена
        content = _git(["show", "%s:app.py" % branch], self.repo).stdout
        self.assertEqual(content, "print('было')\n")

    def test_rollback_is_written_into_the_lineage(self) -> None:
        """Откат виден в родословной: какой ген откатан и когда."""
        gene = self._make_gene()
        adopted = genes.adopt(gene)
        self.assertTrue(adopted.get("ok"), adopted.get("error"))

        # свой коммит, куда ген встал, помнится
        entry = lineage.gene_record(gene)
        self.assertEqual(entry.get("local_commit"), adopted.get("commit"))
        self.assertTrue(entry.get("branch"))

        rolled = genes.rollback(gene)
        self.assertTrue(rolled.get("ok"), rolled.get("error"))

        events = [item.get("event") for item in lineage.ledger(limit=50)]
        self.assertIn("rollback", events)
        marked = {item.get("gene") for item in lineage.rolled_back_genes()}
        self.assertIn(gene, marked)
        stamp = lineage.rolled_back_genes()[0].get("ts")
        self.assertTrue(stamp, "в родословной должно быть видно, когда откатили")
        # и в пуле — соседям полезно знать, что ген уже откатывали
        pool_events = genes.pool_events()
        self.assertTrue(any(item.get("event") == "rolled_back" and
                            item.get("gene") == gene for item in pool_events))

    def test_rolled_back_gene_is_not_offered_anymore(self) -> None:
        """Откатанный ген не попадает в чужие наработки, но виден отдельно."""
        gene = self._make_gene()
        self.assertTrue(genes.adopt(gene).get("ok"))
        self.assertTrue(genes.rollback(gene).get("ok"))

        data = genes.suggestions()
        offered = {item["id"] for item in data["suggestions"]}
        self.assertNotIn(gene, offered)
        refused = {item["id"]: item for item in data["not_wanted"]}
        self.assertIn(gene, refused)
        self.assertIn("откатывали", refused[gene]["why"])

    def test_dismiss_hides_gene_from_suggestions(self) -> None:
        """Пометка «не ставить» убирает ген из предложения."""
        gene = self._make_gene()
        result = genes.dismiss(gene, True, "не нужен")
        self.assertTrue(result.get("ok"))
        data = genes.suggestions()
        self.assertNotIn(gene, {item["id"] for item in data["suggestions"]})
        self.assertIn(gene, {item["id"] for item in data["not_wanted"]})

    def test_adopted_gene_is_not_a_refused_one(self) -> None:
        """Внедрённый, но не откатанный ген — не «не ставим», а «внедрён»."""
        gene = self._make_gene()
        self.assertTrue(genes.adopt(gene).get("ok"))
        data = genes.suggestions()
        self.assertNotIn(gene, {item["id"] for item in data["suggestions"]})
        self.assertNotIn(gene, {item["id"] for item in data["not_wanted"]})
        self.assertIn(gene, {item["gene"] for item in data["installed"]})

    # ── рейтинг ────────────────────────────────────────────────────────────

    def test_rating_shows_votes_and_average(self) -> None:
        """У гена видны число голосов и средняя оценка по всем копиям."""
        gene = self._make_gene()
        result = genes.rate(gene, 8)
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertEqual(result.get("votes"), 1)
        self.assertEqual(result.get("average"), 8.0)

        # вторая копия голосует — свой файл в пуле, общий журнал не нужен
        other = self.pool / "lineage" / "mg-77777777.jsonl"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(json.dumps({"ts": 1, "event": "rated",
                                     "instance": "mg-77777777",
                                     "gene": gene, "score": 10},
                                    ensure_ascii=False) + "\n", encoding="utf-8")
        votes = genes.ratings()
        self.assertEqual(votes[gene]["votes"], 2)
        self.assertEqual(votes[gene]["average"], 9.0)

    def test_one_vote_per_copy(self) -> None:
        """Повторная оценка той же копии заменяет прежнюю, а не добавляет."""
        gene = self._make_gene()
        genes.rate(gene, 8)
        genes.rate(gene, 4)
        votes = genes.ratings()
        self.assertEqual(votes[gene]["votes"], 1)
        self.assertEqual(votes[gene]["average"], 4.0)

    def test_votes_of_two_copies_do_not_conflict(self) -> None:
        """Голоса копий не конфликтуют: свой файл на копию."""
        gene = self._make_gene()
        genes.rate(gene, 7)
        other = self.pool / "lineage" / "mg-77777777.jsonl"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(json.dumps({"ts": 2, "event": "rated",
                                     "instance": "mg-77777777",
                                     "gene": gene, "score": 5},
                                    ensure_ascii=False) + "\n", encoding="utf-8")
        # обе записи на месте — ни одна не потерялась
        votes = genes.ratings()
        self.assertEqual(votes[gene]["votes"], 2)
        self.assertEqual(votes[gene]["average"], 6.0)

    def test_rating_is_visible_in_suggestions(self) -> None:
        """Рейтинг виден там, где решают, ставить ген или нет."""
        gene = self._make_gene()
        genes.rate(gene, 9)
        data = genes.suggestions()
        item = [row for row in data["suggestions"] if row["id"] == gene]
        self.assertTrue(item, "ген должен быть в списке чужих наработок")
        self.assertEqual(item[0]["votes"], 1)
        self.assertEqual(item[0]["average"], 9.0)

    def test_rating_range(self) -> None:
        """Оценка — число от 1 до 10, иначе мост отвечает отказом."""
        gene = self._make_gene()
        self.assertIn("error", genes.rate(gene, 0))
        self.assertIn("error", genes.rate(gene, 11))
        self.assertTrue(genes.rate(gene, 10).get("ok"))

    # ── внешний вид ────────────────────────────────────────────────────────

    def test_appearance_files_are_detected(self) -> None:
        """Граница «внешнего вида» — проверяемая."""
        self.assertTrue(genes.appearance_files(
            ["tools/minigen-gui/src/renderer/src/styles.css"]))
        self.assertTrue(genes.appearance_files(
            ["tools/minigen-gui/src/renderer/src/icons/logo.svg"]))
        self.assertTrue(genes.appearance_files(
            ["tools/minigen-gui/src/renderer/src/theme/dark.ts"]))
        self.assertFalse(genes.appearance_files(
            ["tools/minigen-gui/bridge/minigen_genes.py"]))
        self.assertFalse(genes.appearance_files(
            ["tools/minigen-gui/bridge/tests/test_genes.py"]))

    def test_gene_touching_appearance_is_marked_and_not_adopted_silently(self) -> None:
        """Ген, меняющий вид, помечен заранее и без решения не внедряется."""
        gene = self._make_gene(gene="gene-mg-99999999-002",
                               touch="ui/styles.css")
        listed = genes.list_genes()
        marked = [item for item in listed if item["id"] == gene]
        self.assertTrue(marked)
        self.assertTrue(marked[0]["touches_appearance"],
                        "ген, трогающий оформление, помечен заранее")

        refused = genes.adopt(gene)
        self.assertFalse(refused.get("ok"))
        self.assertTrue(refused.get("needs_appearance_decision"))
        self.assertIn("внешний вид", refused.get("error") or "")

        # с явным решением владельца — внедряется
        allowed = genes.adopt(gene, allow_appearance=True)
        self.assertTrue(allowed.get("ok"), allowed.get("error"))

    def test_gene_without_appearance_change_installs_as_is(self) -> None:
        """Обычный ген внедряется без лишних вопросов, и вид не меняется."""
        gene = self._make_gene()
        result = genes.adopt(gene)
        self.assertTrue(result.get("ok"), result.get("error"))
        branch = result["branch"]
        changed = _diff_names(self.repo, "master", branch)
        self.assertEqual(changed, ["app.py"])
        self.assertFalse(genes.appearance_files(changed),
                         "обычный ген не должен трогать оформление")


if __name__ == "__main__":
    unittest.main()
