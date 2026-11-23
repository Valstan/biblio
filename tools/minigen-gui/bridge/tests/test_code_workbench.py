"""Тесты рабочего стола самообновления (code-workbench).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Проверяем то, что может навредить:

- **правка не выходит за пределы репозитория** (обход через `..` — отказ);
- **секреты не читаются** (файлы с ключами и авторизацией закрыты);
- **правка точная**: фрагмент с несколькими вхождениями не применяется;
- **правка идёт в ветку, а не в master** (коммит и PR отклоняют master);
- **полный цикл в мини-репозитории**: ветка → правка → проверки → коммит.

Тесты идут на временном git-репозитории: боевой репозиторий не трогают.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_code as code  # noqa: E402


def make_repo(path: Path) -> None:
    """Мини-репозиторий с одним файлом."""
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "Тест"
    env["GIT_AUTHOR_EMAIL"] = "test@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Тест"
    env["GIT_COMMITTER_EMAIL"] = "test@example.invalid"
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=str(path), check=True,
                   env=env, capture_output=True)
    (path / "mod.py").write_text("VALUE = 1\nprint(VALUE)\n", encoding="utf-8")
    (path / "config.yaml").write_text("apiKey: secret\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True,
                   env=env, capture_output=True)


class GuardTest(unittest.TestCase):
    """Охранники: периметр и секреты."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        make_repo(self.repo)
        self._old_repo = code.REPO
        self._old_worktrees = code.WORKTREES
        code.REPO = self.repo
        code.WORKTREES = self.repo / ".claude" / "worktrees"

    def tearDown(self) -> None:
        code.REPO = self._old_repo
        code.WORKTREES = self._old_worktrees
        self.tmp.cleanup()

    def test_path_outside_repo_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            code.resolve_in_repo("../outside.txt")

    def test_absolute_path_outside_repo_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            code.resolve_in_repo(str(Path(self.tmp.name) / "elsewhere.txt"))

    def test_secret_files_detected(self) -> None:
        self.assertTrue(code.is_secret_path(Path("a/b/config.yaml")))
        self.assertTrue(code.is_secret_path(Path("a/b/.env.local")))
        self.assertTrue(code.is_secret_path(Path("a/b/secret_token.txt")))
        self.assertTrue(code.is_secret_path(Path("a/b/key.pem")))
        self.assertFalse(code.is_secret_path(Path("a/b/mod.py")))

    def test_read_refuses_secret(self) -> None:
        result = code.read_file("config.yaml")
        self.assertIn("error", result)
        self.assertIn("закрыт", result["error"])

    def test_read_regular_file(self) -> None:
        result = code.read_file("mod.py")
        self.assertIn("VALUE = 1", result["content"])
        self.assertFalse(result["truncated"])

    def test_list_and_search(self) -> None:
        files = code.list_files()
        self.assertIn("mod.py", files)
        hits = code.search("VALUE")
        self.assertTrue(any(hit["path"] == "mod.py" for hit in hits))

    def test_search_does_not_leak_secrets(self) -> None:
        hits = code.search("apiKey")
        self.assertFalse([hit for hit in hits if hit["path"] == "config.yaml"])


class PatchFlowTest(unittest.TestCase):
    """Полный цикл правки в мини-репозитории."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        make_repo(self.repo)
        self._old_repo = code.REPO
        self._old_worktrees = code.WORKTREES
        self._old_state = code.STATE_FILE
        code.REPO = self.repo
        code.WORKTREES = self.repo / ".claude" / "worktrees"
        code.STATE_FILE = Path(self.tmp.name) / "fix-state.json"

    def tearDown(self) -> None:
        code.REPO = self._old_repo
        code.WORKTREES = self._old_worktrees
        code.STATE_FILE = self._old_state
        self.tmp.cleanup()

    def test_patch_without_worktree_refused(self) -> None:
        result = code.apply_patch("mod.py", "VALUE = 1", "VALUE = 2")
        self.assertIn("error", result)

    def test_ambiguous_replace_refused(self) -> None:
        created = code.create_worktree("tweak value")
        self.assertTrue(created.get("ok"), created)
        (code.current_worktree() / "mod.py").write_text("X = 1\nX = 1\n", encoding="utf-8")
        result = code.apply_patch("mod.py", "X = 1", "X = 2")
        self.assertIn("error", result)
        self.assertIn("неоднозначна", result["error"])

    def test_missing_fragment_refused(self) -> None:
        code.create_worktree("tweak value")
        result = code.apply_patch("mod.py", "нет такого", "x")
        self.assertIn("не найден", result["error"])

    def test_full_cycle(self) -> None:
        created = code.create_worktree("tweak value")
        self.assertTrue(created.get("ok"), created)
        branch = created["branch"]
        self.assertTrue(branch.startswith("agent/minigen-fix-"))
        self.assertNotEqual(branch, "master")

        patched = code.apply_patch("mod.py", "VALUE = 1", "VALUE = 2")
        self.assertTrue(patched.get("ok"), patched)

        committed = code.commit("fix(mod): значение 2")
        self.assertTrue(committed.get("ok"), committed)
        self.assertEqual(committed["branch"], branch)

        # коммит лежит в ветке, master не тронут
        head = code.git(["log", "-1", "--pretty=%s"], code.current_worktree())
        self.assertIn("значение 2", head["out"])
        master = code.git(["log", "-1", "--pretty=%s"], self.repo)
        self.assertNotIn("значение 2", master["out"])

        info = code.worktree_info()
        self.assertTrue(info["alive"])
        self.assertEqual(info["branch"], branch)

    def test_commit_on_master_refused(self) -> None:
        # без worktree команда обязана отказать, а не коммитить в master
        result = code.commit("oops")
        self.assertIn("error", result)

    def test_diff_shows_changes(self) -> None:
        code.create_worktree("tweak value")
        code.apply_patch("mod.py", "VALUE = 1", "VALUE = 2")
        result = code.diff()
        self.assertIn("mod.py", " ".join(result["files"]))
        self.assertIn("VALUE = 2", result["diff"])

    def test_commit_warns_without_test(self) -> None:
        code.create_worktree("tweak value")
        code.apply_patch("mod.py", "VALUE = 1", "VALUE = 2")
        result = code.commit("fix(mod): значение 2")
        self.assertTrue(result.get("ok"), result)
        self.assertIn("тесты", result.get("note", ""),
                      "правка без теста обязана вызывать вопрос")

    def test_commit_without_test_note_when_test_changed(self) -> None:
        code.create_worktree("tweak value")
        (code.current_worktree() / "test_mod.py").write_text(
            "def test_value():\n    assert True\n", encoding="utf-8")
        code.apply_patch("mod.py", "VALUE = 1", "VALUE = 2")
        result = code.commit("fix(mod): значение 2 и тест")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("note", ""), "")

    def test_discard_removes_worktree(self) -> None:
        created = code.create_worktree("temp fix")
        self.assertTrue(created.get("ok"))
        path = Path(created["worktree"])
        self.assertTrue(path.exists())
        result = code.discard_worktree()
        self.assertTrue(result.get("ok"))
        self.assertFalse(path.exists())
        self.assertFalse(code.current_worktree())


if __name__ == "__main__":
    unittest.main()
