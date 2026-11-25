"""Тесты своего клона копии на GitHub (24.11, заказ владельца).

Заказ: «для каждого нового запущенного минигена на каждом компьютере она
должна создавать свой клон на гитхабе и вести разработку уже со своим клоном
репозитория».

Проверяем:

- **имя репозитория копии** — своё на каждую копию (`minigen-<идентификатор>`);
- **GitHub не даёт форк своего же репозитория в своём же аккаунте** ⇒ копия
  заводит свой репозиторий и указывает `upstream` на репозиторий программы
  (тот же рабочий процесс: ветка у себя, PR — в upstream);
- **клон берётся рядом с программой**, upstream подключается, пул генов — тоже;
- **PR идёт в upstream**, а ветка — в свой репозиторий (`--head owner:branch`);
- **наружу не уходят** имена машин и пути.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_clone as clone_mod  # noqa: E402
import minigen_code as code        # noqa: E402
import minigen_lineage as lineage  # noqa: E402


class _FakeGit:
    """Заглушка git: отвечает по заданным правилам и пишет, что просили."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.exists = False
        self.remotes: dict[str, str] = {}
        self.branch = "master"

    def __call__(self, args, cwd, timeout=300):
        self.calls.append(list(args))
        head = args[0] if args else ""
        if head == "remote":
            if len(args) > 1 and args[1] == "get-url":
                name = args[2]
                return {"ok": name in self.remotes, "out": self.remotes.get(name, ""),
                        "err": "", "code": 0}
            if len(args) > 1 and args[1] == "-v":
                out = "\n".join("%s %s (fetch)" % (k, v)
                                for k, v in self.remotes.items())
                return {"ok": True, "out": out, "err": "", "code": 0}
        if head == "clone":
            self.exists = True
            # настоящий клон — это каталог с `.git` (иначе «клона нет»)
            try:
                Path(str(args[-1]), ".git").mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            return {"ok": True, "out": "cloned", "err": "", "code": 0}
        if head == "remote" and len(args) > 2 and args[1] == "add":
            self.remotes[args[2]] = args[3]
        if head == "remote" and len(args) > 2 and args[1] == "set-url":
            self.remotes[args[2]] = args[3]
        if head == "init":
            self.exists = True
        if head == "rev-parse":
            return {"ok": True, "out": self.branch, "err": "", "code": 0}
        if head == "status":
            return {"ok": True, "out": "", "err": "", "code": 0}
        return {"ok": True, "out": "", "err": "", "code": 0}


class _FakeGh:
    def __init__(self, fork_allowed: bool = False) -> None:
        self.fork_allowed = fork_allowed
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout=300):
        self.calls.append(list(args))
        head = args[0] if args else ""
        if head == "repo" and "view" in args:
            return {"ok": False, "out": "", "err": "not found", "code": 1}
        if head == "repo" and "fork" in args:
            if self.fork_allowed:
                return {"ok": True, "out": "forked", "err": "", "code": 0}
            return {"ok": False, "code": 1, "out": "",
                    "err": "A single user account cannot own both a parent and fork."}
        if head == "repo" and "create" in args:
            return {"ok": True, "out": "created", "err": "", "code": 0}
        return {"ok": True, "out": "", "err": "", "code": 0}


class CloneNameTest(unittest.TestCase):
    def test_repo_name_is_per_copy(self) -> None:
        self.assertEqual(clone_mod.copy_repo_name("mg-abcdef12"), "minigen-abcdef12")

    def test_no_machine_data_in_the_name(self) -> None:
        name = clone_mod.copy_repo_name("mg-abcdef12")
        for leak in ("users", "\\", "/", "valstan"):
            self.assertNotIn(leak, name.lower())


class SetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._old_clone = clone_mod.CLONE_DIR
        self._old_pool = clone_mod.POOL_DIR
        clone_mod.CLONE_DIR = Path(self.tmp.name) / "clone"
        clone_mod.POOL_DIR = Path(self.tmp.name) / "pool"
        self._git = _FakeGit()
        self._gh = _FakeGh()
        self._old_git = clone_mod.git
        self._old_gh = clone_mod.gh
        clone_mod.git = self._git
        clone_mod.gh = self._gh

    def tearDown(self) -> None:
        clone_mod.CLONE_DIR = self._old_clone
        clone_mod.POOL_DIR = self._old_pool
        clone_mod.git = self._old_git
        clone_mod.gh = self._old_gh
        self.tmp.cleanup()

    def test_own_repo_is_created_when_fork_is_forbidden(self) -> None:
        report = clone_mod.setup(upstream="Valstan/biblio", pool="Valstan/minigen-genepool",
                                 mode="own")
        self.assertTrue(report.get("ok"), report)
        kinds = [str(s.get("how")) for s in report["steps"]]
        self.assertIn("свой репозиторий", kinds)
        # upstream подключён
        self.assertIn("upstream", self._git.remotes)
        self.assertEqual(self._git.remotes["upstream"],
                         "https://github.com/Valstan/biblio.git")

    def test_fork_is_preferred_when_possible(self) -> None:
        self._gh = _FakeGh(fork_allowed=True)
        clone_mod.gh = self._gh
        report = clone_mod.setup(upstream="Valstan/biblio", pool="Valstan/minigen-genepool",
                                 mode="own")
        kinds = [str(s.get("how")) for s in report["steps"]]
        self.assertIn("форк", kinds)

    def test_default_mode_creates_no_repository_on_github(self) -> None:
        """Дефолт (промт #130) — папка в общем репозитории, без своего репо.

        ⚠️ Живой случай 25.11: пока дефолтом был «свой репозиторий», каждая
        новая копия заводила репозиторий в аккаунте владельца — а владелец
        просил, чтобы по умолчанию копия работала папкой в общем репозитории
        и аккаунт на GitHub не требовался.
        """
        report = clone_mod.setup(upstream="Valstan/biblio", pool="Valstan/minigen-genepool")
        self.assertEqual(report.get("mode"), "shared")
        created = [call for call in self._gh.calls if call[:2] == ["repo", "create"]]
        self.assertEqual(created, [], "свой репозиторий создавать нельзя")
        forks = [call for call in self._gh.calls if call[:2] == ["repo", "fork"]]
        self.assertEqual(forks, [], "форк заводить нельзя")

    def test_clone_and_pool_are_taken(self) -> None:
        report = clone_mod.setup(upstream="Valstan/biblio", pool="Valstan/minigen-genepool")
        self.assertTrue(report.get("ok"), report)
        clones = [c for c in self._git.calls if c and c[0] == "clone"]
        self.assertTrue(clones, "клон должен быть взят")

    def test_setup_never_leaks_paths_into_the_repo_name(self) -> None:
        report = clone_mod.setup(upstream="Valstan/biblio", pool="Valstan/minigen-genepool")
        self.assertNotIn(str(self.tmp.name).lower(),
                         str(report.get("repo", "")).lower())


class RegisterFolderTest(unittest.TestCase):
    """Папка копии в общем репозитории программы (промт #130)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._old_clone = clone_mod.CLONE_DIR
        self._old_git = clone_mod.git
        self._old_instance = lineage.instance
        clone_mod.CLONE_DIR = Path(self.tmp.name) / "clone"
        (clone_mod.CLONE_DIR / ".git").mkdir(parents=True, exist_ok=True)
        self._git = _FakeGit()
        self._git.remotes = {"origin": "https://github.com/Valstan/biblio.git"}
        clone_mod.git = self._git
        lineage.instance = lambda: {"instance_id": "mg-abcdef12",
                                    "family": "minigen", "generation": 2,
                                    "fork_of": {"commit": "abc1234"}}

    def tearDown(self) -> None:
        clone_mod.CLONE_DIR = self._old_clone
        clone_mod.git = self._old_git
        lineage.instance = self._old_instance
        self.tmp.cleanup()

    def test_folder_is_registered_in_the_program_repo(self) -> None:
        report = clone_mod.register_folder("mg-abcdef12", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        self.assertEqual(report["folder"], "copies/mg-abcdef12")
        self.assertEqual(report["branch"], "copies/mg-abcdef12")
        marker = clone_mod.CLONE_DIR / "copies" / "mg-abcdef12" / "copy.json"
        self.assertTrue(marker.is_file())
        text = marker.read_text(encoding="utf-8")
        self.assertIn("mg-abcdef12", text)
        # ⚠️ путь и имя машины в общий репозиторий не попадают (D-038)
        self.assertNotIn(str(self.tmp.name).lower(), text.lower())
        pushes = [call for call in self._git.calls if call[:1] == ["push"]]
        self.assertTrue(pushes, "ветка копии должна уйти в репозиторий программы")
        self.assertIn("refs/heads/copies/mg-abcdef12", " ".join(pushes[0]))

    def test_a_lookalike_remote_is_not_pushed_to(self) -> None:
        """«Valstan/biblio-minigen» — не репозиторий программы."""
        self._git.remotes = {"origin": "https://github.com/Valstan/biblio-minigen.git"}
        report = clone_mod.register_folder("mg-abcdef12", upstream="Valstan/biblio")
        self.assertFalse(report.get("ok"), report)
        self.assertEqual([c for c in self._git.calls if c[:1] == ["push"]], [])

    def test_without_a_clone_it_is_refused(self) -> None:
        clone_mod.CLONE_DIR = Path(self.tmp.name) / "empty"
        report = clone_mod.register_folder("mg-abcdef12")
        self.assertFalse(report.get("ok"), report)
        self.assertIn("клона нет", report["why"])

    def test_instance_id_cannot_escape_the_folder(self) -> None:
        report = clone_mod.register_folder("../../evil", upstream="Valstan/biblio")
        self.assertNotIn("..", report["folder"])
        self.assertTrue(report["folder"].startswith("copies/"))


class PullRequestTargetTest(unittest.TestCase):
    """Ветка — в свой репозиторий, PR — в репозиторий программы."""

    def _with_remotes(self, remotes: dict[str, str]):
        old = code.git

        def fake_git(args, cwd, timeout=300):
            if args[:2] == ["remote", "get-url"] and args[2] in remotes:
                return {"ok": True, "out": remotes[args[2]], "err": "", "code": 0}
            return {"ok": False, "out": "", "err": "", "code": 1}

        code.git = fake_git
        return old

    def test_upstream_is_preferred(self) -> None:
        old = self._with_remotes({
            "origin": "https://github.com/Valstan/minigen-abc12345.git",
            "upstream": "https://github.com/Valstan/biblio.git",
        })
        try:
            self.assertEqual(code.upstream_slug(Path(".")), "Valstan/biblio")
        finally:
            code.git = old

    def test_origin_is_the_fallback(self) -> None:
        old = self._with_remotes({
            "origin": "https://github.com/Valstan/minigen-abc12345.git",
        })
        try:
            self.assertEqual(code.upstream_slug(Path(".")), "Valstan/minigen-abc12345")
        finally:
            code.git = old

    def test_slug_parsing(self) -> None:
        self.assertEqual(code._repo_slug("https://github.com/Valstan/biblio.git"),
                         "Valstan/biblio")
        self.assertEqual(code._repo_slug("https://github.com/Valstan/biblio"),
                         "Valstan/biblio")
        self.assertEqual(code._repo_slug("git@github.com:Valstan/biblio.git"),
                         "Valstan/biblio")
        self.assertEqual(code._repo_slug("не репозиторий"), "")


if __name__ == "__main__":
    unittest.main()
