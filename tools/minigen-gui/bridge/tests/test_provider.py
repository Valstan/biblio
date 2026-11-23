"""Тесты «родных настроек модели» (23.11, заказ владельца).

Заказ: МиниГен должен работать на самой новой модели, с самым большим
контекстом и самым высоким эффортом; оператор должен выбирать модель, размер
контекста и уровень эффорта прямо в интерфейсе — так же, как в родной
среде; а в будущем МиниГен должен уметь подключать другие платформы.

Проверяем:

- **каталог берётся из родного конфига платформы** (окна контекста, уровни
  эффорта, варианты, лимиты) — никаких выдуманных значений;
- **выбор оператора главнее родных дефолтов**, но значение, которого
  платформа не объявляет, **отбрасывается** (иначе рантайм его отвергнет);
- **параметры запроса в нативной форме** платформы (`thinking: {effort}`);
- **применение к области пишет нативные ключи** и не портит остальной
  конфиг (в нём есть YAML-якоря — пересборка файла их развернула бы);
- **демоны передают выбор в рантайм** флагами `--model/--effort`.
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

import minigen_provider as provider  # noqa: E402

CONFIG = """\
logLevel: info
provider:
  minimax:
    name: MiniMax
    npm: '@ai-sdk/anthropic'
    options:
      apiKey: stub
      baseURL: https://example.invalid/v1
    models:
      MiniMax-M3.1-Flash:
        name: M3.1-Flash
        reasoning: true
        limit:
          context: 512000
          output: 128000
        contextWindowOptions:
          - 512000
          - 1000000
        contextWindowOptionHints:
          '1000000': higher_usage
        thinking:
          effortOptions:
            - default
            - low
            - high
            - max
          defaultEffort: default
        variants:
          none-thinking:
            thinking:
              type: disabled
          thinking:
            thinking:
              type: adaptive
        capabilities: &ref_0
          support_files_api: true
    model_order:
      - MiniMax-M3.1-Flash
defaultModel: minimax/MiniMax-M3.1-Flash
defaultModelContextWindow: 1000000
defaultModelThinking:
  effort: max
defaultModelVariant: thinking
"""


class ProviderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        area = self.root / "p-test"
        area.mkdir(parents=True)
        (area / "config.yaml").write_text(CONFIG, encoding="utf-8")
        self._old_root = provider.AREAS_ROOT
        provider.AREAS_ROOT = self.root

    def tearDown(self) -> None:
        provider.AREAS_ROOT = self._old_root
        self.tmp.cleanup()


class CatalogTest(ProviderTestCase):
    def test_catalog_reads_native_options(self) -> None:
        cat = provider.catalog("test")
        self.assertEqual(cat["provider"], "minimax")
        self.assertEqual(cat["default_model"], "MiniMax-M3.1-Flash")
        entry = cat["models"][0]
        self.assertEqual(entry["context_options"], [512000, 1000000])
        self.assertEqual(entry["context_default"], 512000)
        self.assertEqual(entry["effort_options"], ["default", "low", "high", "max"])
        self.assertEqual(entry["max_output"], 128000)
        self.assertEqual([v["id"] for v in entry["variants"]],
                         ["none-thinking", "thinking"])
        self.assertEqual(entry["context_hints"].get("1000000"), "higher_usage")

    def test_area_defaults_are_read(self) -> None:
        cat = provider.catalog("test")
        self.assertEqual(cat["area_context"], 1000000)
        self.assertEqual(cat["area_effort"], "max")
        self.assertEqual(cat["area_variant"], "thinking")

    def test_effective_uses_operator_choice(self) -> None:
        eff = provider.effective("test", {"model": "MiniMax-M3.1-Flash",
                                          "modelContext": 1000000,
                                          "modelEffort": "max"})
        self.assertEqual(eff["model"], "MiniMax-M3.1-Flash")
        self.assertEqual(eff["context"], 1000000)
        self.assertEqual(eff["effort"], "max")
        self.assertEqual(eff["max_output"], 128000)

    def test_effective_falls_back_to_area_defaults(self) -> None:
        eff = provider.effective("test", {})
        self.assertEqual(eff["context"], 1000000)
        self.assertEqual(eff["effort"], "max")

    def test_invented_values_are_rejected(self) -> None:
        """Значение, которого платформа не объявляет, брать нельзя."""
        eff = provider.effective("test", {"modelEffort": "turbo",
                                          "modelContext": 123456})
        self.assertNotEqual(eff["effort"], "turbo")
        self.assertIn(eff["effort"], ["default", "low", "high", "max"])
        self.assertIn(eff["context"], [512000, 1000000])

    def test_request_params_are_native(self) -> None:
        eff = provider.effective("test", {"modelEffort": "max"})
        params = provider.request_params(eff)
        self.assertEqual(params, {"thinking": {"type": "enabled", "effort": "max"}})

    def test_request_params_empty_without_effort(self) -> None:
        self.assertEqual(provider.request_params({"effort": ""}), {})
        self.assertEqual(provider.request_params({"effort": "default"}), {})


class ApplyToAreaTest(ProviderTestCase):
    def test_apply_writes_native_keys(self) -> None:
        res = provider.apply_to_area("test", "MiniMax-M3.1-Flash", 1000000, "max", "thinking")
        self.assertTrue(res["ok"], res.get("why"))
        text = (self.root / "p-test" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("defaultModel: minimax/MiniMax-M3.1-Flash", text)
        self.assertIn("defaultModelContextWindow: 1000000", text)
        self.assertIn("effort: max", text)
        # остальной конфиг не тронут (якорь остался якорь)
        self.assertIn("&ref_0", text)
        self.assertIn("baseURL: https://example.invalid/v1", text)

    def test_apply_refuses_unsupported_values(self) -> None:
        res = provider.apply_to_area("test", "MiniMax-M3.1-Flash", 123456, "max", "")
        self.assertFalse(res["ok"])
        self.assertIn("не поддерживает окно", res["why"])
        res = provider.apply_to_area("test", "MiniMax-M3.1-Flash", 1000000, "turbo", "")
        self.assertFalse(res["ok"])
        self.assertIn("не знает эффорта", res["why"])

    def test_apply_is_idempotent(self) -> None:
        provider.apply_to_area("test", "MiniMax-M3.1-Flash", 1000000, "max", "thinking")
        again = provider.apply_to_area("test", "MiniMax-M3.1-Flash", 1000000, "max", "thinking")
        self.assertTrue(again["ok"])
        self.assertFalse(again.get("changed"))


class RuntimeFlagsTest(unittest.TestCase):
    """Демоны передают выбор оператора рантайму родными флагами."""

    def test_model_flags_from_settings(self) -> None:
        core_path = BRIDGE_DIR.parent.parent / "minimax-project-areas" / "minigen_core.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "core_under_test", str(core_path))
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "minigen-gui"
            home.mkdir(parents=True)
            (home / "settings.json").write_text(
                json.dumps({"model": "MiniMax-M3.1-Flash", "modelEffort": "max"}),
                encoding="utf-8")
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = tmp
            try:
                prefs = core.model_preferences()
                self.assertEqual(prefs["model"], "MiniMax-M3.1-Flash")
                self.assertEqual(prefs["effort"], "max")
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


if __name__ == "__main__":
    unittest.main()
