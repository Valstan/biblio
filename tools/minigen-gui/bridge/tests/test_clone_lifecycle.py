"""Жизненный цикл копии «МиниГена»: регистрация папки и обновление (промт #98).

⚠️ Живой случай 26.11 (промт КАРМАНА #98, полоса «МиниГен»): копия на живой
машине работала, а механика обновления была сломана:

- `clone.sync` тянул **текущую** ветку клона, а ветка копии `copies/<id>`
  живёт без слежения ⇒ pull падал («There is no tracking information for the
  current branch») ⇒ владелец видел «обновление не прошло» на исправной
  машине;
- локальный `master` копии не догонял master программы ⇒ правки копии
  (worktree'и) брались из устаревшего кода;
- повторная регистрация папки копии **пересоздавала** ветку от master и пушила
  без force ⇒ push отвергался как non-fast-forward («папка не ушла в
  репозиторий программы») — и регистрация копии переставала обновляться.

Здесь — настоящий git, без сети: «репозиторий программы» и «пул генов» —
локальные bare-репозитории.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_clone as clone_mod  # noqa: E402
import minigen_lineage as lineage  # noqa: E402

INSTANCE = {"instance_id": "mg-probe1234", "family": "minigen",
            "generation": 1, "fork_of": {"commit": "abc1234"},
            "upstream": "Valstan/biblio"}


class CopyLifecycleGitTest(unittest.TestCase):
    """Копия на настоящем git: регистрация папки и обновление."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # глобальный хук машины (ADR-0002) запрещает пуш в master — в тесте
        # хук отключаем: тут проверяется механика копии, а не канон репозитория
        self.nohooks = root / "nohooks"
        self.nohooks.mkdir(parents=True, exist_ok=True)
        self.bare = root / "github.com" / "Valstan" / "biblio"
        self.bare.parent.mkdir(parents=True, exist_ok=True)
        self.url = "file:///%s" % str(self.bare).replace("\\", "/")
        self._git("init", "--bare", "-b", "master", str(self.bare), cwd=root)
        seed = root / "seed"
        self._git("init", "-b", "master", str(seed), cwd=root)
        self._write(seed / "program.txt", "программа\n")
        self._commit(seed, "программа: первая версия")
        self._git("-c", "core.hooksPath=%s" % self.nohooks,
                  "push", str(self.bare), "master", cwd=seed)

        self.clone = root / "clone"
        self._git("clone", self.url, str(self.clone), cwd=root)
        self._git("config", "user.name", "probe", cwd=self.clone)
        self._git("config", "user.email", "probe@example.invalid", cwd=self.clone)
        self._git("remote", "set-url", "origin", self.url, cwd=self.clone)

        # пул генов — свой bare-репозиторий, ветка `pool`
        self.pool_bare = root / "pool.git"
        self._git("init", "--bare", "-b", "pool", str(self.pool_bare), cwd=root)
        pool_seed = root / "pool-seed"
        self._git("init", "-b", "pool", str(pool_seed), cwd=root)
        self._write(pool_seed / "map.md", "карта родословной\n")
        self._commit(pool_seed, "карта")
        self._git("-c", "core.hooksPath=%s" % self.nohooks,
                  "push", str(self.pool_bare), "pool", cwd=pool_seed)
        self.pool = root / "pool"
        self._git("clone", "file:///%s" % str(self.pool_bare).replace("\\", "/"),
                  str(self.pool), cwd=root)

        self._old_clone = clone_mod.CLONE_DIR
        self._old_pool = clone_mod.POOL_DIR
        self._old_instance = lineage.instance
        clone_mod.CLONE_DIR = self.clone
        clone_mod.POOL_DIR = self.pool
        lineage.instance = lambda: dict(INSTANCE)

    def tearDown(self) -> None:
        clone_mod.CLONE_DIR = self._old_clone
        clone_mod.POOL_DIR = self._old_pool
        lineage.instance = self._old_instance
        self.tmp.cleanup()

    # ─────────────────────────────────────────────────────────── помощники

    def _git(self, *args: str, cwd: Path) -> str:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), proc.stderr))
        return (proc.stdout or "").strip()

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _commit(self, cwd: Path, message: str) -> None:
        self._git("add", "-A", cwd=cwd)
        self._git("-c", "user.name=probe",
                  "-c", "user.email=probe@example.invalid",
                  "commit", "-m", message, cwd=cwd)

    def _program_moves_forward(self) -> None:
        """Программа уехала вперёд (новый коммит в master программы)."""
        seed = Path(self.tmp.name) / "seed"
        self._write(seed / "new-module.txt", "новый модуль\n")
        self._commit(seed, "программа: новый модуль")
        self._git("-c", "core.hooksPath=%s" % self.nohooks,
                  "push", str(self.bare), "master", cwd=seed)

    # ─────────────────────────────────────────────────────────────── тесты

    def test_registration_survives_the_program_moving_forward(self) -> None:
        """⚠️ Живой случай 26.11: повторная регистрация падала как
        non-fast-forward, и папка копии переставала обновляться."""
        first = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(first.get("ok"), first)
        self._program_moves_forward()
        second = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(second.get("ok"), second)
        # в репозитории программы — новая версия программы на ветке копии
        branch = "copies/mg-probe1234"
        files = self._git("ls-tree", "-r", "--name-only", branch, cwd=self.bare)
        self.assertIn("new-module.txt", files)
        self.assertIn("copies/mg-probe1234/copy.json", files)

    def test_copy_branch_gets_tracking(self) -> None:
        """Без слежения `git pull --ff-only` (это делает sync) не знает,
        откуда тянуть, — и обновление падает на исправной машине."""
        report = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        tracking = self._git("rev-parse", "--abbrev-ref",
                             "--symbolic-full-name",
                             "copies/mg-probe1234@{u}", cwd=self.clone)
        self.assertTrue(tracking, "у ветки копии должно быть слежение")

    def test_sync_updates_the_base_of_the_copy(self) -> None:
        """Локальный master копии должен догонять программу: правки копии
        берутся из него (worktree'и)."""
        report = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        self._program_moves_forward()
        result = clone_mod.sync()
        self.assertTrue(result.get("ok"), result)
        local = self._git("rev-parse", "--verify", "master", cwd=self.clone)
        fresh = self._git("rev-parse", "--verify", "origin/master", cwd=self.clone)
        self.assertEqual(local, fresh, "master копии не догнал программу")
        names = [str(step.get("what")) for step in result["steps"]]
        self.assertIn("master программы", names)

    def test_sync_brings_the_copy_branch_up_to_date(self) -> None:
        """Ветка копии в репозитории программы должна знать новую версию."""
        report = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        self._program_moves_forward()
        result = clone_mod.sync()
        self.assertTrue(result.get("ok"), result)
        files = self._git("ls-tree", "-r", "--name-only",
                          "copies/mg-probe1234", cwd=self.bare)
        self.assertIn("new-module.txt", files)

    def test_sync_does_not_touch_a_dirty_tree(self) -> None:
        """Локальные правки не трогаем (правило модуля)."""
        report = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        self._write(self.clone / "моя правка.txt", "правка копии\n")
        result = clone_mod.sync()
        self.assertFalse(result.get("ok"), result)
        reasons = " ".join(str(step.get("why")) for step in result["steps"])
        self.assertIn("локальные правки", reasons)
        self.assertTrue((self.clone / "моя правка.txt").is_file())

    def test_sync_gives_the_copy_branch_tracking(self) -> None:
        """⚠️ Живой случай 26.11: после обновления у ветки копии слежения не
        было — `git pull --ff-only` в клоне падал, и владелец видел
        «обновление не прошло»."""
        report = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        # снимем слежение — как будто ветку завели «по-старому»
        self._git("branch", "--unset-upstream", "copies/mg-probe1234", cwd=self.clone)
        result = clone_mod.sync()
        self.assertTrue(result.get("ok"), result)
        tracking = self._git("rev-parse", "--abbrev-ref",
                             "--symbolic-full-name",
                             "copies/mg-probe1234@{u}", cwd=self.clone)
        self.assertTrue(tracking, "sync должен оставить слежение за веткой копии")

    def test_sync_never_pushes_into_master(self) -> None:
        """Канон машины (ADR-0002): пуш — только в ветку, `master` программы
        копия не двигает."""
        report = clone_mod.register_folder("mg-probe1234", upstream="Valstan/biblio")
        self.assertTrue(report.get("ok"), report)
        before = self._git("rev-parse", "refs/heads/master", cwd=self.bare)
        self._program_moves_forward()
        after_program = self._git("rev-parse", "refs/heads/master", cwd=self.bare)
        self.assertNotEqual(before, after_program, "программа должна была уехать вперёд")
        clone_mod.sync()
        now = self._git("rev-parse", "refs/heads/master", cwd=self.bare)
        self.assertEqual(now, after_program,
                         "master программы копия двигать не должна")
        heads = self._git("for-each-ref", "--format=%(refname)",
                          "refs/heads", cwd=self.bare).split()
        self.assertIn("refs/heads/copies/mg-probe1234", heads)
        self.assertIn("refs/heads/master", heads)


if __name__ == "__main__":
    unittest.main()
