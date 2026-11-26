"""Тесты инструкции по установке «МиниГена» (заказ владельца 26.11).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем:

1. **Инструкция находится сама** — программа отдаёт окну текст, путь к файлу
   и путь к установщику: владельцу не нужно искать по диску, что отправить
   знакомому.
2. **Инструкции нет — честный отказ с подсказкой**, что делать (а не пустота).
3. **В установленной программе инструкция есть** — установщик копирует файл
   в папку программы, иначе вкладка «Черенки» сказала бы «инструкции нет».

⚠️ Настройки машины не трогаем: путь к исходникам подменяется на время тестов.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BRIDGE_DIR.parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_install as install   # noqa: E402


class GuideTest(unittest.TestCase):
    """Инструкция по установке — то, что владелец отправит знакомому."""

    def test_the_guide_is_found_in_the_sources(self) -> None:
        guide = install.read_guide()
        self.assertTrue(guide["ok"], guide.get("error"))
        self.assertIn("Установка", guide["title"])
        self.assertIn("Node.js", guide["text"])
        self.assertTrue(guide["path"].endswith("INSTALL.md"), guide["path"])

    def test_the_installer_is_next_to_the_guide(self) -> None:
        guide = install.read_guide()
        self.assertTrue(guide["installer"].endswith("install.ps1"),
                        guide["installer"])
        self.assertTrue(guide["installer_dir"], "папка установщика должна быть")

    def test_the_repo_is_named(self) -> None:
        guide = install.read_guide()
        self.assertTrue(guide["repo"], "адрес репозитория нужен знакомому")
        self.assertIn("biblio", guide["repo"])

    def test_a_missing_guide_is_an_honest_refusal(self) -> None:
        """Нет инструкции — говорим об этом и говорим, что делать."""
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp)
            original_dir = install.PROGRAM_DIR
            original_read = install.state.read_settings
            install.PROGRAM_DIR = empty
            install.state.read_settings = lambda: {"codeRepo": str(empty)}
            try:
                guide = install.read_guide()
            finally:
                install.PROGRAM_DIR = original_dir
                install.state.read_settings = original_read
        self.assertFalse(guide["ok"])
        self.assertIn("не найдена", guide["error"])
        self.assertIn("что делать", guide["why"].lower())

    def test_the_program_folder_is_a_fallback(self) -> None:
        """Папка программы — второй источник (установленная копия)."""
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp)
            (program / "docs").mkdir(parents=True)
            (program / "docs" / "INSTALL.md").write_text(
                "# Установка «МиниГена»\n\nтест\n", encoding="utf-8")
            (program / "install.ps1").write_text("Write-Host ok\n", encoding="utf-8")
            original_dir = install.PROGRAM_DIR
            original_read = install.state.read_settings
            install.PROGRAM_DIR = program
            install.state.read_settings = lambda: {}
            try:
                guide = install.read_guide()
            finally:
                install.PROGRAM_DIR = original_dir
                install.state.read_settings = original_read
        self.assertTrue(guide["ok"], guide.get("error"))
        self.assertEqual(guide["source"], "папка программы")
        self.assertTrue(guide["installer"].endswith("install.ps1"))

    def test_the_installer_copies_the_guide_to_the_program(self) -> None:
        """Установщик кладёт инструкцию в папку программы — иначе её нет."""
        script = (BRIDGE_DIR.parent / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("INSTALL.md", script)
        self.assertIn('docs\\INSTALL.md', script)


if __name__ == "__main__":
    unittest.main()
