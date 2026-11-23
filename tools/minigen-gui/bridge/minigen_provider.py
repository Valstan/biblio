"""Настройки модели — так, как их объявляет сама платформа.

Заказ владельца (23.11): МиниГен должен работать на самой новой модели, с самым
большим контекстом и самым высоким эффортом; оператор должен выбирать модель,
размер контекста и уровень эффорта **прямо в интерфейсе МиниГена** — «как в
родной среде». А в будущем МиниГен должен уметь подключать и другие
платформы (другие npm-провайдеры, другие схемы параметров).

Поэтому источник правды — **конфиг провайдера области** (`config.yaml`): его
пишет сама платформа, и рантайм (`@minimax-ai/code`) строит из него свой
каталог моделей. Мы читаем те же поля, что и рантайм, — и отправляем вендору
ровно то, что он понимает.

| поле конфига | что это (по рантайму) |
|---|---|
| `models.<id>.limit.context` | дефолтное окно контекста |
| `models.<id>.contextWindowOptions` | допустимые окна контекста |
| `models.<id>.contextWindowOptionHints` | подсказки к окнам (`higher_usage` = дороже) |
| `models.<id>.thinking.effortOptions` | уровни эффорта платформы |
| `models.<id>.thinking.defaultEffort` | дефолтный эффорт модели |
| `models.<id>.variants` | варианты (thinking on/off) |
| `models.<id>.limit.output` | максимум ответа |
| `defaultModelContextWindow` | выбранное окно контекста области |
| `defaultModelThinking.effort` | выбранный эффорт области |
| `defaultModelVariant` | выбранный вариант области |

⚠️ Проверено пробой вендора (23.11): `thinking: {type: "enabled", effort:
"max"}` вендор принимает (200 OK). Рантайм в запросе шлёт `thinking:
{effort: …}` — значит, форма та же.

⚠️ Настройки пишутся в конфиг области **текстом, а не пересборкой YAML**: в
конфиге есть якоря (`&ref_0`), и пересборка развернула бы их и испортила
формат. Меняем только нужные верхнеуровневые ключи, остальное — байт в байт.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

AREAS_ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT",
                                 str(Path.home() / ".minimax-areas")))

# Сколько токенов примерно в одном символе — для оценки бюджета контекста.
CHARS_PER_TOKEN = 4


class ProviderUnavailable(RuntimeError):
    """Провайдер/модель недоступны — программа должна сказать об этом честно."""


def area_config_path(area_slug: str) -> Path:
    return AREAS_ROOT / ("p-" + area_slug) / "config.yaml"


def read_area_config(area_slug: str) -> dict[str, Any]:
    """Прочитать `config.yaml` области (как это делает рантайм)."""
    path = area_config_path(area_slug)
    if not path.is_file():
        raise ProviderUnavailable("нет конфига провайдера: %s" % path)
    try:
        import yaml
    except ImportError as exc:
        raise ProviderUnavailable("PyYAML не установлен: %s" % exc)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise ProviderUnavailable("конфиг провайдера не читается: %s" % exc)
    if not isinstance(data, dict):
        raise ProviderUnavailable("конфиг провайдера — не словарь")
    return data


def provider_of(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Провайдер конфига и его раздел (как выбирает рантайм: minimax — приоритет)."""
    providers = cfg.get("provider") or {}
    if not isinstance(providers, dict) or not providers:
        raise ProviderUnavailable("в конфиге нет провайдера")
    provider_id = "minimax" if "minimax" in providers else list(providers)[0]
    pconf = providers.get(provider_id) or {}
    if not isinstance(pconf, dict):
        raise ProviderUnavailable("раздел провайдера — не словарь")
    return provider_id, pconf


def _model_defaults(model_cfg: dict[str, Any]) -> dict[str, Any]:
    """Родные значения модели — ровно те, что рантайм кладёт в каталог."""
    limit = model_cfg.get("limit") or {}
    thinking = model_cfg.get("thinking") or {}
    variants = model_cfg.get("variants") or {}
    context_default = limit.get("context")
    context_options = [int(x) for x in (model_cfg.get("contextWindowOptions") or [])
                       if isinstance(x, (int, float))]
    effort_options = [str(x) for x in (thinking.get("effortOptions") or [])]
    out: dict[str, Any] = {
        "context_default": int(context_default) if isinstance(context_default, (int, float)) else 0,
        "context_options": context_options,
        "context_hints": {str(k): str(v) for k, v in
                          (model_cfg.get("contextWindowOptionHints") or {}).items()},
        "effort_options": effort_options,
        "effort_default": str(thinking.get("defaultEffort") or "") if thinking else "",
        "max_output": int(limit.get("output") or 0) if isinstance(limit.get("output"), (int, float)) else 0,
        "variants": [{"id": str(name), "thinking": (value.get("thinking") or {})}
                     for name, value in variants.items() if isinstance(value, dict)],
        "modalities": model_cfg.get("modalities") or {},
        "reasoning": bool(model_cfg.get("reasoning")),
    }
    return out


def catalog(area_slug: str) -> dict[str, Any]:
    """Каталог моделей для интерфейса — из родного конфига платформы.

    В нём всё, что платформа сама объявляет: какие окна контекста бывают,
    какие уровни эффорта, какие варианты мышления. Интерфейс показывает
    ровно это — как в родной среде, без выдуманных значений.
    """
    cfg = read_area_config(area_slug)
    provider_id, pconf = provider_of(cfg)
    models_cfg = pconf.get("models") or {}
    models: list[dict[str, Any]] = []
    for model_id, model_cfg in models_cfg.items():
        if not isinstance(model_cfg, dict):
            continue
        item: dict[str, Any] = {"id": str(model_id),
                                "name": str(model_cfg.get("name") or model_id)}
        item.update(_model_defaults(model_cfg))
        models.append(item)
    default_ref = str(cfg.get("defaultModel") or "")
    default_model = default_ref.split("/", 1)[1] if "/" in default_ref else default_ref
    order = [str(x) for x in (pconf.get("model_order") or [])]
    if order:
        models.sort(key=lambda m: order.index(m["id"]) if m["id"] in order else 99)
    return {
        "provider": provider_id,
        "provider_name": str(pconf.get("name") or provider_id),
        "npm": str(pconf.get("npm") or ""),
        "models": models,
        "default_model": default_model,
        # выбранные в области значения — их рантайм и называет дефолтами
        "area_context": int(cfg.get("defaultModelContextWindow") or 0),
        "area_effort": str((cfg.get("defaultModelThinking") or {}).get("effort") or ""),
        "area_variant": str(cfg.get("defaultModelVariant") or ""),
        "config_path": str(area_config_path(area_slug)),
    }


def model_entry(area_slug: str, model_id: str) -> dict[str, Any]:
    """Родные настройки одной модели (по id)."""
    for item in catalog(area_slug)["models"]:
        if item["id"] == model_id:
            return item
    raise ProviderUnavailable("нет такой модели у провайдера: %s" % model_id)


def effective(area_slug: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Что именно используем: модель, окно контекста, эффорт, вариант.

    Порядок: **выбор оператора** (настройки программы) → родной дефолт
    области → дефолт модели. Значение, которого платформа не объявляет,
    не берём: лучше дефолт платформы, чем выдуманный.
    """
    settings = settings or {}
    cat = catalog(area_slug)
    models = cat["models"]
    wanted = str(settings.get("model") or "").strip()
    entry = next((m for m in models if m["id"] == wanted), None)
    if entry is None:
        wanted = cat["default_model"]
        entry = next((m for m in models if m["id"] == wanted), None)
    if entry is None and models:
        entry = models[0]
        wanted = entry["id"]
    if entry is None:
        raise ProviderUnavailable("у провайдера нет ни одной модели")

    # окно контекста
    context = int(settings.get("modelContext") or 0)
    allowed = entry.get("context_options") or []
    if not context:
        context = int(cat.get("area_context") or 0)
    if not context:
        context = int(entry.get("context_default") or 0)
    if allowed and context and context not in allowed:
        context = int(entry.get("context_default") or 0) or int(allowed[0])
    if not context:
        context = int(entry.get("context_default") or 0)

    # эффорт
    effort = str(settings.get("modelEffort") or "").strip()
    efforts = entry.get("effort_options") or []
    if not effort:
        effort = str(cat.get("area_effort") or "")
    if not effort:
        effort = str(entry.get("effort_default") or "")
    if efforts and effort and effort not in efforts:
        effort = str(entry.get("effort_default") or "") or efforts[0]
    if not efforts:
        effort = ""

    variant = str(cat.get("area_variant") or "")
    return {
        "provider": cat["provider"],
        "model": wanted,
        "context": int(context or 0),
        "effort": effort,
        "variant": variant,
        "max_output": int(entry.get("max_output") or 0),
        "effort_options": efforts,
        "context_options": allowed,
    }


def request_params(eff: dict[str, Any]) -> dict[str, Any]:
    """Параметры запроса в **родной** форме платформы.

    Уровень эффорта вендор принимает как `thinking: {type: "enabled",
    effort: "<уровень>"}` (проверено пробой 23.11: 200 OK). Если модель
    эффортов не объявляет — не шлём ничего (модель сама решит).
    """
    out: dict[str, Any] = {}
    effort = str(eff.get("effort") or "")
    if effort and effort != "default":
        out["thinking"] = {"type": "enabled", "effort": effort}
    return out


# ─────────────────────────────────────────── применение к областям (нативно)

def _set_top_level(text: str, key: str, value: str) -> str:
    """Заменить верхнеуровневый ключ YAML (текстом, без пересборки файла)."""
    pattern = re.compile(r"(?m)^%s:.*$" % re.escape(key))
    line = "%s: %s" % (key, value)
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text, count=1)
    return text.rstrip("\n") + "\n" + line + "\n"


def _set_thinking(text: str, effort: str) -> str:
    """Заменить блок `defaultModelThinking:` (там живёт выбранный эффорт)."""
    pattern = re.compile(r"(?ms)^defaultModelThinking:[^\n]*\n(?:[ \t]+[^\n]*\n)*")
    block = "defaultModelThinking:\n  effort: %s\n" % effort
    if pattern.search(text):
        return pattern.sub(lambda _m: block, text, count=1)
    return text.rstrip("\n") + "\n" + block


def apply_to_area(area_slug: str, model: str, context: int = 0,
                  effort: str = "", variant: str = "") -> dict[str, Any]:
    """Записать в конфиг области выбранные модель/контекст/эффорт **нативно**.

    Правила — те же, что у рантайма: окно контекста должно быть в списке
    `contextWindowOptions` модели, эффорт — в `thinking.effortOptions`
    (иначе рантайм отвергнет: «context_limit must be supported by the
    selected model»).
    """
    entry = model_entry(area_slug, model)
    allowed_context = entry.get("context_options") or []
    allowed_effort = entry.get("effort_options") or []
    if context and allowed_context and context not in allowed_context:
        return {"ok": False,
                "why": "модель %s не поддерживает окно %s (доступны: %s)"
                       % (model, context, ", ".join(str(x) for x in allowed_context))}
    if effort and allowed_effort and effort not in allowed_effort:
        return {"ok": False,
                "why": "модель %s не знает эффорта %s (доступны: %s)"
                       % (model, effort, ", ".join(allowed_effort))}
    path = area_config_path(area_slug)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "why": "не читается %s: %s" % (path, exc)}
    cat = catalog(area_slug)
    provider = cat["provider"]
    new = _set_top_level(text, "defaultModel", "%s/%s" % (provider, model))
    if context:
        new = _set_top_level(new, "defaultModelContextWindow", str(int(context)))
    if effort:
        new = _set_thinking(new, effort)
    if variant:
        new = _set_top_level(new, "defaultModelVariant", variant)
    if new == text:
        return {"ok": True, "changed": False, "area": area_slug,
                "why": "уже так"}
    tmp = path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(path)          # атомарно: рантайм не увидит половину
    except OSError as exc:
        return {"ok": False, "why": "не записалось: %s" % exc}
    return {"ok": True, "changed": True, "area": area_slug,
            "model": model, "context": context, "effort": effort}


def apply_to_areas(area_slugs: list[str], model: str, context: int = 0,
                   effort: str = "", variant: str = "") -> dict[str, Any]:
    """Разложить выбранные настройки по областям (каждой — по её провайдеру)."""
    done: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for slug in area_slugs:
        try:
            res = apply_to_area(slug, model, context, effort, variant)
        except ProviderUnavailable as exc:
            res = {"ok": False, "area": slug, "why": str(exc)}
        (done if res.get("ok") else failed).append(res)
    return {"applied": done, "failed": failed,
            "changed": sum(1 for r in done if r.get("changed"))}
