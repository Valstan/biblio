# -*- coding: utf-8 -*-
"""Приёмка: копия берёт код из **своего** репозитория (живой случай 28.11).

Запуск: python tools/minimax-project-areas/test_copy_own_ref.py

**Что было.** Раскатка читала эталон из `origin/master`, а копия программы —
отдельный репозиторий (`Valstan/minigen-<идентификатор>`), где ветки `master`
**не заводится**: прямой пуш в `master` запрещён глобальным хуком, поэтому
код копии лежит в ветке `base`. На живой машине (28.11, «Обновление
программы») вышло: «не знаю, какой коммит раскатываю (origin/master)» —
**раскатка останавливалась**, и копия не обновлялась вовсе.

**Что стало.** Правило одно на всю программу (`minigen_core.own_ref`):
1. `origin/master` есть ⇒ это репозиторий **программы** (оригинал);
2. иначе `origin/base` есть ⇒ это репозиторий **копии** ⇒ своя ветка;
3. иначе — ветка, за которой следит текущая;
4. иначе — `origin/master` (пусть скажет git, что не вышло).

Правило зовут **и** раскатка, **и** кнопка «Обновить программу» — вторая
правда тут же разошлась бы (как «Режим» с «Автоматом»).
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import minigen_core as core  # noqa: E402


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, timeout=120)


class OwnRefTest(unittest.TestCase):
    """Правило: откуда машина берёт код."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _repo_with(self, branch: str) -> pathlib.Path:
        """Репозиторий, у которого на `origin` есть только ветка `branch`.

        ⚠️ «Удалённый» репозиторий — **bare**: в обычный пуш в его текущую
        ветку запрещён (`receive.denyCurrentBranch`), и ветка бы не доехала.
        """
        remote = self.root / ("remote-" + branch)
        work = self.root / ("work-" + branch)
        git(["init", "-q", "--bare", str(remote)], self.root)
        git(["init", "-q", "--initial-branch=" + branch, str(work)], self.root)
        git(["config", "user.email", "test@example.com"], work)
        git(["config", "user.name", "Тест"], work)
        (work / "file.txt").write_text("код\n", encoding="utf-8")
        git(["add", "-A"], work)
        git(["commit", "-q", "-m", "первый"], work)
        git(["remote", "add", "origin", str(remote)], work)
        push = git(["push", "-q", "origin", branch], work)
        self.assertEqual(push.returncode, 0,
                         "ветка не доехала: %s" % push.stderr)
        git(["fetch", "-q", "origin"], work)
        return work

    def test_the_program_takes_its_own_master(self):
        """Репозиторий программы: ветка `master` — и берём её."""
        repo = self._repo_with("master")
        self.assertEqual(core.own_ref(repo), "origin/master")

    def test_the_copy_takes_its_own_base(self):
        """⚠️ Живой случай 28.11: у копии ветки `master` нет — есть `base`."""
        repo = self._repo_with("base")
        self.assertEqual(core.own_ref(repo), "origin/base",
                         "копия взяла бы чужую ветку — раскатка бы упала")

    def test_a_missing_repository_is_not_a_crash(self):
        self.assertEqual(core.own_ref(self.root / "нет-такого"), "origin/master")


class RolloutUsesTheRuleTest(unittest.TestCase):
    """Раскатка и кнопка обновления зовут **одно** правило."""

    def test_the_rollout_uses_own_ref(self):
        source = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
        self.assertIn("from minigen_core import own_ref", source)
        self.assertIn("ref = own_ref(REPO)", source)
        # ⚠️ Старая строка — «источник по умолчанию master» — возвращаться не
        # должна: на копии ветки master нет.
        self.assertNotIn('ref = "origin/master"', source)

    def test_the_update_button_uses_own_ref(self):
        source = (REPO / "tools" / "minigen-gui" / "bridge" /
                  "minigen_update.py").read_text(encoding="utf-8")
        self.assertIn("def own_ref(", source)
        self.assertIn("ref = own_ref(repo)", source)
        # Тянуть общий репозиторий программы нельзя: копия слилась бы с ним.
        self.assertNotIn('["pull", "--ff-only", remote, "master"]', source)


if __name__ == "__main__":
    unittest.main()
