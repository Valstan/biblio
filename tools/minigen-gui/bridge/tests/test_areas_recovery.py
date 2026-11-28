r"""Приёмка: области не теряются, а агент попадает в список (28.11).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

**Живой случай (28.11, этот компьютер).** Владелец завёл агента — и агент
**не появился в «Рабочих областях»**. Причина оказалась двойной:

1. **Конфиг областей пропал.** Файл `mcode-project-areas.json` — чисто
   локальная вещь машины (в git его нет), и на живой машине его удалили
   (28.11 в 00:39, сразу после раскатки). Демон полдня писал в журнал
   «конфиг не читается — держу последний рабочий», а **рабочего не было**:
   надзор над областями молча стоял, окно показывало «0 областей».

2. **Дефект записи.** В `cmd_create` было `projects = cfg.get("projects") or []`.
   При **пустом** списке `or` даёт **новый** список, `projects.append(...)`
   уходил в этот временный список, а в конфиг писался пустой. Список от
   этого оставался пустым — и **ни одна** область не заводилась никогда:
   ловушка, которая не выпускала.

Приёмка проверяет, что обе причины закрыты и что потеря конфига больше не
стирает области.

⚠️ Изолированно: временный конфиг, временный каталог областей, боевой
журнал не трогаем.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_state as state   # noqa: E402


class AreasRecoveryTest(unittest.TestCase):
    """Список областей: не теряется, восстанавливается, не уменьшается."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.core = state.load_daemons()["core"]
        self._old = (self.core.CONFIG, self.core.BIN, state.AREAS_ROOT)
        self._old_log = self.core.log

        self.config = root / "mcode-project-areas.json"
        self.areas_root = root / "areas"
        self.areas_root.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.core.CONFIG = self.config
        self.core.BIN = self.bin
        state.AREAS_ROOT = self.areas_root
        self.core.log = lambda msg: None

    def tearDown(self) -> None:
        self.core.CONFIG, self.core.BIN, state.AREAS_ROOT = self._old
        self.core.log = self._old_log
        self.tmp.cleanup()

    # ── вспомогательное ──────────────────────────────────────────────────
    def write_config(self, projects: list[dict], **extra) -> None:
        cfg = {"projects": projects}
        cfg.update(extra)
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def read_slugs(self) -> list[str]:
        data = json.loads(self.config.read_text(encoding="utf-8"))
        return [str(p.get("slug") or "") for p in (data.get("projects") or [])]

    def fake_bridge(self):
        import minigen_bridge

        class FakeBridge:
            core = self.core
            mods = state.load_daemons()

            def log(self, msg: str) -> None:
                pass

        return FakeBridge()

    # ── 1) главный дефект: пустой список не съедает новую область ───────
    def test_area_is_registered_when_the_config_is_empty(self) -> None:
        """⚠️ Живой случай 28.11: список пуст ⇒ область всё равно заводится."""
        import minigen_bridge

        self.write_config([])
        result = minigen_bridge.Bridge.cmd_create(
            self.fake_bridge(),
            {"slug": "probe", "title": "Проба", "folder": "", "karman": "probe"})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("probe", self.read_slugs(),
                      "область не попала в список — это тот самый дефект")

    def test_area_is_registered_when_the_config_is_missing(self) -> None:
        """⚠️ Конфиг **пропал** (файл удалили) ⇒ область заводится и видна."""
        import minigen_bridge

        self.assertFalse(self.config.exists())
        result = minigen_bridge.Bridge.cmd_create(
            self.fake_bridge(),
            {"slug": "probe", "title": "Проба", "folder": "", "karman": "probe"})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("probe", self.read_slugs())

    # ── 2) потеря конфига не стирает прежние области ─────────────────────
    def test_missing_config_does_not_lose_the_backup_areas(self) -> None:
        """Резервная копия рядом — единственная страховка: она и спасает."""
        import minigen_bridge

        self.write_config([{"slug": "старая", "title": "Старая"}])
        state.write_projects(state.read_projects())     # ⇒ появилась копия
        backup = Path(str(self.config) + ".bak")
        self.assertTrue(backup.is_file(), "копии рядом нет — страховки нет")
        self.config.unlink()                             # конфиг пропал

        result = minigen_bridge.Bridge.cmd_create(
            self.fake_bridge(),
            {"slug": "probe", "title": "Проба", "folder": "", "karman": "probe"})
        self.assertTrue(result.get("ok"), result)
        slugs = self.read_slugs()
        self.assertIn("старая", slugs, "область из копии потерялась")
        self.assertIn("probe", slugs)

    def test_read_restores_from_the_backup(self) -> None:
        self.write_config([{"slug": "старая", "title": "Старая"}])
        state.write_projects(state.read_projects())
        self.config.unlink()
        cfg = state.read_projects()
        self.assertEqual([str(p.get("slug")) for p in cfg.get("projects") or []],
                         ["старая"])
        self.assertTrue(cfg.get("_restored"), "восстановление не объявлено")

    # ── 3) запись не может уменьшить список ──────────────────────────────
    def test_write_refuses_to_shrink_the_list(self) -> None:
        """Программа нигде не удаляет области ⇒ уменьшение списка — потеря."""
        self.write_config([{"slug": "одна", "title": "Одна"},
                           {"slug": "две", "title": "Две"}])
        ok, err = state.write_projects({"projects": [{"slug": "одна"}]})
        self.assertFalse(ok, "список областей уменьшился — и запись прошла")
        self.assertIn("уменьшился", err)
        self.assertEqual(len(self.read_slugs()), 2, "список покорёжен")

    def test_write_can_grow_the_list(self) -> None:
        self.write_config([{"slug": "одна", "title": "Одна"}])
        ok, err = state.write_projects(
            {"projects": [{"slug": "одна"}, {"slug": "две"}]})
        self.assertTrue(ok, err)
        self.assertEqual(self.read_slugs(), ["одна", "две"])

    # ── 4) самовосстановление по машине ─────────────────────────────────
    def test_selfheal_rebuilds_the_list_from_the_local_folders(self) -> None:
        """Каталог области (`p-<имя>`) — и есть область на этой машине."""
        (self.areas_root / "p-ideas").mkdir(parents=True)
        (self.areas_root / "p-orca").mkdir(parents=True)
        res = state.areas_selfheal(force=True)
        self.assertTrue(res.get("ok"), res)
        slugs = self.read_slugs()
        self.assertIn("ideas", slugs)
        self.assertIn("orca", slugs)

    def test_selfheal_is_quiet_when_everything_is_in_place(self) -> None:
        self.write_config([{"slug": "одна", "title": "Одна"}])
        res = state.areas_selfheal()
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(res.get("healed"), "список трогали без нужды")
        self.assertEqual(self.read_slugs(), ["одна"])

    def test_selfheal_writes_the_config_back_after_a_loss(self) -> None:
        """Конфиг пропал, но копия есть ⇒ программа **возвращает** файл."""
        self.write_config([{"slug": "одна", "title": "Одна"}])
        state.write_projects(state.read_projects())
        self.config.unlink()
        res = state.areas_selfheal()
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(self.config.is_file(), "файл не вернулся на место")
        self.assertIn("одна", self.read_slugs())

    # ── 5) шаблон авторизации переживает потерю конфига ──────────────────
    def test_auth_template_is_found_among_local_areas(self) -> None:
        """⚠️ Живой случай 28.11: конфиг пропал ⇒ новая область вышла без
        входа (шаблон авторизации жил только в конфиге)."""
        template = self.areas_root / "p-образец"
        (template / "auth" / "prod" / "en" / "mcode-public").mkdir(parents=True)
        (template / "auth" / "prod" / "en" / "mcode-public" / "auth.json").write_text(
            '{"records": {}}', encoding="utf-8")
        self.assertEqual(state.auth_template(), str(template))

    def test_auth_template_from_settings(self) -> None:
        template = Path(self.tmp.name) / "шаблон"
        template.mkdir()
        old = state.read_settings()
        state.write_settings({"authTemplate": str(template)})
        try:
            self.assertEqual(state.auth_template(), str(template))
        finally:
            state.write_settings({"authTemplate": old.get("authTemplate") or ""})


class AgentAreaNameTest(unittest.TestCase):
    """Имя области агента — без `p-` (живой случай 28.11)."""

    def test_prefix_is_cut(self) -> None:
        import minigen_agents as agents

        self.assertEqual(agents.area_slug({"area": "p-ideas"}), "ideas")
        self.assertEqual(agents.area_slug({"area": "ideas"}), "ideas")
        self.assertEqual(agents.area_slug({}), "")
        self.assertEqual(agents.area_slug({"area": ""}), "")


if __name__ == "__main__":
    unittest.main()
