"""Тесты обновления программы из окна (заказ владельца 24.11).

Заказ: «как мне запустить процесс обновления из графического интерфейса
программы? Сделай процесс обновления наглядным».

Проверяем то, что может навредить:

- **обновление не затирает чужую работу**: незакоммиченные правки в
  исходниках ⇒ обновление останавливается и говорит почему; а *новые*
  файлы (которых обновление не трогает) — не повод останавливаться
  (живой случай 25.11: служебные файлы агента блокировали кнопку
  «Обновить программу»);
- **версии сравниваются честно**: что стоит на диске, что есть в
  репозитории, сколько коммитов новых;
- **исходники берутся правильные**: тот репозиторий, который записан в
  настройках (а не «где попало»);
- **журнал шагов** — по нему окно показывает процесс.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_update as update  # noqa: E402


class UpdateTest(unittest.TestCase):
    """Обновление программы: путь, версии, отказ при чужой работе."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self._old_local = update.LOCAL
        self._old_git = update._git
        self._old_stream = update._stream
        self._old_cache = dict(update._cache)
        self._old_log = update.LOG_FILE
        update.LOCAL = Path(self.tmp.name)
        # ⚠️ Живой случай 24.11: `LOG_FILE` — константа модуля, и тесты писали
        # в **настоящий** журнал программы: владелец видел в шагах обновления
        # «исходники: C:\...\Temp\tmpXXXX\repo» и ошибки тестов. Свой файл.
        update.LOG_FILE = Path(self.tmp.name) / "update.log"
        update._cache = {"at": 0.0, "value": {}}
        with update._lock:
            update._steps.clear()
            update._last.clear()
            update._running = False
        (update.LOCAL / "settings.json").write_text(
            json.dumps({"codeRepo": str(self.repo)}), encoding="utf-8")

    def tearDown(self) -> None:
        update.LOCAL = self._old_local
        update._git = self._old_git
        update._stream = self._old_stream
        update._cache = self._old_cache
        update.LOG_FILE = self._old_log
        self.tmp.cleanup()

    # ── исходники ──────────────────────────────────────────────────────
    def test_repo_taken_from_settings(self) -> None:
        self.assertEqual(update.repo_dir(), self.repo)

    def test_repo_falls_back_to_clone(self) -> None:
        (update.LOCAL / "settings.json").unlink()
        clone = Path(self.tmp.name) / "clone"
        (clone / ".git").mkdir(parents=True)
        import minigen_clone
        old = minigen_clone.CLONE_DIR
        minigen_clone.CLONE_DIR = clone
        try:
            self.assertEqual(update.repo_dir(), clone)
        finally:
            minigen_clone.CLONE_DIR = old

    # ── версии ─────────────────────────────────────────────────────────
    def test_available_reads_commit_and_distance(self) -> None:
        def fake_git(args, cwd, timeout=120):
            if args[:2] == ["rev-parse", "--short"]:
                return 0, "ccccccc", ""
            if args[:2] == ["rev-list", "--count"]:
                return 0, "4", ""
            if args and args[0] == "remote":
                return 0, "origin", ""
            return 0, "", ""
        update._git = fake_git
        info = update.available()
        self.assertEqual(info["commit"], "ccccccc")
        self.assertEqual(info["behind"], 4)
        self.assertEqual(info["remote"], "origin")

    def test_check_says_up_to_date(self) -> None:
        def fake_git(args, cwd, timeout=120):
            if args[:2] == ["rev-parse", "--short"]:
                return 0, "aaaaaaa", ""
            if args[:2] == ["rev-list", "--count"]:
                return 0, "0", ""
            if args and args[0] == "remote":
                return 0, "origin", ""
            return 0, "", ""
        update._git = fake_git
        import minigen_state as state
        old = state.program_version
        state.program_version = lambda: {"commit": "aaaaaaa", "installed_at": 1,
                                         "source": "C:/repo", "installed": True,
                                         "running_from": "C:/МиниГен"}
        try:
            result = update.check()
        finally:
            state.program_version = old
        self.assertTrue(result["ok"])
        self.assertTrue(result["up_to_date"])

    # ── отказ при чужой работе ─────────────────────────────────────────
    def test_update_stops_on_dirty_sources(self) -> None:
        """Незакоммиченные правки ⇒ обновление не идёт и говорит почему."""
        def fake_git(args, cwd, timeout=120):
            if args and args[0] == "status":
                return 0, " M tools/minigen-gui/src/main/index.ts", ""
            if args and args[0] == "remote":
                return 0, "origin", ""
            return 0, "aaaaaaa", ""
        update._git = fake_git
        update._stream = lambda args, cwd, step: 0
        update._run()
        with update._lock:
            last = dict(update._last)
            steps = list(update._steps)
        self.assertFalse(last.get("ok"))
        self.assertIn("незакоммиченные", last.get("why", ""))
        self.assertTrue(any(step["status"] == "error" for step in steps))

    def test_update_ignores_untracked_files(self) -> None:
        """Новые незакоммиченные файлы ⇒ обновление НЕ останавливается.

        Живой случай 25.11: в корне исходников лежали служебные файлы агента
        (тела PR и тексты коммитов). `git status --porcelain` показывает их как
        `??`, и обновление из окна останавливалось с «в исходниках есть
        незакоммиченные правки» — владелец не мог обновить программу одной
        кнопкой из-за файлов, которых обновление не трогает.
        """
        seen: list[list[str]] = []

        def fake_git(args, cwd, timeout=120):
            if args and args[0] == "status":
                seen.append(list(args))
                # Так ведёт себя настоящий git: с `--untracked-files=no` новых
                # файлов в ответе нет, без флага — есть.
                if "--untracked-files=no" in args:
                    return 0, "", ""
                return 0, "?? .pr-body.md", ""
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return 0, "master", ""
            if args and args[0] == "remote":
                return 0, "origin", ""
            return 0, "aaaaaaa", ""

        update._git = fake_git
        update._stream = lambda args, cwd, step: 0
        update._run()
        self.assertTrue(seen, "обновление не спросило состояние исходников")
        self.assertIn("--untracked-files=no", seen[0])
        with update._lock:
            last = dict(update._last)
        # Чужие новые файлы — не повод останавливаться: дальше обновление
        # упирается в отсутствие node_modules (тестовый репозиторий).
        self.assertNotIn("незакоммиченные", last.get("why", ""))

    def test_update_stops_without_node_modules(self) -> None:
        def fake_git(args, cwd, timeout=120):
            if args and args[0] == "status":
                return 0, "", ""
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return 0, "master", ""
            if args and args[0] == "remote":
                return 0, "origin", ""
            return 0, "aaaaaaa", ""
        update._git = fake_git
        update._stream = lambda args, cwd, step: 0
        update._run()
        with update._lock:
            last = dict(update._last)
        self.assertFalse(last.get("ok"))
        self.assertIn("node_modules", last.get("why", ""))

    def test_update_stops_on_a_branch(self) -> None:
        """Исходники на ветке ⇒ обновление не идёт: окно собирается из master.

        Живой случай 24.11: в дереве была ветка правки, сборка взяла метку из
        неё, и окно потом вечно спорило с «версией на месте» — перезапуск не
        помогал, потому что перезапускался тот же бандл.
        """
        def fake_git(args, cwd, timeout=120):
            if args and args[0] == "status":
                return 0, "", ""
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return 0, "agent/minigen-fix", ""
            if args and args[0] == "remote":
                return 0, "origin", ""
            return 0, "aaaaaaa", ""
        update._git = fake_git
        update._stream = lambda args, cwd, step: 0
        update._run()
        with update._lock:
            last = dict(update._last)
        self.assertFalse(last.get("ok"))
        why = last.get("why", "")
        self.assertIn("ветке", why)
        self.assertIn("master", why)

    # ── журнал и статус ────────────────────────────────────────────────
    def test_log_records_steps(self) -> None:
        update._log("build", "собираю окно")
        update._log("build", "окно собрано", "ok")
        with update._lock:
            steps = list(update._steps)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["status"], "info")
        self.assertEqual(steps[1]["status"], "ok")
        self.assertTrue(update.LOG_FILE.is_file())

    def test_status_has_shape_for_the_window(self) -> None:
        update._log("repo", "исходники")
        result = update.status()
        for key in ("running", "steps", "installed", "available", "repo"):
            self.assertIn(key, result)
        self.assertFalse(result["running"])

    def test_steps_survive_a_window_restart(self) -> None:
        """После перезапуска окна шаги берутся из журнала на диске.

        Живой случай 24.11: окно перезапустили, а во вкладке было «обновление
        ещё не запускали — журнал пуст», хотя обновление шло и журнал лежал.
        """
        update._log("build", "собираю окно")
        update._log("build", "окно собрано", "ok")
        with update._lock:
            update._steps.clear()
        result = update.status()
        texts = [step.get("text") for step in result["steps"]]
        self.assertIn("окно собрано", texts)

    def test_npm_command_goes_through_cmd_on_windows(self) -> None:
        """npm на Windows — это .cmd: его надо звать через cmd.exe."""
        command = update._npm(["run", "build"])
        if os.name == "nt":
            self.assertIn("/c", command)
            self.assertTrue(any("npm" in part.lower() for part in command))
        else:
            self.assertTrue(any("npm" in part.lower() for part in command))


if __name__ == "__main__":
    unittest.main()
