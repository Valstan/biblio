r"""Тест создания рабочей области (изолированно от боевого конфига).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Проверяем РЕАЛЬНУЮ команду `areas.create` целиком: каталог данных, копия
авторизации, запись в конфиг, вызов скрипта ярлыков.

⚠️ Почему изолированно: настоящая команда копирует авторизацию из
шаблонной области, а refresh-токен одноразовый — копия живой авторизации в
новую область может сломать вход у ШАБЛОНА (а это Мозг или другая рабочая
область владельца). Поэтому тест работает на временном конфиге, временном
каталоге областей и поддельном шаблоне авторизации.

Отдельно: сам шаг «ярлык» в бою вызывает `make-mcode-project-shortcuts.ps1`
из `C:\Users\valstan\bin` (одна реализация на все области). Здесь подменяем
его заглушкой и проверяем, что команда его зовёт.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_state as state   # noqa: E402


class CreateAreaTest(unittest.TestCase):
    """areas.create: каталог + авторизация + конфиг + ярлык."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.core = state.load_daemons()["core"]
        self._old = (self.core.CONFIG, self.core.BIN, state.AREAS_ROOT)

        # временный конфиг, каталог областей и «живая» папка bin
        self.config = root / "mcode-project-areas.json"
        template = root / "template"
        template.mkdir()
        (template / "auth.json").write_text('{"records": {}}', encoding="utf-8")
        self.config.write_text(json.dumps({
            "authTemplate": str(template),
            "projects": [{"slug": "brain", "title": "МОЗГ", "folder": ""}],
        }, ensure_ascii=False), encoding="utf-8")

        self.bin = root / "bin"
        self.bin.mkdir()
        self.maker = self.bin / "make-mcode-project-shortcuts.ps1"
        self.maker.write_text("param()\n", encoding="utf-8")
        self.maker_marker = root / "shortcuts-called.txt"
        self.maker.write_text(
            'param()\nSet-Content -Path "%s" -Value "yes"\n' % self.maker_marker,
            encoding="utf-8")

        self.areas_root = root / "areas"
        self.core.CONFIG = self.config
        self.core.BIN = self.bin
        state.AREAS_ROOT = self.areas_root
        # Журнал — боевой, туда писать нельзя: тест оставил бы владельцу
        # запись про несуществующую область.
        self._old_log = self.core.log
        self.core.log = lambda msg: None

    def tearDown(self) -> None:
        self.core.CONFIG, self.core.BIN, state.AREAS_ROOT = self._old
        self.core.log = self._old_log
        self.tmp.cleanup()

    def test_create_area(self) -> None:
        import minigen_bridge

        class FakeBridge:
            core = self.core
            mods = state.load_daemons()

            def log(self, msg: str) -> None:
                pass

        result = minigen_bridge.Bridge.cmd_create(
            FakeBridge(), {"slug": "probe1", "title": "Проба", "folder": "",
                           "karman": "probe1"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["slug"], "probe1")

        # 1) каталог данных области создан, авторизация скопирована
        area_dir = self.areas_root / "p-probe1"
        self.assertTrue(area_dir.is_dir())
        self.assertTrue((area_dir / "auth.json").is_file())

        # 2) запись в конфиге
        data = json.loads(self.config.read_text(encoding="utf-8"))
        slugs = [p["slug"] for p in data["projects"]]
        self.assertIn("probe1", slugs)
        self.assertIn("brain", slugs)      # прежние области не тронуты

        # 3) ярлыки: команда создания позвала общий скрипт
        self.assertTrue(self.maker_marker.is_file(),
                        "скрипт ярлыков не вызван: %s" % result.get("shortcut"))

        # 4) повторное создание с тем же slug — отказ (без дубля)
        again = minigen_bridge.Bridge.cmd_create(
            FakeBridge(), {"slug": "probe1", "title": "Проба"})
        self.assertIn("error", again)


if __name__ == "__main__":
    unittest.main()
