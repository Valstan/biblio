r"""Тест счётчика активности области и удаления агента (промт КАРМАНА #208).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Проверяем ровно то, что обещал промт:

1. **счётчик активности** — у области в состоянии есть поле активности, и оно
   заполняется по данным автомата аккаунтов (по журналу), а не на глаз;
2. **удаление агента**:
   - агент с неотправленным коммитом → удаление **отклонено с объяснением**;
   - агент с полностью отправленным → удалено **всё**, следов не осталось.

⚠️ Почему изолированно: удаление необратимо — тест работает на временных
конфиге, журнале, папках и **поддельном рабочем столе**, чтобы не тронуть
живую машину владельца.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_delete as delete_mod   # noqa: E402
import minigen_state as state         # noqa: E402


def git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (proc.stdout or "").strip()


class ActivityCounterTest(unittest.TestCase):
    """Счётчик активности области — по журналу автомата аккаунтов."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.core = state.load_daemons()["core"]
        self._old = (self.core.CONFIG, self.core.JOURNAL, self.core.BIN)
        self.core.CONFIG = self.root / "areas.json"
        self.core.JOURNAL = self.root / "journal.log"
        self.core.BIN = self.root / "bin"
        self.core.BIN.mkdir()
        self.areas = [
            {"slug": "memory", "title": "Память", "folder": "", "karman": "memory"},
            {"slug": "kompik", "title": "Компик", "folder": "", "karman": "kompik"},
        ]
        self.core.CONFIG.write_text(json.dumps({
            "projects": [
                {"slug": "memory", "title": "Память", "folder": "", "karman": "memory"},
                {"slug": "kompik", "title": "Компик", "folder": "", "karman": "kompik"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        state._ACTIVITY_CACHE["key"] = None
        state._ACTIVITY_CACHE["data"] = {}

    def tearDown(self) -> None:
        self.core.CONFIG, self.core.JOURNAL, self.core.BIN = self._old
        state._ACTIVITY_CACHE["key"] = None
        state._ACTIVITY_CACHE["data"] = {}
        self.tmp.cleanup()

    def _write_journal(self, lines: list[str]) -> None:
        self.core.JOURNAL.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_counter_counts_grants_and_updates(self) -> None:
        self._write_journal([
            "[2026-11-28 17:05:13] Память: Карман отдал: id=157 email=a@b tier=points_1000 why=x (проект memory)",
            "[2026-11-28 17:05:16] Память: аккаунт получен (аккаунт a@b, токен жив ещё 720 мин, вендер ответил 200, прока: ok)",
            "[2026-11-28 18:51:43] Компик: Карман отдал: id=158 email=c@d tier=points_1000 why=y (проект kompik)",
        ])
        counters = state.activity_counters({"core": self.core}, self.areas)
        self.assertEqual(counters["memory"]["granted"], 1)
        self.assertEqual(counters["memory"]["updated"], 1)
        self.assertEqual(counters["memory"]["total"], 2)
        self.assertEqual(counters["kompik"]["granted"], 1)
        self.assertEqual(counters["kompik"]["updated"], 0)
        self.assertEqual(counters["kompik"]["total"], 1)

    def test_rejected_account_is_not_a_grant(self) -> None:
        # «Карман отдал негодный токен — пробую следующий» — это отказ, а не
        # выдача: в счётчик выдач он не идёт.
        self._write_journal([
            "[2026-11-28 17:05:13] Память: Карман отдал негодный токен — пробую следующий (попытка 1 из 3)",
        ])
        counters = state.activity_counters({"core": self.core}, self.areas)
        self.assertEqual(counters["memory"]["granted"], 0)
        self.assertEqual(counters["memory"]["total"], 0)

    def test_area_state_carries_activity(self) -> None:
        self._write_journal([
            "[2026-11-28 17:05:13] Память: Карман отдал: id=1 email=a@b tier=t why=x (проект memory)",
        ])
        items = state.areas_state({"core": self.core}, include_sessions=False)
        by_slug = {item["slug"]: item for item in items}
        self.assertIn("activity", by_slug["memory"])
        self.assertEqual(by_slug["memory"]["activity"]["total"], 1)
        # у области без событий — ноль, а не «нет поля»
        self.assertEqual(by_slug["kompik"]["activity"]["total"], 0)


class DeleteAgentTest(unittest.TestCase):
    """Удаление агента: проверка синхронизации с GitHub и полная чистка."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.core = state.load_daemons()["core"]
        self._old = (self.core.CONFIG, self.core.JOURNAL, self.core.BIN,
                     state.AREAS_ROOT, delete_mod.REGISTRY_HOME,
                     delete_mod.LOCAL, delete_mod.PASSPORTS_HOME)
        self.core.CONFIG = self.root / "areas.json"
        self.core.JOURNAL = self.root / "journal.log"
        self.core.JOURNAL.write_text("", encoding="utf-8")
        self.core.BIN = self.root / "bin"
        self.core.BIN.mkdir()
        state.AREAS_ROOT = self.root / "areas"
        state.AREAS_ROOT.mkdir()
        delete_mod.LOCAL = self.root / "program"
        delete_mod.PASSPORTS_HOME = self.root / "program" / "passports"
        delete_mod.REGISTRY_HOME = self.root / "program" / "agents"
        (delete_mod.PASSPORTS_HOME).mkdir(parents=True)
        (delete_mod.REGISTRY_HOME / "minigen-agents" / "agents").mkdir(parents=True)
        # Реестр агентов берёт свой путь из своего модуля. ⚠️ `bridge/tests/
        # __init__.py` подменяет `registry_dir` на общий тестовый реестр —
        # значит, переопределять надо именно его (иначе тест удалил бы агента
        # из НАСТОЯЩЕГО реестра на машине владельца).
        import minigen_agents
        self._old_registry_dir = minigen_agents.registry_dir
        minigen_agents.registry_dir = (
            lambda: delete_mod.REGISTRY_HOME / "minigen-agents")
        # поддельный рабочий стол: ярлыки удалять нельзя настоящие
        self.desktop = self.root / "Desktop"
        self.desktop.mkdir()
        self._old_desktop = delete_mod._desktop_dir
        delete_mod._desktop_dir = lambda: self.desktop
        self._old_log = self.core.log
        self.core.log = lambda msg: None
        # без лечащего: процессы не трогаем (в тесте их и нет)
        self.mods = {"core": self.core}

    def tearDown(self) -> None:
        (self.core.CONFIG, self.core.JOURNAL, self.core.BIN,
         state.AREAS_ROOT, delete_mod.REGISTRY_HOME,
         delete_mod.LOCAL, delete_mod.PASSPORTS_HOME) = self._old
        import minigen_agents
        minigen_agents.registry_dir = self._old_registry_dir
        delete_mod._desktop_dir = self._old_desktop
        self.core.log = self._old_log
        self.tmp.cleanup()

    # ── помощники ────────────────────────────────────────────────────────────

    def _repo(self, name: str, push: bool = True) -> Path:
        """Репозиторий агента (с origin, чтобы было что проверять)."""
        origin = self.root / ("%s-origin.git" % name)
        git(["init", "--bare", str(origin)], self.root)
        work = self.root / name
        work.mkdir()
        git(["init"], work)
        # ⚠️ На машине владельца глобальный `core.hooksPath` запрещает пуш в
        # master (ADR-0002) — тест от него отвязывается, чтобы проверять
        # «отправлено / не отправлено», а не канон пуша.
        hooks = self.root / "empty-hooks"
        hooks.mkdir(exist_ok=True)
        git(["config", "core.hooksPath", str(hooks)], work)
        git(["symbolic-ref", "HEAD", "refs/heads/master"], work)
        git(["config", "user.email", "test@example.com"], work)
        git(["config", "user.name", "Тест"], work)
        (work / "README.md").write_text("привет\n", encoding="utf-8")
        git(["add", "."], work)
        git(["commit", "-m", "первый"], work)
        git(["remote", "add", "origin", str(origin)], work)
        if push:
            git(["push", "-u", "origin", "master"], work)
        return work

    def _write_config(self, slug: str, folder: str) -> None:
        self.core.CONFIG.write_text(json.dumps({
            "projects": [
                {"slug": slug, "title": "Орка", "folder": folder, "karman": slug},
                {"slug": "other", "title": "Другой", "folder": "", "karman": "other"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def _make_traces(self, slug: str) -> None:
        """Следы агента на диске: папка агента, паспорта, каталог, ярлыки."""
        agent = delete_mod.REGISTRY_HOME / "minigen-agents" / "agents" / slug
        agent.mkdir(parents=True)
        (agent / "agent.yaml").write_text("title: Орка\n", encoding="utf-8")
        passports = delete_mod.PASSPORTS_HOME / slug
        passports.mkdir(parents=True)
        (passports / "s-a.json").write_text("{}", encoding="utf-8")
        area_dir = state.AREAS_ROOT / ("p-" + slug)
        area_dir.mkdir(parents=True)
        (area_dir / "auth.json").write_text('{"records": {}}', encoding="utf-8")
        (self.desktop / "Орка — MiniMax.lnk").write_text("x", encoding="utf-8")
        (self.desktop / "Орка — MiniMax (стоп).lnk").write_text("x", encoding="utf-8")

    def _area_dir(self, slug: str) -> str:
        """Каталог данных области (его отдаёт ядро: `core.areas()`)."""
        return str(state.AREAS_ROOT / ("p-" + slug))

    # ── проверки ─────────────────────────────────────────────────────────────

    def test_unpushed_commit_blocks_deletion(self) -> None:
        work = self._repo("orca", push=True)
        # новый коммит, который не отправлен
        (work / "новый.txt").write_text("не отправлено\n", encoding="utf-8")
        git(["add", "."], work)
        git(["commit", "-m", "не отправлен"], work)
        self._write_config("orca", str(work))
        self._make_traces("orca")

        sync = delete_mod.git_sync_state(work)
        self.assertFalse(sync["ok"], "неотправленный коммит должен мешать")
        self.assertTrue(sync["unpushed"], sync)

        result = delete_mod.delete_agent(self.core, self.mods, "orca", "Орка", str(work), self._area_dir("orca"))
        self.assertFalse(result["ok"], "удаление должно быть отклонено")
        self.assertIn("не всё отправлено", result.get("error", ""))
        # и ничего не удалено
        self.assertTrue(work.is_dir())
        self.assertTrue((delete_mod.PASSPORTS_HOME / "orca").is_dir())
        data = json.loads(self.core.CONFIG.read_text(encoding="utf-8"))
        self.assertIn("orca", [p["slug"] for p in data["projects"]])

    def test_uncommitted_changes_block_deletion(self) -> None:
        work = self._repo("orca", push=True)
        (work / "README.md").write_text("правка не закоммичена\n", encoding="utf-8")
        self._write_config("orca", str(work))
        self._make_traces("orca")

        sync = delete_mod.git_sync_state(work)
        self.assertFalse(sync["ok"])
        self.assertTrue(sync["dirty"], sync)

        result = delete_mod.delete_agent(self.core, self.mods, "orca", "Орка", str(work), self._area_dir("orca"))
        self.assertFalse(result["ok"])
        self.assertTrue(work.is_dir())

    def test_fully_pushed_agent_is_removed_completely(self) -> None:
        work = self._repo("orca", push=True)
        self._write_config("orca", str(work))
        self._make_traces("orca")

        sync = delete_mod.git_sync_state(work)
        self.assertTrue(sync["ok"], sync)

        result = delete_mod.delete_agent(self.core, self.mods, "orca", "Орка", str(work), self._area_dir("orca"))
        self.assertTrue(result["ok"], result)
        self.assertFalse(result.get("failed"), result)

        # 1) рабочей папки проекта нет
        self.assertFalse(work.exists())
        # 2) записи в настройке областей нет (а чужая — на месте)
        data = json.loads(self.core.CONFIG.read_text(encoding="utf-8"))
        self.assertNotIn("orca", [p["slug"] for p in data["projects"]])
        self.assertIn("other", [p["slug"] for p in data["projects"]])
        # 3) папки агента нет
        self.assertFalse((delete_mod.REGISTRY_HOME / "minigen-agents" / "agents" / "orca").exists())
        # 4) паспортов ниток нет
        self.assertFalse((delete_mod.PASSPORTS_HOME / "orca").exists())
        # 5) каталога области (авторизации) нет
        self.assertFalse((state.AREAS_ROOT / "p-orca").exists())
        # 6) ярлыков нет
        self.assertFalse((self.desktop / "Орка — MiniMax.lnk").exists())
        self.assertFalse((self.desktop / "Орка — MiniMax (стоп).lnk").exists())

    def test_plan_shows_what_will_be_deleted(self) -> None:
        work = self._repo("orca", push=True)
        self._write_config("orca", str(work))
        self._make_traces("orca")
        plan = delete_mod.collect("orca", "Орка", str(work), self._area_dir("orca"))
        whats = [target["what"] for target in plan["targets"]]
        self.assertIn("рабочая папка проекта", whats)
        self.assertIn("паспорта ниток области", whats)
        self.assertIn("папка агента (конституция и описание)", whats)
        self.assertIn("каталог области (авторизация)", whats)
        self.assertTrue(any("ярлык" in what for what in whats))
        # план ничего не удаляет
        self.assertTrue(work.is_dir())
        self.assertTrue((delete_mod.PASSPORTS_HOME / "orca").is_dir())


if __name__ == "__main__":
    unittest.main()
