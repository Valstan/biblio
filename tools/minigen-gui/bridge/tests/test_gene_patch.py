"""Тесты патча гена и пути приёмки (24.11, живые находки).

Живой прогон цикла «копия → ген → пул → внедрение» вскрыл два дефекта:

1. **патч гена был битый**: diff брался в текстовом режиме, где срезается
   финальный перевод строки, — git apply такой патч считает «corrupt patch»
   и внедрение не работало;
2. **приёмка панели всегда красная**: путь к ней устарел (файл переехал в
   `archive/`), а правило «красные проверки — не PR» из-за этого блокировало
   встроенной нейросети открывать PR.

Проверяем:

- патч гена **применяется** (git apply --check) — на настоящем репозитории;
- патч сохраняется **байтами** (финальный перевод строки на месте);
- путь приёмки панели **существует**.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
REPO = BRIDGE_DIR.parent.parent.parent      # корень репозитория
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_code as code   # noqa: E402
import minigen_genes as genes  # noqa: E402


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, timeout=120)


class PatchBytesTest(unittest.TestCase):
    """Патч гена должен быть применимым — это и есть смысл гена."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "test@local"], self.repo)
        _git(["config", "user.name", "test"], self.repo)
        (self.repo / "a.txt").write_text("строка 1\nстрока 2\n", encoding="utf-8")
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "первый"], self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_diff_bytes_keep_the_final_newline(self) -> None:
        (self.repo / "a.txt").write_text("строка 1\nстрока 2\nстрока 3\n", encoding="utf-8")
        diff = genes._git_bytes(["diff", "HEAD"], self.repo)
        self.assertTrue(diff)
        self.assertTrue(diff.endswith(b"\n"), "финальный перевод строки обязан быть")

    def test_published_patch_applies(self) -> None:
        (self.repo / "a.txt").write_text("строка 1\nстрока 2\nстрока 3\n", encoding="utf-8")
        diff = genes._git_bytes(["diff", "HEAD"], self.repo)
        patch = Path(self.tmp.name) / "p.diff"
        patch.write_bytes(diff)
        # проверка применения в чистой копии того же состояния
        clone = Path(self.tmp.name) / "clone"
        _git(["clone", "-q", str(self.repo), str(clone)], Path(self.tmp.name))
        check = subprocess.run(["git", "-C", str(clone), "apply", "--check", str(patch)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(check.returncode, 0,
                         "патч не применился: %s" % ((check.stdout or "") + (check.stderr or "")))


class AcceptancePathTest(unittest.TestCase):
    """Приёмка панели лежит в archive/ — и код проверок это знает."""

    def test_panel_acceptance_exists(self) -> None:
        panel = REPO / "tools" / "minimax-project-areas" / "archive" / "check_panel.py"
        self.assertTrue(panel.is_file(), "нет приёмки панели: %s" % panel)

    def test_run_tests_points_to_an_existing_acceptance(self) -> None:
        source = (BRIDGE_DIR / "minigen_code.py").read_text(encoding="utf-8")
        self.assertIn('archive" / "check_panel.py', source,
                      "run_tests должен указывать на существующую приёмку панели")


if __name__ == "__main__":
    unittest.main()
