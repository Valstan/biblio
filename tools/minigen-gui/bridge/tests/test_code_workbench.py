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
    """Мини-репозиторий с одним файлом.

    ⚠️ 23.11: identity git задаётся **в самом репозитории**, а не только через
    переменные окружения. Иначе тесты проходят на машине владельца (где
    глобальный `user.name` есть) и падают в CI: «Author identity unknown».
    """
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "Тест"
    env["GIT_AUTHOR_EMAIL"] = "test@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Тест"
    env["GIT_COMMITTER_EMAIL"] = "test@example.invalid"
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=str(path), check=True,
                   env=env, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Тест"], cwd=str(path),
                   check=True, env=env, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                   cwd=str(path), check=True, env=env, capture_output=True)
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


class BranchNameTest(unittest.TestCase):
    """Имя ветки и worktree: название правки нейросеть пишет по-русски.

    ⚠️ Живой случай 24.11: `[^a-zA-Z0-9]` выбрасывал кириллицу целиком ⇒
    slug пустовал ⇒ **все** ветки назывались `agent/minigen-fix-fix`, а
    worktree'ы — `fix`. Вторая правка упиралась в «worktree уже есть»: то
    есть встроенная нейросеть не могла работать с программой, для которой и
    написана.
    """

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

    def test_russian_title_gives_latin_slug(self) -> None:
        slug = code.slugify("Раскатка чистит старые бандли окна")
        self.assertTrue(slug)
        self.assertNotEqual(slug, "fix")
        self.assertTrue(all(ord(char) < 128 for char in slug),
                        "имя ветки должно быть латинским: %s" % slug)

    def test_different_titles_give_different_slugs(self) -> None:
        self.assertNotEqual(code.slugify("Правка"), code.slugify("Другая правка"))

    def test_untranslatable_title_still_gets_name(self) -> None:
        slug = code.slugify("★★★")
        self.assertTrue(slug)
        self.assertNotEqual(slug, "fix")

    def test_same_title_twice_gives_two_worktrees(self) -> None:
        first = code.create_worktree("Одна и та же правка")
        second = code.create_worktree("Одна и та же правка")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertNotEqual(first["worktree"], second["worktree"])
        self.assertNotEqual(first["branch"], second["branch"])


class PullRequestHeadTest(unittest.TestCase):
    """Как открывается PR, когда репозиторий копии — не форк.

    ⚠️ Живой случай 24.11: GitHub **не даёт** форк своего же репозитория в
    своём же аккаунте, поэтому копия держит свой репозиторий — и GitHub не
    открывает PR из него: «Head sha can't be blank … Head ref must be a
    branch» (ветку он ищет только среди форков сети). Значит, ветку надо
    отправить ещё и в репозиторий программы — под именем с идентификатором
    копии, иначе две копии с одинаковым названием правки столкнулись бы.
    """

    def setUp(self) -> None:
        self._old_gh = code.gh
        self._old_fork = code.fork_parent

    def tearDown(self) -> None:
        code.gh = self._old_gh
        code.fork_parent = self._old_fork

    def test_same_repository_keeps_branch(self) -> None:
        head, extra = code.pr_head("agent/minigen-fix-x", "Valstan/biblio", "Valstan/biblio")
        self.assertEqual(head, "agent/minigen-fix-x")
        self.assertFalse(extra)

    def test_fork_uses_owner_prefix(self) -> None:
        code.fork_parent = lambda repo: "Valstan/biblio"
        head, extra = code.pr_head("agent/minigen-fix-x", "Valstan/kopiya",
                                   "Valstan/biblio")
        self.assertEqual(head, "Valstan:agent/minigen-fix-x")
        self.assertFalse(extra)

    def test_not_a_fork_pushes_branch_to_program_repo(self) -> None:
        code.fork_parent = lambda repo: ""
        head, extra = code.pr_head("agent/minigen-fix-x", "Valstan/kopiya",
                                   "Valstan/biblio", "mg-693f8fdd")
        self.assertEqual(head, "agent/mg-693f8fdd/minigen-fix-x")
        self.assertTrue(extra, "ветку надо отправить ещё и в репозиторий программы")

    def test_two_copies_do_not_share_upstream_branch(self) -> None:
        first = code.upstream_branch("agent/minigen-fix-x", "mg-11111111")
        second = code.upstream_branch("agent/minigen-fix-x", "mg-22222222")
        self.assertNotEqual(first, second)

    def test_fork_parent_reads_github(self) -> None:
        code.gh = lambda args, cwd=None, timeout=300: {
            "ok": True, "code": 0, "out": '{"parent": {"full_name": "Valstan/biblio"}}',
            "err": ""}
        self.assertEqual(code.fork_parent("Valstan/kopiya"), "Valstan/biblio")
        code.gh = lambda args, cwd=None, timeout=300: {
            "ok": True, "code": 0, "out": '{"parent": null}', "err": ""}
        self.assertEqual(code.fork_parent("Valstan/kopiya"), "")
        code.gh = lambda args, cwd=None, timeout=300: {
            "ok": False, "code": 1, "out": "", "err": "нет gh"}
        self.assertEqual(code.fork_parent("Valstan/kopiya"), "")


if __name__ == "__main__":
    unittest.main()
