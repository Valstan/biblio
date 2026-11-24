"""Тесты формата заявки (чистый модуль — без сети и без моделей).

Запуск:  python -m unittest discover -s bridge/tests -t . -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_ingest as ingest      # noqa: E402


class RenderTest(unittest.TestCase):
    """Заявка — один текст с фиксированным порядком разделов."""

    def test_sections_in_canonical_order(self) -> None:
        draft = {
            "title": "Сарафан: импорт контактов",
            "project": "sarafan",
            "summary": "Импортировать контакты из Telegram.",
            "Что сделать": "1. Взять выгрузку.\n2. Свести по телефону.",
            "Контекст": "Репозиторий sarafan, модуль contacts.",
            "План": "1. Импортер.\n2. Тест.",
            "Критерии приёмки": "- [ ] 100 контактов загружены",
            "Риски и открытые вопросы": "Дубли в источнике.",
            "Источник": "зачитка 24.11.2026",
        }
        text = ingest.render_request(draft)
        head = text.splitlines()
        self.assertEqual(head[0], "# Сарафан: импорт контактов")
        self.assertIn("**Проект:** sarafan", text)
        self.assertIn("**Суть:** Импортировать контакты из Telegram.", text)
        order = [line for line in head if line.startswith("## ")]
        self.assertEqual(order, ["## Что сделать", "## Контекст", "## План",
                                 "## Критерии приёмки", "## Риски и открытые вопросы",
                                 "## Источник"])

    def test_empty_sections_are_skipped(self) -> None:
        text = ingest.render_request({"title": "Пусто", "План": "шаг"})
        self.assertNotIn("## Контекст", text)
        self.assertIn("## План", text)

    def test_render_parse_round_trip(self) -> None:
        draft = {
            "title": "Карман: новая комната",
            "project": "karman",
            "summary": "Завести комнату.",
            "Что сделать": "Создать комнату и выпустить токен.",
            "План": "1. Комната.\n2. Токен.",
            "Критерии приёмки": "- [ ] раздел читается",
        }
        parsed = ingest.parse_request(ingest.render_request(draft))
        self.assertEqual(parsed["title"], "Карман: новая комната")
        self.assertEqual(parsed["проект"], "karman")
        self.assertEqual(parsed["план"], draft["План"])
        self.assertEqual(parsed["критерии приёмки"], draft["Критерии приёмки"])


class MissingTest(unittest.TestCase):
    """Заявка без плана или критериев — не заявка."""

    def test_missing_sections(self) -> None:
        self.assertEqual(
            ingest.missing_sections({"Что сделать": "что-то"}),
            ["план", "критерии приёмки"])
        self.assertEqual(ingest.missing_sections({
            "Что сделать": "что-то", "План": "шаг", "Критерии приёмки": "- [ ] ок"}), [])


class ProjectGuessTest(unittest.TestCase):
    """К какому проекту относится заявка — и когда нужен новый."""

    projects = [
        {"slug": "sarafan", "title": "Сарафан"},
        {"slug": "karman", "title": "Карман", "keywords": ["комната", "токен"]},
        {"slug": "biblio", "title": "biblio", "keywords": ["миниген", "демон"]},
    ]

    def test_known_project(self) -> None:
        guess = ingest.guess_project(
            "Сделать импорт контактов в Сарафан и проверить выгрузку", self.projects)
        self.assertTrue(guess["known"])
        self.assertEqual(guess["slug"], "sarafan")

    def test_unknown_project(self) -> None:
        guess = ingest.guess_project(
            "Придумать, как кормить кур в загоне, и купить корм", self.projects)
        self.assertFalse(guess["known"])
        self.assertEqual(guess["slug"], "")

    def test_keywords_count(self) -> None:
        guess = ingest.guess_project(
            "Выпустить токен в новую комнату", self.projects)
        self.assertTrue(guess["known"])
        self.assertEqual(guess["slug"], "karman")


class SlugTest(unittest.TestCase):
    def test_slugify_is_latin(self) -> None:
        self.assertEqual(ingest.slugify("Сарафан 2: импорт"), "sarafan-2-import")
        self.assertEqual(ingest.slugify(""), "idea")
        self.assertEqual(ingest.slugify("!!!"), "idea")

    def test_filename(self) -> None:
        name = ingest.request_filename({"title": "Сарафан: импорт"}, "2026-11-24")
        self.assertEqual(name, "2026-11-24-sarafan-import.md")


if __name__ == "__main__":
    unittest.main()
