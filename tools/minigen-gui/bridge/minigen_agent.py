"""Встроенная нейросеть «МиниГена»: клиент модели + агент-цикл с инструментами.

Задача (заказ владельца, 23.11): внутри программы есть чат, в котором
работает нейросеть MiniMax. Она видит состояние программы, сама выбирает,
что сделать, и **выполняет** это через инструменты. Если модель недоступна
(отказ по очкам, обрыв сети, протухший токен) — чат честно об этом сообщает,
а управление остаётся у алгоритмических функций (сторожей). Сторожа
работают независимо от этого модуля: он ничего не ломает, когда модель мертва.

Протокол с вендором (проверен пробой 23.11): Anthropic-совместимый
`POST {baseURL}/messages`, авторизация — `Authorization: Bearer <токен
области>`. Ключ из `config.yaml` — заглушка, рабочий токен лежит в сторе
авторизации области (он же списывает очки).

Правила безопасности, зашитые в цикл:

- **сначала наблюдение, потом действие**: в промпте и в каталоге инструментов
  безопасные вызовы стоят первыми;
- **разрушительное — через подтверждение**: инструмент с `dangerous=True`
  не выполняется молча, а возвращается владельцу на подтверждение;
- **сомнение — спросить**: у инструмента есть параметр, значение которого
  модель обязана уточнить у владельца, а не угадать;
- **правдивость важнее полноты**: системный промпт запрещает выдумывать
  состояние — всё берётся из инструментов.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
KNOWLEDGE_PACK = HERE.parent / "docs" / "knowledge-pack.md"

import minigen_provider as provider  # noqa: E402
import minigen_council as council    # noqa: E402

MAX_ROUNDS = 24
DEFAULT_TIMEOUT = 120
# Сколько символов ответа инструмента уходит обратно в модель. Файлы кода
# читаются целиком — иначе модель видит только начало модуля и не может
# найти нестыковку (окно модели — 1 млн токенов, места хватает).
TOOL_RESULT_LIMIT = 24_000
#: Бюджет совета — **свой**, работа его не съедает (живой случай 25.11, проба
#: самоотладки: при общем бюджете на работу оставалось 4 шага, а задача «найди
#: дефект и почини» столько не занимает). Совет обсуждает — работа делает;
#: одно не отменяет другого.
COUNCIL_ROUNDS = 16


class ModelUnavailable(RuntimeError):
    """Модель недоступна — чат должен честно сказать об этом."""


# ───────────────────────────────────────────────────── конфиг провайдера

def provider_config(area_slug: str) -> dict[str, Any]:
    """Прочитать конфиг провайдера области (`config.yaml` рядом с данными).

    Схема та же, что у рантайма MiniMax Code:

        provider:
          minimax:
            npm: "@ai-sdk/anthropic"
            options: {baseURL: ..., apiKey: ...}
            models: {MiniMax-M3: {...}}
        defaultModel: "minimax/MiniMax-M3.1-Flash"

    ⚠️ `options.apiKey` в конфиге — заглушка (6 символов), рабочего ключа
    там нет. Настоящая авторизация — **токен области** из её стора
    (`Bearer <accessToken>`); он же считается очки. Поэтому ключ из конфига
    используется только как запасной вариант.

    Ключи лежат на машине владельца и в репозиторий НЕ попадают.
    """
    import yaml

    root = Path(os.environ.get("MINIMAX_AREAS_ROOT",
                                str(Path.home() / ".minimax-areas")))
    path = root / ("p-" + area_slug) / "config.yaml"
    if not path.is_file():
        raise ModelUnavailable("нет конфига провайдера: %s" % path)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise ModelUnavailable("конфиг провайдера не читается: %s" % exc)
    providers = cfg.get("provider") or {}
    if not isinstance(providers, dict) or not providers:
        raise ModelUnavailable("в конфиге нет провайдера")
    provider_id = "minimax" if "minimax" in providers else list(providers)[0]
    pconf = providers.get(provider_id) or {}
    options = pconf.get("options") or {}
    base_url = str(options.get("baseURL") or "").rstrip("/")
    if not base_url:
        raise ModelUnavailable("в конфиге провайдера нет baseURL")
    models = list((pconf.get("models") or {}).keys())
    default_ref = str(cfg.get("defaultModel") or "")
    model = ""
    if "/" in default_ref:
        model = default_ref.split("/", 1)[1]
    if model not in models and models:
        model = models[0]
    return {
        "provider": provider_id,
        "base_url": base_url,
        "api_key": str(options.get("apiKey") or ""),
        "model": model,
        "models": models,
        "npm": str(pconf.get("npm") or ""),
        "headers": dict(options.get("headers") or {}),
        "config_path": str(path),
    }


# ────────────────────────────────────────────────────────── вызов модели

def _post_json(url: str, headers: dict[str, str], body: dict[str, Any],
               timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise ModelUnavailable("вендор ответил %s: %s" % (exc.code, detail))
    except Exception as exc:  # noqa: BLE001
        raise ModelUnavailable("сеть/вендор недоступен: %s" % exc)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ModelUnavailable("ответ вендора не разобран: %s" % exc)


def chat_completion(messages: list[dict[str, Any]], system: str,
                    tools: list[dict[str, Any]], cfg: dict[str, Any],
                    timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Один запрос к модели (Anthropic-совместимый `/messages`).

    ⚠️ Модель, окно контекста и уровень эффорта берём **родными** для
    платформы: `thinking: {type: "enabled", effort: "<уровень>"}` — эту форму
    вендор принимает (проверено пробой 23.11), и ровно её же шлёт рантайм.
    """
    url = cfg["base_url"] + "/messages"
    headers = {"Content-Type": "application/json",
               "anthropic-version": "2023-06-01"}
    for key, value in (cfg.get("headers") or {}).items():
        headers[key] = value
    token = cfg.get("token") or cfg.get("api_key") or ""
    if token:
        headers["Authorization"] = "Bearer " + token
    max_output = int(cfg.get("max_output") or 0)
    body: dict[str, Any] = {
        "model": cfg["model"],
        "max_tokens": max_output if max_output else 4096,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools
    body.update(provider.request_params(cfg))
    return _post_json(url, headers, body, timeout=timeout)


def parse_message(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Разобрать ответ вендора: (блоки, текст, вызовы инструментов)."""
    blocks = data.get("content") or []
    text = "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if block.get("type") == "text"
    ).strip()
    calls = [
        {
            "id": str(block.get("id") or ""),
            "name": str(block.get("name") or ""),
            "input": block.get("input") if isinstance(block.get("input"), dict) else {},
        }
        for block in blocks
        if block.get("type") == "tool_use"
    ]
    return blocks, text, calls


# ────────────────────────────────────────────────────────── системный промпт

def system_prompt() -> str:
    """Системный промпт = сведения об устройстве программы (knowledge-pack)."""
    try:
        return KNOWLEDGE_PACK.read_text(encoding="utf-8")
    except OSError:
        return ("Ты — встроенная нейросеть программы «МиниГен». "
                "Состояние программы узнавай только через инструменты.")


# ────────────────────────────────────────────────────────────── агент-цикл

class Agent:
    """Диалог с моделью: сообщения, вызовы инструментов, подтверждения.

    `dispatch(cmd, args)` — исполняемая команда программы (тот же реестр,
    что и у кнопок в окне). Инструменты описаны в `minigen_tools.py`;
    опасные требуют подтверждения владельца и ждут его ответа.
    """

    def __init__(self, dispatch: Callable[[str, dict[str, Any]], Any],
                 tools: list[dict[str, Any]],
                 token_provider: Callable[[str], str] | None = None,
                 settings_provider: Callable[[], dict[str, Any]] | None = None):
        self.dispatch = dispatch
        self.tools = tools
        self.token_provider = token_provider
        self.settings_provider = settings_provider
        self.pending: dict[str, Any] | None = None

    # ── низкий уровень ──
    def _tool_payload(self, tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Описание инструментов для модели (свой набор — у голоса совета)."""
        source = tools if tools is not None else self.tools
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
            }
            for tool in source
        ]

    def _model_config(self, area_slug: str, model: str = "") -> dict[str, Any]:
        """Конфиг запроса: модель, окно контекста, эффорт — родными словами.

        Порядок выбора: **выбор оператора** (настройки программы) → родной
        дефолт области → дефолт модели. Значения, которых платформа не
        объявляет, не выдумываются.
        """
        cfg = provider_config(area_slug)
        if self.token_provider:
            token = self.token_provider(area_slug)
            if token:
                cfg["token"] = token
        settings = self.settings_provider() if self.settings_provider else {}
        try:
            eff = provider.effective(area_slug, settings)
        except Exception:  # noqa: BLE001
            # каталог/конфиг недоступны — чат всё равно должен работать
            # (модель и токен берём из обычного конфига провайдера)
            eff = {}
        if model:
            # явная модель из запроса — важнее настроек (её выбирает оператор
            # в этом же чате), но параметры модели всё равно родные
            try:
                eff = provider.effective(area_slug, {**settings, "model": model})
            except Exception:  # noqa: BLE001
                eff = {"model": model}
        if eff.get("model"):
            cfg["model"] = eff["model"]
        cfg["context"] = int(eff.get("context") or 0)
        cfg["effort"] = str(eff.get("effort") or "")
        cfg["variant"] = str(eff.get("variant") or "")
        cfg["max_output"] = int(eff.get("max_output") or 0)
        return cfg

    def _tool_by_name(self, name: str) -> dict[str, Any] | None:
        return next((tool for tool in self.tools if tool["name"] == name), None)

    # ── основной ход ──
    def ask(self, history: list[dict[str, Any]], user_text: str,
            area_slug: str = "minigen", model: str = "",
            note: str = "", blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Обработать реплику владельца. Возвращает ответ и ход работы.

        Новое задание сначала обсуждает **совет специалистов** (промт #132):
        секретарь отбирает роли → специалисты дают оценки → судья выносит
        решение и назначает исполнителей → основной ход работает по решению.
        Продолжения нитки («Продолжай») совет не зовёт — он по этой задаче уже
        отработал.

        ⚠️ `note` — подсказка на **один ход** (сводка отчётов сторожей, заказ
        24.11). Она попадает в системную подсказку хода, а **не** в историю
        сообщений: отчёты сторожей — не реплики владельца, и в переписке им
        не место (промт #129). В историю попадают только слова владельца и
        ответы модели.

        ⚠️ `blocks` — файлы, приложенные оператором (промт #90): они уходят в
        сообщение **блоками** (текст документа или картинка), а не строкой.
        Протокол вендора блоки уже умеет — менять ничего не пришлось.
        """
        cfg = self._model_config(area_slug, model)
        messages = list(history)
        if blocks:
            content: Any = [{"type": "text", "text": user_text}] + list(blocks)
        else:
            content = user_text
        messages.append({"role": "user", "content": content})
        budget = {"left": MAX_ROUNDS}
        settings = self.settings_provider() if self.settings_provider else {}
        # Совет включается **настройкой программы**: агент без провайдера
        # настроек (тесты, вызовы из стороннего кода) работает без совета —
        # иначе любая проверка агента платила бы за лишние вызовы модели.
        council_on = (self.settings_provider is not None
                      and council.enabled(settings))
        council_text = ""
        decision: dict[str, Any] = {}
        if council_on and not council.is_continuation(user_text):
            outcome = self._council(messages, cfg, budget, settings, user_text)
            if outcome.get("status") in ("unavailable", "error"):
                return outcome
            council_text = str(outcome.get("council_text") or "")
            decision = outcome.get("decision") or {}
            messages = outcome.get("history") or messages
        system = system_prompt()
        if note.strip():
            system = "%s\n\n%s" % (system, note.strip())
        result = self._loop(messages, cfg, [], budget=budget, system=system)
        if council_text:
            result["text"] = "%s\n\n%s" % (council_text, result.get("text") or "")
        if decision:
            result["council"] = decision
        return result

    def _role_turn(self, system: str, messages: list[dict[str, Any]],
                   tool_names: list[str], cfg: dict[str, Any], rounds: int,
                   budget: dict[str, int]) -> dict[str, Any]:
        """Один голос в совете: свой системный промпт, свои инструменты, свой предел."""
        tools = [tool for tool in self.tools if tool["name"] in (tool_names or [])]
        return self._loop(messages, cfg, [], system=system, tools=tools,
                          budget=budget, rounds=rounds)

    def _council(self, messages: list[dict[str, Any]], cfg: dict[str, Any],
                 budget: dict[str, int], settings: dict[str, Any],
                 task: str) -> dict[str, Any]:
        """Совет специалистов: отбор ролей → обсуждение → решение → исполнители.

        Если совет не сложился (модель недоступна, реестр не прочитан, решения
        нет) — работа продолжается **без** совета, и в чате это сказано прямо.
        """
        cohort = council.roles()
        if not cohort:
            return {"status": "done", "history": messages, "council_text": ""}
        limit = council.max_roles(settings)
        # ⚠️ Живой случай 25.11 (проба самоотладки, промт #129): бюджет был
        # общим, и совет съедал его целиком — на работу оставалось 4 шага, а
        # задача «найди дефект и почини» (поиск → чтение → ветка → правка →
        # проверки → коммит → PR) столько не занимает. Теперь у совета **свой**
        # предел (COUNCIL_ROUNDS), а работа получает весь MAX_ROUNDS: совет
        # обсуждает, а работу он не отменяет.
        initial = max(2, min(COUNCIL_ROUNDS, budget.get("left", MAX_ROUNDS)))
        used = {"left": initial}

        # 1) секретарь отбирает, кто нужен на задачу
        triage = self._role_turn(
            council.triage_prompt(task, cohort, limit),
            [{"role": "user", "content": task}], [], cfg, 1, used)
        if triage.get("status") != "done":
            return {"status": "done", "history": messages, "council_text": ""}
        wanted = council.parse_json(triage.get("text") or "").get("roles") or []
        selected: list[dict[str, Any]] = []
        for slug in wanted:
            item = council.role(str(slug))
            if item is None or str(item.get("kind")) == "executor":
                continue        # исполнителей назначает судья, а не секретарь
            if all(str(item.get("slug")) != str(slug) for item in selected):
                selected.append(item)
            if len(selected) >= limit:
                break

        # 2) обсуждение: каждый специалист — по своей роли и своими инструментами
        opinions: list[dict[str, Any]] = []
        for item in selected:
            turn = self._role_turn(
                council.role_prompt(item, task),
                [{"role": "user", "content": task}],
                item.get("tools") or [], cfg, council.ROLE_ROUNDS, used)
            if turn.get("status") == "unavailable":
                break
            opinions.append({"slug": item.get("slug"),
                             "title": item.get("title") or item.get("slug"),
                             "text": (turn.get("text") or "").strip()
                             [:council.ROLE_TEXT_LIMIT]})

        # 3) решение судьи
        judge = self._role_turn(
            council.judge_prompt(cohort),
            [{"role": "user", "content": council.judge_input(task, opinions)}],
            [], cfg, council.JUDGE_ROUNDS, used)
        decision = council.normalize_decision(
            council.parse_json(judge.get("text") or ""),
            [str(item.get("slug")) for item in council.executors()])
        if not decision.get("to_do") and not decision.get("executors"):
            return {"status": "done", "history": messages,
                    "council_text": "Совет специалистов: решение не сложилось — "
                                    "работаю без него."}

        # 4) исполнители: у каждого своя часть и свои инструменты
        outputs: list[dict[str, Any]] = []
        for slug in decision.get("executors") or []:
            item = council.role(slug)
            if item is None:
                continue
            turn = self._role_turn(
                council.executor_prompt(item, decision),
                [{"role": "user",
                  "content": council.executor_input(task, decision, outputs)}],
                item.get("tools") or [], cfg, council.EXECUTOR_ROUNDS, used)
            outputs.append({"slug": slug, "title": item.get("title") or slug,
                            "text": (turn.get("text") or "").strip()
                            [:council.ROLE_TEXT_LIMIT]})

        # ⚠️ Бюджет работы **не трогаем**: у совета свой предел (COUNCIL_ROUNDS),
        # и работа получает все MAX_ROUNDS (живой случай 25.11 — см. выше).
        context = council.context_block(decision, opinions, outputs)
        return {"status": "done",
                "history": list(messages) + [{"role": "user", "content": context}],
                "council_text": council.format_discussion(opinions, decision),
                "decision": decision}

    def _loop(self, messages: list[dict[str, Any]], cfg: dict[str, Any],
              calls: list[dict[str, Any]],
              system: str | None = None,
              tools: list[dict[str, Any]] | None = None,
              budget: dict[str, int] | None = None,
              rounds: int | None = None) -> dict[str, Any]:
        """Рабочий цикл: модель → инструменты → модель.

        `system` и `tools` позволяют прогнать голос совета (свой промпт и
        свой набор инструментов). `budget` — общий счётчик раундов задачи:
        совет и работа считаются вместе, поэтому лимит `MAX_ROUNDS` на задачу
        не превышается.
        """
        prompt = system if system is not None else system_prompt()
        payload = self._tool_payload(tools)
        allowed = {item["name"] for item in payload}
        limit = int(rounds or MAX_ROUNDS)
        for _ in range(limit):
            if budget is not None and int(budget.get("left", 1)) <= 0:
                return {"status": "done",
                        "text": "достигнут лимит шагов (%d) — сузьте запрос: "
                                "назови файл или конкретную функцию" % MAX_ROUNDS,
                        "calls": calls, "history": messages}
            if budget is not None:
                budget["left"] = int(budget.get("left", 1)) - 1
            try:
                raw = chat_completion(messages, prompt, payload, cfg)
            except ModelUnavailable as exc:
                return {"status": "unavailable", "error": str(exc),
                        "calls": calls, "text": ""}
            blocks, text, tool_calls = parse_message(raw)
            messages.append({"role": "assistant", "content": blocks})
            if not tool_calls:
                return {"status": "done", "text": text, "calls": calls,
                        "history": messages}
            # Сначала проверяем все вызовы: если среди них опасный — не
            # выполняем ничего и отдаём решение владельцу.
            dangerous = next((call for call in tool_calls
                              if (self._tool_by_name(call["name"]) or {}).get("dangerous")),
                             None)
            if dangerous is not None:
                tool = self._tool_by_name(dangerous["name"]) or {}
                self.pending = {
                    "tool": dangerous["name"],
                    "args": dangerous["input"],
                    "reason": tool.get("confirm_hint") or
                              "действие требует подтверждения владельца",
                    "call_id": dangerous["id"],
                    "messages": messages,
                    "cfg": cfg,
                    "calls": calls,
                }
                return {"status": "confirm", "pending": _pending_view(self.pending),
                        "calls": calls, "text": text, "history": messages}
            results = []
            for call in tool_calls:
                tool = self._tool_by_name(call["name"])
                if tool is None:
                    result: Any = {"error": "неизвестный инструмент %s" % call["name"]}
                elif tool["name"] not in allowed:
                    # Голос совета работает со своим набором инструментов:
                    # чужой инструмент он не вызывает (даже если выдумал его).
                    result = {"error": "инструмент %s в этом ходе недоступен"
                              % call["name"]}
                else:
                    try:
                        result = self.dispatch(tool["command"], call["input"])
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": str(exc)}
                calls.append({"tool": call["name"], "args": call["input"],
                              "result": _trim(result)})
                results.append((call, result))
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call["id"],
                 "content": json.dumps(_trim(result), ensure_ascii=False)}
                for call, result in results
            ]})
        return {"status": "done",
                "text": ("специалист не уложился в %d шагов — сузьте запрос"
                         % limit) if rounds else
                        ("достигнут лимит шагов (%d) — сузьте запрос: назни файл "
                         "или конкретную функцию" % MAX_ROUNDS),
                "calls": calls, "history": messages}

    def confirm(self, approved: bool, history: list[dict[str, Any]],
                area_slug: str = "minigen", model: str = "") -> dict[str, Any]:
        """Владелец подтвердил (или отклонил) отложенное действие."""
        pending = self.pending
        self.pending = None
        if not pending:
            return {"status": "error", "error": "нечего подтверждать", "calls": []}
        if not approved:
            return {"status": "done", "text": "действие отменено владельцем",
                    "calls": pending.get("calls") or [], "history": history}
        tool = self._tool_by_name(pending["tool"])
        if tool is None:
            return {"status": "error", "error": "инструмент исчез", "calls": []}
        try:
            result = self.dispatch(tool["command"], pending["args"])
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}
        call = {"tool": pending["tool"], "args": pending["args"],
                "result": _trim(result), "approved": True}
        calls = list(pending.get("calls") or []) + [call]
        messages = list(pending.get("messages") or history)
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": pending.get("call_id") or pending["tool"],
             "content": json.dumps(_trim(result), ensure_ascii=False)}
        ]})
        messages.append({"role": "user",
                         "content": "владелец подтвердил действие, продолжай."})
        cfg = pending.get("cfg") or self._model_config(area_slug, model)
        return self._loop(messages, cfg, calls)


def _pending_view(pending: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": pending.get("tool"),
        "args": pending.get("args") or {},
        "reason": pending.get("reason") or "",
        "call_id": pending.get("call_id") or "",
    }


def _trim(value: Any, limit: int = TOOL_RESULT_LIMIT) -> Any:
    """Обрезать ответ инструмента: в промпт уходит не всё."""
    if isinstance(value, str):
        return value[:limit]
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}
