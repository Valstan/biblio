"""Тесты агентов «МиниГена»: реестр, конституция, скиллы, заявки.

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем (и почему):

- **реестр**: манифест пишется и читается, список агентов собирается из
  реестра (он — источник правды: по нему агента восстанавливают на другой
  машине);
- **шаблон агента**: конституция и скилл копируются в репозиторий агента —
  без них агент не «специализированный», а просто ещё одна папка;
- **скиллы ставятся в область**: рантайм берёт их из `<область>/skills/`,
  а не из репозитория (иначе агент не увидит своих умений);
- **заявка переживает переустановку**: лежит в репозитории агента и находится
  после этого по списку;
- **чужие репозитории читаются, но не пишутся**: путь-обход отбит, файлы с
  ключами закрыты.

Всё это — без сети: GitHub в тестах не трогаем (урок 22.11: две копии
одного агента — брак).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_agents as agents   # noqa: E402
import minigen_ingest as ingest   # noqa: E402
import minigen_repos as repos     # noqa: E402


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git"] + args, cwd=str(cwd), check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class RegistryTest(unittest.TestCase):
    """Реестр агентов — манифесты."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Path(self.tmp.name) / "minigen-agents"
        self.registry.mkdir(parents=True, exist_ok=True)
        self._old = agents.registry_dir
        agents.registry_dir = lambda: self.registry   # type: ignore[assignment]

    def tearDown(self) -> None:
        agents.registry_dir = self._old   # type: ignore[assignment]
        self.tmp.cleanup()

    def test_manifest_round_trip(self) -> None:
        manifest = {"slug": "ideas", "title": "Идеи", "repo": "Valstan/ideas",
                    "area": "p-ideas", "skills": ["ideas-ingest"]}
        written = agents.write_manifest("ideas", manifest)
        self.assertTrue(written.get("ok"), written)
        self.assertEqual(agents.read_manifest("ideas")["repo"], "Valstan/ideas")

    def test_missing_manifest_is_empty(self) -> None:
        self.assertEqual(agents.read_manifest("нет-такого"), {})

    def test_list_agents_uses_manifests(self) -> None:
        agents.write_manifest("ideas", {"slug": "ideas", "title": "Идеи"})
        agents.write_manifest("sarafan", {"slug": "sarafan", "title": "Сарафан"})
        listed = agents.list_agents()
        self.assertEqual([a["slug"] for a in listed], ["ideas", "sarafan"])

    def test_manifest_is_yaml(self) -> None:
        agents.write_manifest("ideas", {"slug": "ideas", "title": "Идеи «по-русски»"})
        text = (self.registry / "agents" / "ideas" / "agent.yaml").read_text(
            encoding="utf-8")
        self.assertIn("Идеи «по-русски»", text)


class TemplateTest(unittest.TestCase):
    """Шаблон агента: конституция и скиллы."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "agent-repo"
        self.repo.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ideas_template_has_constitution_and_skill(self) -> None:
        copied = agents.copy_template("ideas", self.repo)
        self.assertIn("AGENTS.md", copied)
        self.assertIn("skills/ideas-ingest/SKILL.md", copied)
        constitution = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Идеи", constitution)

    def test_skill_has_front_matter(self) -> None:
        agents.copy_template("ideas", self.repo)
        skill = (self.repo / "skills" / "ideas-ingest" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertTrue(skill.startswith("---"), "у скилла нет front matter")
        self.assertIn("name: ideas-ingest", skill)

    def test_unknown_template_falls_back_to_default(self) -> None:
        copied = agents.copy_template("нет-такого", self.repo)
        self.assertIn("AGENTS.md", copied)

    def test_skills_of_finds_skill_dirs(self) -> None:
        agents.copy_template("ideas", self.repo)
        self.assertEqual(agents.skills_of(self.repo), ["ideas-ingest"])

    def test_install_skills_into_area(self) -> None:
        agents.copy_template("ideas", self.repo)
        area = Path(self.tmp.name) / "area" / "skills"
        old = agents.area_skills_dir
        agents.area_skills_dir = lambda slug: area   # type: ignore[assignment]
        try:
            result = agents.install_skills("ideas", self.repo)
        finally:
            agents.area_skills_dir = old   # type: ignore[assignment]
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("installed"), ["ideas-ingest"])
        self.assertTrue((area / "ideas-ingest" / "SKILL.md").is_file())


class WorkRepoTest(unittest.TestCase):
    """Рабочий репозиторий агента: ветка `master` и remote."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ideas"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_repo_is_on_master(self) -> None:
        """`git init` может дать `main` — тогда клон придёт пустым."""
        result = agents.init_work_repo(self.path, "Valstan/ideas")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("branch"), "master")
        # Коммита ещё нет (ветка «не родилась»), поэтому HEAD читаем
        # как символическую ссылку, а не как ревизию.
        branch = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"],
                                cwd=str(self.path), capture_output=True, text=True,
                                encoding="utf-8", check=True).stdout.strip()
        self.assertEqual(branch, "master")
        remote = subprocess.run(["git", "remote", "get-url", "origin"],
                                cwd=str(self.path), capture_output=True, text=True,
                                encoding="utf-8", check=True).stdout.strip()
        self.assertEqual(remote, "https://github.com/Valstan/ideas.git")


class RequestTest(unittest.TestCase):
    """Заявки агента: лежат в репозитории и находятся после этого."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workspace = root / "workspace"
        self.repo = self.workspace / "ideas"
        self.repo.mkdir(parents=True, exist_ok=True)
        _git(["init"], self.repo)
        _git(["config", "user.email", "test@example.com"], self.repo)
        _git(["config", "user.name", "Test"], self.repo)
        self.registry = root / "registry"
        self.registry.mkdir(parents=True, exist_ok=True)
        agents.write_manifest("ideas", {"slug": "ideas", "repo": "Valstan/ideas"})
        self._old_dir = agents.registry_dir
        self._old_home = agents.workspace_home
        agents.registry_dir = lambda: self.registry     # type: ignore[assignment]
        agents.workspace_home = lambda: self.workspace  # type: ignore[assignment]

    def tearDown(self) -> None:
        agents.registry_dir = self._old_dir    # type: ignore[assignment]
        agents.workspace_home = self._old_home  # type: ignore[assignment]
        self.tmp.cleanup()

    def test_save_request_writes_file(self) -> None:
        text = ingest.render_request({
            "title": "Сарафан: импорт контактов",
            "project": "sarafan",
            "summary": "Импортировать контакты.",
            "Что сделать": "1. Взять выгрузку.",
            "План": "1. Импортер.",
            "Критерии приёмки": "- [ ] тест проходит",
        })
        saved = agents.save_request("ideas", "sarafan", "2026-11-24-sarafan.md", text)
        self.assertTrue(saved.get("ok"), saved)
        path = Path(saved["path"])
        self.assertTrue(path.is_file())
        self.assertIn("## План", path.read_text(encoding="utf-8"))

    def test_list_requests_finds_saved(self) -> None:
        agents.save_request("ideas", "sarafan", "2026-11-24-sarafan.md", "# заявка")
        listed = agents.list_requests("ideas")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["project"], "sarafan")

    def test_request_branch_is_slug(self) -> None:
        self.assertEqual(agents.request_branch("Сарафан Мини"), "requests/sarafan-mini")


class ReposReadTest(unittest.TestCase):
    """Чужие репозитории — только чтение."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.repo = self.workspace / "project"
        self.repo.mkdir(parents=True, exist_ok=True)
        _git(["init"], self.repo)
        (self.repo / "README.md").write_text("# Проект\n", encoding="utf-8")
        (self.repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        self._old = repos.workspace_home
        repos.workspace_home = lambda: self.workspace  # type: ignore[assignment]

    def tearDown(self) -> None:
        repos.workspace_home = self._old  # type: ignore[assignment]
        self.tmp.cleanup()

    def test_path_escape_is_refused(self) -> None:
        for bad in ("..", "../..", "a/b", "project/../.."):
            with self.assertRaises(PermissionError, msg=bad):
                repos.repo_path(bad)

    def test_list_repos_finds_repo(self) -> None:
        names = [item["name"] for item in repos.list_repos(detail=False)]
        self.assertIn("project", names)

    def test_read_file_works(self) -> None:
        result = repos.read_file("project", "README.md")
        self.assertIn("Проект", result.get("content", ""))

    def test_secret_file_is_closed(self) -> None:
        result = repos.read_file("project", ".env")
        self.assertIn("error", result)
        self.assertNotIn("secret", result.get("content", ""))

    def test_profile_reports_readme(self) -> None:
        profile = repos.profile("project")
        self.assertEqual(profile.get("readme"), "README.md")
        self.assertIn("Проект", profile.get("readme_head", ""))


class BridgeCommandTest(unittest.TestCase):
    """Команды моста: заявка собирается, пустая заявка не сохраняется."""

    def _fake_bridge(self, projects: list[dict]) -> object:
        import minigen_bridge as bridge

        class Fake:
            _known_projects = lambda self: projects   # type: ignore[assignment]

        return Fake()

    def test_draft_guesses_project(self) -> None:
        import minigen_bridge as bridge
        fake = self._fake_bridge([{"slug": "sarafan", "title": "Сарафан"}])
        result = bridge.Bridge.cmd_ingest_draft(
            fake, {"text": "Сделать импорт контактов в Сарафан"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["project"]["slug"], "sarafan")
        self.assertIn("## План", result["skeleton"])

    def test_draft_without_text_is_error(self) -> None:
        import minigen_bridge as bridge
        fake = self._fake_bridge([])
        result = bridge.Bridge.cmd_ingest_draft(fake, {"text": "  "})
        self.assertIn("error", result)

    def test_new_commands_are_registered(self) -> None:
        source = (BRIDGE_DIR / "minigen_bridge.py").read_text(encoding="utf-8")
        for command in ("agents.list", "agents.create", "agents.apply",
                        "ingest.draft", "ingest.save", "repos.list", "repos.read"):
            self.assertIn('"%s": self.cmd_' % command, source, command)

    def test_tools_point_to_commands(self) -> None:
        import minigen_tools as tools
        names = {tool["name"] for tool in tools.TOOLS}
        for name in ("agents.list", "agents.create", "agents.apply",
                     "ingest.draft", "ingest.save", "repos.list", "repos.read",
                     "repos.profile"):
            self.assertIn(name, names, name)


if __name__ == "__main__":
    unittest.main()
