"""Тесты режимов копии и первого запуска (25.11, промт #131).

Заказ владельца: «при первом запуске копия спрашивает разрешение на
регистрацию своей папки и предлагает три режима работы (свой GitHub / общий
репозиторий / только локально)», а в локальном режиме всё честно: правки идут
в локальный git-репозиторий, PR не открывается, обмен с копиями
недоступен.

Проверяем:

- вопрос первого запуска: репозиторий, папка, что даёт, что при отказе;
- ответ сохраняется **один раз** (в `instance.json` и в настройках);
- отказ не ломает программу: копия работает в локальном режиме;
- три режима переключаются, и смена видна в `clone.status`;
- локальный режим: локальный git-репозиторий поднимается, `code.pr`
  честно говорит, что PR без GitHub не открывается, пул не обновляется;
- подсказки наработок: чужой ген предлагается с **причиной** совпадения, а
  свой и уже внедрённый — нет;
- ручной обмен: выгрузка папки и внос её обратно.

⚠️ Тесты не трогают живые файлы программы: настройки, родословная, клон и
пул подменяются на временные (см. `_CopyTestCase`).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_clone as clone_mod    # noqa: E402
import minigen_code as code          # noqa: E402
import minigen_copy as copy_mod      # noqa: E402
import minigen_genes as genes_mod    # noqa: E402
import minigen_lineage as lineage    # noqa: E402
import minigen_state as state        # noqa: E402


class _CopyTestCase(unittest.TestCase):
    """Общая подмена живых путей на временные."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings_file = root / "settings.json"
        self.instance_file = root / "instance.json"
        self.ledger_file = root / "lineage.jsonl"
        self.clone_dir = root / "clone"
        self.pool_dir = root / "pool"
        self.local_repo = root / "local-repo"
        self.export_dir = root / "export"

        self._saved = {
            "settings": state.SETTINGS_FILE,
            "instance": lineage.INSTANCE_FILE,
            "ledger": lineage.LEDGER_FILE,
            "clone": clone_mod.CLONE_DIR,
            "pool": clone_mod.POOL_DIR,
            "local": copy_mod.LOCAL_REPO,
            "export": copy_mod.EXPORT_DIR,
            "copy_local": copy_mod.LOCAL,
            "code_repo": code.REPO,
            "code_worktrees": code.WORKTREES,
            "code_state": code.STATE_FILE,
            "pool_dir_fn": genes_mod.pool_dir,
        }
        state.SETTINGS_FILE = self.settings_file
        lineage.INSTANCE_FILE = self.instance_file
        lineage.LEDGER_FILE = self.ledger_file
        clone_mod.CLONE_DIR = self.clone_dir
        clone_mod.POOL_DIR = self.pool_dir
        copy_mod.LOCAL_REPO = self.local_repo
        copy_mod.EXPORT_DIR = self.export_dir
        copy_mod.LOCAL = root
        code.STATE_FILE = root / "fix-worktree.json"
        genes_mod.pool_dir = lambda: self.pool_dir

    def tearDown(self) -> None:
        state.SETTINGS_FILE = self._saved["settings"]
        lineage.INSTANCE_FILE = self._saved["instance"]
        lineage.LEDGER_FILE = self._saved["ledger"]
        clone_mod.CLONE_DIR = self._saved["clone"]
        clone_mod.POOL_DIR = self._saved["pool"]
        copy_mod.LOCAL_REPO = self._saved["local"]
        copy_mod.EXPORT_DIR = self._saved["export"]
        copy_mod.LOCAL = self._saved["copy_local"]
        code.REPO = self._saved["code_repo"]
        code.WORKTREES = self._saved["code_worktrees"]
        code.STATE_FILE = self._saved["code_state"]
        genes_mod.pool_dir = self._saved["pool_dir_fn"]
        self.tmp.cleanup()

    def make_sources(self) -> Path:
        """Папка с исходниками программы (как на машине после установки)."""
        root = Path(self.tmp.name) / "sources"
        (root / "tools" / "minigen-gui" / "bridge").mkdir(parents=True, exist_ok=True)
        (root / "tools" / "minigen-gui" / "bridge" / "mod.py").write_text(
            "x = 1\n", encoding="utf-8")
        (root / "README.md").write_text("программа\n", encoding="utf-8")
        return root


class ModeTest(_CopyTestCase):
    def test_three_modes_are_offered(self) -> None:
        options = copy_mod.mode_options()
        self.assertEqual([item["id"] for item in options], ["own", "shared", "local"])
        for item in options:
            self.assertTrue(item["label"])
            self.assertTrue(item["hint"])

    def test_mode_falls_back_to_the_state_of_the_machine(self) -> None:
        # ничего не выбрано, клона нет ⇒ локальный режим (сети нет — не выдумываем)
        self.assertEqual(copy_mod.stored_mode(), "")
        self.assertEqual(copy_mod.mode(), "local")

    def test_chosen_mode_wins(self) -> None:
        state.write_settings({"copyMode": "shared"})
        self.assertEqual(copy_mod.mode(), "shared")
        self.assertEqual(copy_mod.mode_label(), "Папка в общем репозитории")

    def test_local_mode_is_visible_in_clone_status(self) -> None:
        state.write_settings({"copyMode": "local"})
        info = clone_mod.status()
        self.assertEqual(info["mode"], "local")
        self.assertEqual(info["mode_label"], "Только локально, без сети")
        self.assertFalse(info["exchange"]["available"])
        self.assertIn("локальный режим", info["exchange"]["why"])


class FirstRunTest(_CopyTestCase):
    def test_offer_names_repository_folder_and_consequences(self) -> None:
        offer = copy_mod.offer()
        self.assertTrue(offer["repository"])
        self.assertIn("copies/", offer["folder"])
        self.assertTrue(offer["gives"])
        self.assertTrue(offer["if_refuse"])
        # отказ описан прямо: программа работает, PR не открываются
        joined = " ".join(offer["if_refuse"])
        self.assertIn("работает", joined)
        self.assertIn("PR", joined)

    def test_refusal_keeps_the_program_working(self) -> None:
        report = copy_mod.answer_first_run(agree=False)
        self.assertTrue(report.get("ok"), report)
        self.assertEqual(copy_mod.mode(), "local")
        # ответ сохранён в родословной копии и в настройках
        saved = json.loads(self.instance_file.read_text(encoding="utf-8"))
        self.assertTrue(saved["first_run"]["asked_at"])
        self.assertFalse(saved["first_run"]["agreed"])
        self.assertEqual(state.read_settings()["copyMode"], "local")
        # программа жива: состояние отдаётся
        self.assertIn("mode", clone_mod.status())

    def test_consent_defaults_to_shared_repository(self) -> None:
        clone_mod.setup = lambda **kwargs: {"ok": True, "steps": [],
                                            "why": "подмена в тесте"}
        try:
            report = copy_mod.answer_first_run(agree=True)
        finally:
            clone_mod.setup = _real_setup
        self.assertTrue(report.get("ok"), report)
        self.assertEqual(copy_mod.mode(), "shared")

    def test_question_is_asked_only_once(self) -> None:
        self.assertTrue(copy_mod.answer_first_run(agree=False).get("ok"))
        again = copy_mod.answer_first_run(agree=True)
        self.assertFalse(again.get("ok"))
        self.assertIn("уже задан", again.get("error", ""))


_real_setup = clone_mod.setup


class LocalRepoTest(_CopyTestCase):
    def test_local_repo_is_created_with_history(self) -> None:
        sources = self.make_sources()
        copy_mod.source_candidates = lambda: [sources]
        try:
            report = copy_mod.ensure_local_repo()
        finally:
            copy_mod.source_candidates = _real_sources
        self.assertTrue(report.get("ok"), report)
        self.assertTrue((self.local_repo / ".git").is_dir())
        self.assertTrue((self.local_repo / "tools" / "minigen-gui" / "bridge"
                         / "mod.py").is_file())
        head = copy_mod.git(["rev-parse", "--abbrev-ref", "HEAD"], self.local_repo)
        self.assertEqual(head.get("out"), "master")
        log = copy_mod.git(["log", "--oneline"], self.local_repo)
        self.assertTrue(log.get("out"))

    def test_second_call_does_not_touch_the_repo(self) -> None:
        sources = self.make_sources()
        copy_mod.source_candidates = lambda: [sources]
        try:
            copy_mod.ensure_local_repo()
            again = copy_mod.ensure_local_repo()
        finally:
            copy_mod.source_candidates = _real_sources
        self.assertTrue(again.get("ok"))
        self.assertIn("уже есть", again.get("why", ""))

    def test_without_sources_the_copy_says_so(self) -> None:
        copy_mod.source_candidates = lambda: []
        try:
            report = copy_mod.ensure_local_repo()
        finally:
            copy_mod.source_candidates = _real_sources
        self.assertFalse(report.get("ok"))
        self.assertIn("исходников программы на этой машине не найдено",
                      report.get("why", ""))


_real_sources = copy_mod.source_candidates


class LocalModeBoundariesTest(_CopyTestCase):
    def test_sync_says_that_there_is_no_network(self) -> None:
        state.write_settings({"copyMode": "local"})
        result = clone_mod.sync()
        self.assertFalse(result.get("ok"))
        self.assertIn("локальный режим", result.get("why", ""))

    def test_pr_is_not_opened_without_github(self) -> None:
        state.write_settings({"copyMode": "local"})
        worktree = Path(self.tmp.name) / "wt"
        worktree.mkdir(parents=True, exist_ok=True)
        code.current_worktree = lambda: worktree
        code.current_branch = lambda cwd: "agent/minigen-fix-test"
        try:
            result = code.open_pr("правка")
        finally:
            code.current_worktree = _real_worktree
            code.current_branch = _real_branch
        self.assertIn("PR не открывается", result.get("error", ""))
        self.assertTrue(result.get("local"))

    def test_publish_in_local_mode_stays_on_the_machine(self) -> None:
        state.write_settings({"copyMode": "local"})
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        old_diff = genes_mod._current_diff
        genes_mod._current_diff = lambda: (b"--- a/x.py\n+++ b/x.py\n+line\n", ["x.py"])
        try:
            result = genes_mod.publish("Наработка", summary="суть")
        finally:
            genes_mod._current_diff = old_diff
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("local_only"))
        self.assertIn("только на этой машине", result.get("why", ""))
        # ген лёг в локальный пул — папка создаётся сама
        self.assertTrue((self.pool_dir / "genes").is_dir())


_real_worktree = code.current_worktree
_real_branch = code.current_branch


class _FakeSubprocess:
    """Заглушка `subprocess` для `code.open_pr` (настоящий `gh` не зовём)."""

    CREATE_NO_WINDOW = 0

    class TimeoutExpired(Exception):
        pass

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args, **kwargs):
        self.calls.append(list(args))

        class Proc:
            returncode = 0
            stdout = "https://github.com/Valstan/biblio/pull/1"
            stderr = ""

        return Proc()


class BranchTargetTest(_CopyTestCase):
    """Куда уходит ветка — это и есть разница между режимами копии."""

    def _open_pr(self, mode: str) -> tuple[list[list[str]], dict]:
        state.write_settings({"copyMode": mode})
        worktree = Path(self.tmp.name) / "wt"
        worktree.mkdir(parents=True, exist_ok=True)
        calls: list[list[str]] = []
        urls = {"origin": "https://github.com/Valstan/biblio.git",
                "upstream": "https://github.com/Valstan/biblio.git"}

        def fake_git(args, cwd, timeout=120):
            calls.append(list(args))
            if args[:2] == ["remote", "get-url"]:
                return {"ok": True, "out": urls.get(args[2], ""), "err": "", "code": 0}
            return {"ok": True, "out": "", "err": "", "code": 0}

        old_git = code.git
        old_worktree = code.current_worktree
        old_branch = code.current_branch
        old_subprocess = code.subprocess
        code.git = fake_git
        code.current_worktree = lambda: worktree
        code.current_branch = lambda cwd: "agent/minigen-fix-test"
        code.subprocess = _FakeSubprocess()
        try:
            result = code.open_pr("правка")
        finally:
            code.git = old_git
            code.current_worktree = old_worktree
            code.current_branch = old_branch
            code.subprocess = old_subprocess
        return calls, result

    def test_shared_mode_sends_the_branch_to_the_program_repository(self) -> None:
        calls, result = self._open_pr("shared")
        self.assertTrue(result.get("ok"), result)
        pushes = [call for call in calls if call and call[0] == "push"]
        self.assertTrue(pushes)
        # `git push -u <remote> <ветка>`
        self.assertIn(pushes[0][2], ("upstream", "origin"))

    def test_own_mode_pushes_to_its_own_repository(self) -> None:
        calls, result = self._open_pr("own")
        self.assertTrue(result.get("ok"), result)
        pushes = [call for call in calls if call and call[0] == "push"]
        self.assertTrue(pushes)
        self.assertEqual(pushes[0][2], "origin")


class SuggestionTest(_CopyTestCase):
    def _gene(self, gene_id: str, author: str, files: list[str]) -> dict:
        directory = self.pool_dir / "genes" / author / gene_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps({
            "id": gene_id, "title": "Что-то", "summary": "про то",
            "author": author, "created_at": 1, "base_commit": "abc",
            "files": files, "version": "1",
        }), encoding="utf-8")
        (directory / "patch.diff").write_bytes(b"--- a/x\n+++ b/x\n+y\n")
        (directory / "notes.md").write_text("зачем\n", encoding="utf-8")
        return {"id": gene_id, "author": author, "files": files}

    def test_foreign_gene_is_offered_with_a_reason(self) -> None:
        self._gene("gene-mg-11111111-001", "mg-11111111",
                   ["tools/minigen-gui/bridge/minigen_genes.py"])
        genes_mod.my_files = lambda: ["tools/minigen-gui/bridge/minigen_genes.py"]
        try:
            result = genes_mod.suggestions()
        finally:
            genes_mod.my_files = _real_my_files
        items = result["suggestions"]
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["fits"])
        self.assertIn("те же файлы", items[0]["why"])
        self.assertEqual(result["fits"], 1)

    def test_own_and_taken_genes_are_not_offered(self) -> None:
        self._gene("gene-mg-22222222-001", "mg-22222222", ["a.py"])
        self._gene("gene-mg-33333333-001", "mg-33333333", ["b.py"])
        data = lineage.instance()
        data["instance_id"] = "mg-22222222"
        data["genes"] = [{"gene": "gene-mg-33333333-001", "from": "mg-33333333"}]
        lineage._write_json(lineage.INSTANCE_FILE, data)
        genes_mod.my_files = lambda: ["a.py"]
        try:
            result = genes_mod.suggestions()
        finally:
            genes_mod.my_files = _real_my_files
        self.assertEqual(result["suggestions"], [])

    def test_unrelated_gene_is_marked_as_not_fitting(self) -> None:
        self._gene("gene-mg-44444444-001", "mg-44444444", ["other/file.py"])
        genes_mod.my_files = lambda: ["tools/minigen-gui/bridge/minigen_genes.py"]
        try:
            result = genes_mod.suggestions()
        finally:
            genes_mod.my_files = _real_my_files
        item = result["suggestions"][0]
        self.assertFalse(item["fits"])
        self.assertIn("не пересекается", item["why"])


_real_my_files = genes_mod.my_files


class ManualExchangeTest(_CopyTestCase):
    def test_export_and_import_round_trip(self) -> None:
        directory = self.pool_dir / "genes" / "mg-55555555" / "gene-mg-55555555-001"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps({
            "id": "gene-mg-55555555-001", "title": "Наработка", "author": "mg-55555555",
            "files": ["a.py"], "created_at": 1,
        }), encoding="utf-8")
        (directory / "patch.diff").write_bytes(b"--- a/a.py\n+++ b/a.py\n+x\n")
        (directory / "notes.md").write_text("зачем\n", encoding="utf-8")
        exported = genes_mod.export_pool("")
        self.assertTrue(exported.get("ok"), exported)
        self.assertTrue((self.export_dir / "genes" / "mg-55555555"
                         / "gene-mg-55555555-001" / "manifest.json").is_file())
        # вторая копия: свой пул, папка принесена вручную
        other = Path(self.tmp.name) / "other-pool"
        genes_mod.pool_dir = lambda: other
        try:
            imported = genes_mod.import_pool(str(self.export_dir))
        finally:
            genes_mod.pool_dir = self._saved["pool_dir_fn"]
        self.assertTrue(imported.get("ok"), imported)
        self.assertEqual(imported["added"], ["gene-mg-55555555-001"])
        self.assertTrue((other / "genes" / "mg-55555555"
                         / "gene-mg-55555555-001" / "manifest.json").is_file())
        # повторный внос ничего не ломает
        genes_mod.pool_dir = lambda: other
        try:
            again = genes_mod.import_pool(str(self.export_dir))
        finally:
            genes_mod.pool_dir = self._saved["pool_dir_fn"]
        self.assertEqual(again["added"], [])
        self.assertEqual(again["skipped"], ["gene-mg-55555555-001"])


if __name__ == "__main__":
    unittest.main()
