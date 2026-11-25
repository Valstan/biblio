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
        # Локальный «origin»: ветка и пуш идут в него, сеть не нужна.
        self.origin = root / "origin.git"
        _git(["init", "--bare", str(self.origin)], root)
        _git(["remote", "add", "origin", str(self.origin)], self.repo)
        _git(["symbolic-ref", "HEAD", "refs/heads/master"], self.repo)
        # ветка master должна существовать (как в настоящем репозитории агента)
        _git(["commit", "--allow-empty", "-m", "начало"], self.repo)
        # PR — к GitHub не ходим: имитируем слияние локально (как на GitHub —
        # после слияния ветка сливается в master, и заявка живёт в master).
        self._old_pr = agents.publish_pr

        def fake_publish_pr(repo_slug: str, branch: str, title: str,
                            body: str = "", **kwargs) -> dict:
            _git(["checkout", "master"], self.repo)
            _git(["merge", "--no-ff", "-m", "PR: заявка", branch], self.repo)
            return {"ok": True, "pr": "1"}

        agents.publish_pr = fake_publish_pr  # type: ignore[assignment]
        self.registry = root / "registry"
        self.registry.mkdir(parents=True, exist_ok=True)
        # ⚠️ Подмена пути — **до** записи манифеста. Иначе манифест уходит в
        # живой реестр программы и обрезает его (живой случай 24.11: агент
        # «Идеи» остался без названия и области).
        self._old_dir = agents.registry_dir
        self._old_home = agents.workspace_home
        agents.registry_dir = lambda: self.registry     # type: ignore[assignment]
        agents.workspace_home = lambda: self.workspace  # type: ignore[assignment]
        agents.write_manifest("ideas", {"slug": "ideas", "repo": "Valstan/ideas"})

    def tearDown(self) -> None:
        agents.publish_pr = self._old_pr   # type: ignore[assignment]
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

    def test_request_reaches_master_through_pr(self) -> None:
        """Заявка обязана попасть в `master` (иначе её не восстановить)."""
        agents.save_request("ideas", "sarafan", "2026-11-24-sarafan.md", "# заявка")
        master = subprocess.run(["git", "ls-tree", "-r", "--name-only", "master"],
                                cwd=str(self.repo), capture_output=True, text=True,
                                encoding="utf-8", check=True).stdout
        self.assertIn("requests/sarafan/2026-11-24-sarafan.md", master)

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


class RegistryStateTest(unittest.TestCase):
    """Локальная копия реестра: совпадает ли с GitHub и как её вернуть.

    Реестр — источник правды, и его локальная копия иногда расходится с ним
    (живой случай 24.11: манифест агента «Идеи» обрезался — окно показало
    агента без названия и области). Программа должна это **заметить** и
    уметь вернуть копию к источнику.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = Path(self.tmp.name) / "minigen-agents"
        (self.registry / "agents" / "ideas").mkdir(parents=True)
        _git(["init"], self.registry)
        _git(["config", "user.email", "test@example.com"], self.registry)
        _git(["config", "user.name", "Test"], self.registry)
        (self.registry / "agents" / "ideas" / "agent.yaml").write_text(
            "slug: ideas\ntitle: Идеи\n", encoding="utf-8")
        _git(["add", "-A"], self.registry)
        _git(["commit", "-m", "манифест"], self.registry)
        self._old = agents.registry_dir
        agents.registry_dir = lambda: self.registry  # type: ignore[assignment]

    def tearDown(self) -> None:
        agents.registry_dir = self._old   # type: ignore[assignment]
        self.tmp.cleanup()

    @property
    def manifest(self) -> Path:
        return self.registry / "agents" / "ideas" / "agent.yaml"

    def test_clean_registry_is_not_dirty(self) -> None:
        state = agents.registry_state()
        self.assertTrue(state["cloned"])
        self.assertFalse(state["dirty"], state)
        self.assertEqual(state["files"], [])

    def test_changed_manifest_is_seen(self) -> None:
        self.manifest.write_text("slug: ideas\n", encoding="utf-8")
        state = agents.registry_state()
        self.assertTrue(state["dirty"], state)
        self.assertIn("agents/ideas/agent.yaml", state["files"])
        self.assertIn("отличается", state["why"])

    def test_restore_returns_the_registry_to_the_source(self) -> None:
        self.manifest.write_text("slug: ideas\n", encoding="utf-8")
        restored = agents.restore_registry()
        self.assertTrue(restored.get("ok"), restored)
        self.assertIn("title: Идеи", self.manifest.read_text(encoding="utf-8"))
        self.assertFalse(agents.registry_state()["dirty"])

    def test_restore_without_a_clone_is_an_error(self) -> None:
        agents.registry_dir = lambda: Path(self.tmp.name) / "нет-такого"  # type: ignore[assignment]
        restored = agents.restore_registry()
        self.assertFalse(restored.get("ok"))
        self.assertIn("не склонирован", str(restored.get("error")))


class LiveRegistryGuardTest(unittest.TestCase):
    """Живой реестр агентов тесты не трогают (живой случай 24.11).

    Тест, который пишет манифест в настоящий реестр, ломает программу
    владельца: манифест агента «Идеи» обрезался до двух строк (`slug` и
    `repo`), и окно показывало агента без названия и области, а
    восстановление агента на другой машине поехало бы не по тому
    манифесту. Страховка — временный реестр на весь сеанс тестов
    (`bridge/tests/__init__.py`).
    """

    def test_registry_is_temporary(self) -> None:
        live = agents.REGISTRY_HOME / agents.DEFAULT_REGISTRY.split("/")[-1]
        current = Path(agents.registry_dir())
        self.assertNotEqual(current, live, "тесты работают с живым реестром")
        self.assertIn(str(Path(tempfile.gettempdir())), str(current))

    def test_workspace_is_temporary(self) -> None:
        live = agents.DEFAULT_WORKSPACE
        current = Path(agents.workspace_home())
        self.assertNotEqual(current, live, "тесты работают с живым рабочим местом")
        self.assertIn(str(Path(tempfile.gettempdir())), str(current))

    def test_writing_a_manifest_does_not_touch_the_live_registry(self) -> None:
        live = agents.REGISTRY_HOME / agents.DEFAULT_REGISTRY.split("/")[-1]
        manifest = live / "agents" / "ideas" / "agent.yaml"
        before = manifest.read_bytes() if manifest.is_file() else None
        agents.write_manifest("ideas", {"slug": "ideas", "title": "Проверка"})
        after = manifest.read_bytes() if manifest.is_file() else None
        self.assertEqual(before, after, "тест тронул живой реестр агентов")


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
