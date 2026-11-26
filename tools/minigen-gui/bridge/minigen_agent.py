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
#: Бюджет совета — **часть бюджета задачи**, а не отдельный счёт.
#:
#: ⚠️ Живой случай 25.11 (проба самоотладки): бюджет был общий, и совет съедал
#: его целиком — на работу оставалось 4 шага, а задача «найди дефект и почини»
#: (поиск → чтение → ветка → правка → проверки → коммит → PR) столько не
#: занимает: проба остановилась на «достигнут лимит шагов» и PR не открыла.
#:
#: Теперь: совет занимает не больше `COUNCIL_ROUNDS`, работа получает остаток,
#: но не меньше `WORK_FLOOR`. Итог — раундов на задачу **не больше
#: `MAX_ROUNDS`** (совет + работа вместе), и работа никогда не остаётся без
#: шагов. Это и есть приёмка промта #132: «число раундов на задачу не выходит
#: за `MAX_ROUNDS`».
#:
#: ⚠️ Числа — из живых проб 26.11 (см. `docs/SESSION_HANDOFF.md`): работа по
#: задаче «объясни, как устроен совет» без совета занимает **12** раундов, а
#: полный совет (3 голоса + 3 исполнителя + судья + секретарь) — не меньше 14.
#: На 24 раундах обе части не помещаются ⇒ совет получает 12, работа — не
#: меньше 12. Голосов в бюджет совета помещается два (по два раунда: прочитать
#: и ответить) — поэтому секретарь зовёт не больше, чем бюджет выдерживает.
COUNCIL_ROUNDS = 12
#: Гарантированный запас работы: сколько шагов хода остаётся на работу, сколько
#: бы совет ни занял. Живой случай 25.11 оставил 4 — этого не хватало.
WORK_FLOOR = 12


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

    def _run_tool(self, call: dict[str, Any],
                  allowed: set[str]) -> Any:
        """Выполнить вызов инструмента и вернуть его результат.

        Ошибка — тоже результат: модели нужен ответ на **каждый** вызов, и
        ошибка честнее, чем «вызова не было» (вендор такой ход отвергает).
        """
        tool = self._tool_by_name(call["name"])
        if tool is None:
            return {"error": "неизвестный инструмент %s" % call["name"]}
        if tool["name"] not in allowed:
            # Голос совета работает со своим набором инструментов:
            # чужой инструмент он не вызывает (даже если выдумал его).
            return {"error": "инструмент %s в этом ходе недоступен" % call["name"]}
        try:
            return self.dispatch(tool["command"], call["input"])
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    # ── основной ход ──
    def ask(self, history: list[dict[str, Any]], user_text: str,
            area_slug: str = "minigen", model: str = "",
            note: str = "", blocks: list[dict[str, Any]] | None = None,
            council_note: str = "") -> dict[str, Any]:
        """Обработать реплику владельца. Возвращает ответ и ход работы.

        Новое задание сначала обсуждает **совет специалистов** (промт #132):
        секретарь отбирает роли → специалисты дают оценки → судья выносит
        решение и назначает исполнителей → основной ход работает по решению.
        Продолжения нитки («Продолжай») совет не зовёт — он по этой задаче уже
        отработал.

        ⚠️ Бюджет: совет и работа делят **один** бюджет задачи (`MAX_ROUNDS`).
        Совет занимает часть и оставляет работе гарантированный запас
        (`WORK_FLOOR`) — см. `COUNCIL_ROUNDS`.

        ⚠️ `note` — подсказка на **один ход** (сводка отчётов сторожей, заказ
        24.11). Она попадает в системную подсказку хода, а **не** в историю
        сообщений: отчёты сторожей — не реплики владельца, и в переписке им
        не место (промт #129). В историю попадают только слова владельца и
        ответы модели.

        ⚠️ `blocks` — файлы, приложенные оператором (промт #90): они уходят в
        сообщение **блоками** (текст документа или картинка), а не строкой.
        Протокол вендора блоки уже умеет — менять ничего не пришлось.

        ⚠️ `council_note` — то же содержимое **словом** для совета: роли совета
        видят свой контекст отдельно от основного хода, и без этого файла они
        бросались искать его по диску и не укладывались в шаги (живая проба
        25.11: «специалист не уложился в 5 шагов»).
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
            # ⚠️ Совет обсуждает **то же задание**, что и работа: с содержимым
            # приложенных файлов (живая проба 25.11 — без этого роли искали
            # файл по диску и не укладывались в шаги).
            council_task = user_text
            if council_note.strip():
                council_task = "%s\n\n%s" % (user_text, council_note.strip())
            outcome = self._council(messages, cfg, budget, settings, council_task)
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

        **Бюджет.** Совет занимает часть бюджета задачи (`COUNCIL_ROUNDS`), работа
        получает остаток, но не меньше `WORK_FLOOR` — итого раундов на задачу не
        больше `MAX_ROUNDS` (живой случай 25.11 — см. `COUNCIL_ROUNDS`).
        """
        cohort = council.roles()
        if not cohort:
            return {"status": "done", "history": messages, "council_text": ""}
        limit = council.max_roles(settings)
        task_left = int(budget.get("left", MAX_ROUNDS))
        # Сколько совет может занять: не больше COUNCIL_ROUNDS и не больше
        # того, что останется работе (WORK_FLOOR — гарантированный запас).
        cap = max(2, min(COUNCIL_ROUNDS, task_left - WORK_FLOOR))
        used = {"left": cap}

        # ⚠️ Сколько голосов бюджет совета выдерживает: секретарь не должен
        # звать больше, чем совет потом оплатит (иначе часть голосов не
        # говорит — и это видно владельцу в переписке).
        voices_pool = max(0, cap - council.JUDGE_ROUNDS - council.EXECUTOR_POOL - 1)
        limit = max(1, min(limit, voices_pool // council.MIN_VOICE_ROUNDS))

        def spent() -> int:
            """Сколько раундов занял совет к этому моменту."""
            return max(0, cap - used["left"])

        def give_back() -> None:
            """Отдать бюджету задачи то, что занял совет (и раундов не
            осталось лишних — работа получает остаток, но не меньше запаса)."""
            budget["left"] = max(WORK_FLOOR, task_left - spent())

        # 1) секретарь отбирает, кто нужен на задачу
        triage = self._role_turn(
            council.triage_prompt(task, cohort, limit),
            [{"role": "user", "content": task}], [], cfg, 1, used)
        if triage.get("status") != "done":
            give_back()
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
        #
        # ⚠️ Резерв под судью и исполнителей: без решения совета обсуждение
        # бессмысленно, и голоса не должны съесть весь бюджет совета. Резерв
        # откладывается **до** голосов, а доля голоса делится поровну между
        # оставшимися специалистами: один голос не съедает бюджет остальных.
        reserve = council.JUDGE_ROUNDS + council.EXECUTOR_POOL
        voices = {"left": voices_pool}
        opinions: list[dict[str, Any]] = []
        for index, item in enumerate(selected):
            if voices["left"] < council.MIN_VOICE_ROUNDS:
                break              # бюджет голосов исчерпан — совет без шума
            # Доля голоса: остаток поровну на оставшихся, но не меньше двух
            # раундов (голос должен успеть прочитать и ответить).
            share = max(council.MIN_VOICE_ROUNDS,
                        min(council.ROLE_ROUNDS,
                            voices["left"] // (len(selected) - index)))
            share = min(share, voices["left"])
            turn = self._role_turn(
                council.role_prompt(item, task),
                [{"role": "user", "content": task}],
                item.get("tools") or [], cfg, share, voices)
            if turn.get("status") == "unavailable":
                break
            opinions.append({"slug": item.get("slug"),
                             "title": item.get("title") or item.get("slug"),
                             "text": (turn.get("text") or "").strip()
                             [:council.ROLE_TEXT_LIMIT]})
        used["left"] = voices["left"] + reserve

        # 3) решение судьи
        judge = self._role_turn(
            council.judge_prompt(cohort),
            [{"role": "user", "content": council.judge_input(task, opinions)}],
            [], cfg, council.JUDGE_ROUNDS, used)
        decision = council.normalize_decision(
            council.parse_json(judge.get("text") or ""),
            [str(item.get("slug")) for item in council.executors()])
        if not decision.get("to_do") and not decision.get("executors"):
            give_back()
            return {"status": "done", "history": messages,
                    "council_text": "Совет специалистов: решение не сложилось — "
                                    "работаю без него."}

        # 4) исполнители: у каждого своя часть и свои инструменты
        outputs: list[dict[str, Any]] = []
        makers = {"left": min(council.EXECUTOR_POOL, used["left"])}
        wanted = list(decision.get("executors") or [])
        for index, slug in enumerate(wanted):
            if makers["left"] < council.MIN_VOICE_ROUNDS:
                break              # бюджет исполнителей исчерпан
            item = council.role(slug)
            if item is None:
                continue
            # Доля исполнителя — та же поровну (приёмка: работа делится между
            # исполнителями, а не делается одним — каждому нужны свои шаги).
            share = max(council.MIN_VOICE_ROUNDS,
                        min(council.EXECUTOR_ROUNDS,
                            makers["left"] // (len(wanted) - index)))
            share = min(share, makers["left"])
            before = makers["left"]
            turn = self._role_turn(
                council.executor_prompt(item, decision),
                [{"role": "user",
                  "content": council.executor_input(task, decision, outputs)}],
                item.get("tools") or [], cfg, share, makers)
            used["left"] -= before - makers["left"]
            outputs.append({"slug": slug, "title": item.get("title") or slug,
                            "text": (turn.get("text") or "").strip()
                            [:council.ROLE_TEXT_LIMIT]})

        # ⚠️ Бюджет задачи: совет занял часть, работа получает остаток — но не
        # меньше WORK_FLOOR. Итого раундов на задачу не больше MAX_ROUNDS.
        consumed = spent()
        give_back()
        context = council.context_block(decision, opinions, outputs)
        return {"status": "done",
                "history": list(messages) + [{"role": "user", "content": context}],
                "council_text": council.format_discussion(opinions, decision),
                "decision": decision,
                "rounds": consumed}

    def _loop(self, messages: list[dict[str, Any]], cfg: dict[str, Any],
              calls: list[dict[str, Any]],
              system: str | None = None,
              tools: list[dict[str, Any]] | None = None,
              budget: dict[str, int] | None = None,
              rounds: int | None = None) -> dict[str, Any]:
        """Рабочий цикл: модель → инструменты → модель.

        `system` и `tools` позволяют прогнать голос совета (свой промпт и
        свой набор инструментов). `budget` — счётчик раундов: у совета он
        свой (`_council` считает свой расход и уменьшает общий), у работы —
        общий. Поэтому раундов на задачу — не больше `MAX_ROUNDS`, а работа
        никогда не остаётся без шагов (`WORK_FLOOR`).
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
            # ⚠️ Протокол вендора (проверено живой пробой 26.11): на **каждый**
            # вызов модели в ходе должен прийти ответ (`tool_result`). Если
            # вызовов было два, а ответ один — вендор отвечает 400
            # «tool call and result not match (2013)», и ход умирает.
            # Опасные вызовы ждут владельца, поэтому ответ на них известен
            # не сразу ⇒ ход модели уходит вендору только после того, как
            # подтверждены (или отменены) **все** опасные вызовы.
            dangerous = [call for call in tool_calls
                         if (self._tool_by_name(call["name"]) or {}).get("dangerous")]
            if dangerous:
                # безопасные вызовы этого хода выполняем сейчас — они и не
                # требуют подтверждения; опасные ждут.
                results = []
                for call in tool_calls:
                    if any(call is item for item in dangerous):
                        continue
                    result = self._run_tool(call, allowed)
                    calls.append({"tool": call["name"], "args": call["input"],
                                  "result": _safe(result)})
                    results.append((call, result))
                tool = self._tool_by_name(dangerous[0]["name"]) or {}
                self.pending = {
                    "tool": dangerous[0]["name"],
                    "args": dangerous[0]["input"],
                    "reason": tool.get("confirm_hint") or
                              "действие требует подтверждения владельца",
                    "call_id": dangerous[0]["id"],
                    # ход модели целиком — его вернём вендору с ответами
                    "blocks": blocks,
                    # все опасные вызовы хода: подтверждаем по одному
                    "dangerous": dangerous,
                    # порядок вызовов модели: ответы на вызовы уходят вендору
                    # в том же порядке, в каком модель их вызвала
                    "tool_calls": tool_calls,
                    # результаты безопасных вызовов (уже выполнены)
                    "results": results,
                    # история **без** хода модели: ход вернём вместе с
                    # ответами на вызовы — иначе вендор увидит вызов без
                    # ответа и откажет.
                    "messages": list(messages[:-1]),
                    "cfg": cfg,
                    "calls": calls,
                }
                return {"status": "confirm", "pending": _pending_view(self.pending),
                        "calls": calls, "text": text, "history": messages}
            results = []
            for call in tool_calls:
                result = self._run_tool(call, allowed)
                calls.append({"tool": call["name"], "args": call["input"],
                              "result": _safe(result)})
                results.append((call, result))
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call["id"],
                 "content": json.dumps(_trim(result), ensure_ascii=False,
                                       default=str)}
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
        """Владелец подтвердил (или отклонил) отложенное действие.

        ⚠️ Опасных вызовов в ходе может быть несколько — подтверждаются по
        одному (каждый отдельный ответ владельца). Ход модели уходит вендору
        только когда подтверждены **все** опасные вызовы: на каждый вызов
        модели нужен ответ, иначе вендор отвечает 400 «tool call and result
        not match (2013)» (живая проба 26.11).

        ⚠️ Отмена — без хода модели: действие не сделано, ход вендору не
        уходит, владелец видит «действие отменено владельцем» и решает дальше
        (проверено тестом `test_dangerous_tool_can_be_declined`).
        """
        pending = self.pending
        if not pending:
            return {"status": "error", "error": "нечего подтверждать", "calls": []}
        queue = list(pending.get("dangerous") or [])
        if not queue:
            # отложенный вызов без очереди (старый вид) — подтверждаем один
            queue = [{"name": pending.get("tool"), "input": pending.get("args") or {},
                      "id": pending.get("call_id") or pending.get("tool")}]
        current = queue[0]
        rest = queue[1:]
        results = list(pending.get("results") or [])
        calls = list(pending.get("calls") or [])
        if approved:
            tool = self._tool_by_name(current["name"])
            if tool is None:
                result: Any = {"error": "инструмент исчез"}
            else:
                try:
                    result = self.dispatch(tool["command"], current["input"])
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc)}
            calls.append({"tool": current["name"], "args": current["input"],
                          "result": _safe(result), "approved": True})
        else:
            # Отмена — ход модели вендору не уходит (ответ на вызов не нужен,
            # если вызова не было). Опасные вызовы хода отменяются все.
            for item in queue:
                calls.append({"tool": item["name"], "args": item["input"],
                              "result": {"cancelled": True,
                                         "note": "владелец отменил действие"},
                              "approved": False})
            self.pending = None
            return {"status": "done", "text": "действие отменено владельцем",
                    "calls": calls, "history": history}
        results.append((current, result))
        if rest:
            # опасных вызовов было несколько — спрашиваем по одному.
            # ⚠️ Баннер в окне показывает **текущий** вызов: имя, аргументы и
            # причину берём у следующего в очереди, а не у первого.
            nxt = rest[0]
            nxt_tool = self._tool_by_name(nxt["name"]) or {}
            self.pending = {**pending, "dangerous": rest, "results": results,
                            "calls": calls,
                            "tool": nxt["name"], "args": nxt["input"],
                            "call_id": nxt["id"],
                            "reason": nxt_tool.get("confirm_hint") or
                                      pending.get("reason") or
                                      "действие требует подтверждения владельца"}
            return {"status": "confirm", "pending": _pending_view(self.pending),
                    "calls": calls,
                    "text": "ждёт подтверждения: %s" % self.pending["tool"],
                    "history": history}
        self.pending = None
        # Собираем сообщение для вендора: ход модели и ответ на **каждый**
        # его вызов (безопасные — уже выполнены, опасный — подтверждён).
        # ⚠️ Порядок ответов — как порядок вызовов модели (живая проба 26.11:
        # вендор сверяет вызов с ответом по порядку и по id).
        ordered = _ordered_results(pending.get("tool_calls") or [], results)
        messages = list(pending.get("messages") or history)
        messages.append({"role": "assistant", "content": pending.get("blocks") or []})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call["id"],
             "content": json.dumps(_trim(result), ensure_ascii=False, default=str)}
            for call, result in ordered
        ]})
        messages.append({"role": "user",
                         "content": "владелец подтвердил действие, продолжай."})
        cfg = pending.get("cfg") or self._model_config(area_slug, model)
        return self._loop(messages, cfg, calls)

    # ─────────────────────────────── ожидающее подтверждение (промт #129)

    def pending_view(self) -> dict[str, Any] | None:
        """Вид ожидающего подтверждения для окна (или None — ждать некого)."""
        if not self.pending:
            return None
        return _pending_view(self.pending)

    def pending_snapshot(self) -> dict[str, Any] | None:
        """Снимок ожидающего подтверждения — для хранения на диске.

        ⚠️ **Конфиг запроса в снимок не попадает**: в нём токен вендора, а
        секрет на диске — это лишний след (D-038). При подтверждении конфиг
        соберётся заново: у области свой живой токен.

        ⚠️ Результаты безопасных вызовов обрезаются так же, как их видел
        вендор (`_trim`): продолжение хода после перезапуска должно уйти
        вендору тем же, что и до перезапуска.
        """
        if not self.pending:
            return None
        pending = self.pending
        results = []
        for item in pending.get("results") or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            call, result = item
            results.append([_call_view(call), _trim(result)])
        return {
            "tool": str(pending.get("tool") or ""),
            "args": pending.get("args") or {},
            "reason": str(pending.get("reason") or ""),
            "call_id": str(pending.get("call_id") or ""),
            "blocks": pending.get("blocks") or [],
            "dangerous": [_call_view(item)
                          for item in (pending.get("dangerous") or [])],
            "tool_calls": [_call_view(item)
                           for item in (pending.get("tool_calls") or [])],
            "results": results,
            "messages": _pending_messages(pending.get("messages") or []),
            "calls": list(pending.get("calls") or []),
        }

    def restore_pending(self, snapshot: dict[str, Any] | None) -> bool:
        """Восстановить ожидающее подтверждение из снимка (после перезапуска).

        ⚠️ **Ход модели до опасного вызова восстанавливается из снимка** —
        текстом. Без него продолжение после перезапуска окна начинается
        **слепо**: модель не помнит, что уже прочитала и узнала (файлы,
        состояние области) и продолжает задачу вслепую (живая проба 26.11:
        после перезапуска модель всё перечитывала заново). Видимая переписка
        (`minigen_chat_store`) хранит только слова — ходы модели и результаты
        её вызовов живут здесь.

        ⚠️ **Вложения не переживают перезапуск** (картинка, документ): в
        снимке остаётся текст сообщения, тяжёлые блоки — нет. Это честно, а
        не тихо.

        ⚠️ Снимок без очереди опасных вызовов — не подтверждение: значит,
        подтверждать нечего, и мы честно его не восстанавливаем.
        """
        if not snapshot:
            self.pending = None
            return False
        queue = []
        for item in snapshot.get("dangerous") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            queue.append({"name": str(item.get("name") or ""),
                          "input": item.get("input") or {},
                          "id": item.get("id")})
        if not queue:
            self.pending = None
            return False
        results = []
        for item in snapshot.get("results") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                results.append((item[0], item[1]))
        self.pending = {
            "tool": str(snapshot.get("tool") or queue[0].get("name") or ""),
            "args": snapshot.get("args") or {},
            "reason": str(snapshot.get("reason") or ""),
            "call_id": str(snapshot.get("call_id") or queue[0].get("id") or ""),
            "blocks": snapshot.get("blocks") or [],
            "dangerous": queue,
            "tool_calls": list(snapshot.get("tool_calls") or []),
            "results": results,
            # ход модели до опасного вызова — из снимка (см. выше); если его
            # нет (старый снимок) — мост отдаст подтверждению историю чата
            "messages": _pending_messages(snapshot.get("messages") or []),
            "cfg": None,         # конфиг соберётся при подтверждении
            "calls": list(snapshot.get("calls") or []),
        }
        return True


def _pending_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ход модели до опасного вызова — текстом (для снимка и восстановления).

    ⚠️ Остаётся **текст**, вызовы модели и ответы на них: без них
    продолжение после перезапуска окна начинается слепой — модель не знает,
    что уже нашла.

    ⚠️ **Вызов и его ответ — всегда вместе.** Вендор сверяет вызов с ответом
    по id и отвергает ход с 400 «tool result's tool id not found» (живая
    проба 26.11). Поэтому, если ответ на вызов остаётся, остаётся и сам
    вызов: выбрасывать только лишь одно из двух нельзя.

    ⚠️ **Вложения отбрасываются** (картинка, документ): они тяжёлые и не
    переживают перезапуск — об этом владельцу сказано прямо.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if isinstance(content, str):
            if content:
                out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            if kind == "text":
                text = str(block.get("text") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif kind == "tool_use":
                # вызов модели: нужен его ответ — значит, нужен и он
                blocks.append({"type": "tool_use",
                               "id": str(block.get("id") or ""),
                               "name": str(block.get("name") or ""),
                               "input": block.get("input") if
                               isinstance(block.get("input"), (dict, list))
                               else {}})
            elif kind == "tool_result":
                blocks.append({"type": "tool_result",
                               "tool_use_id": str(block.get("tool_use_id") or ""),
                               "content": str(block.get("content") or "")})
        if blocks:
            out.append({"role": role, "content": blocks})
    return out


def _call_view(call: Any) -> dict[str, Any]:
    """Вызов модели — в виде, который можно положить в JSON и в снимок.

    ⚠️ Только то, что нужно для ответа вендору: имя вызова, его вход и id.
    Вход должен быть JSON — если нет, он становится строкой.
    """
    if not isinstance(call, dict):
        return {"name": str(call or ""), "input": {}, "id": ""}
    raw = call.get("input")
    if raw is None:
        raw = call.get("args")
    try:
        json.dumps(raw)
    except (TypeError, ValueError):
        raw = str(raw)
    return {"name": str(call.get("name") or call.get("tool") or ""),
            "input": raw if raw is not None else {},
            "id": str(call.get("id") or call.get("call_id") or "")}


def _pending_view(pending: dict[str, Any]) -> dict[str, Any]:
    """Вид ожидающего подтверждения для окна.

    ⚠️ Показывается **текущий** вызов очереди (первый), а не первый попавшийся.
    """
    queue = list(pending.get("dangerous") or [])
    current = queue[0] if queue else {}
    return {
        "tool": current.get("name") or pending.get("tool"),
        "args": current.get("input") if current.get("input") is not None
                else (pending.get("args") or {}),
        "reason": pending.get("reason") or "",
        "call_id": current.get("id") or pending.get("call_id") or "",
    }


def _ordered_results(tool_calls: list[dict[str, Any]],
                     results: list[tuple[dict[str, Any], Any]]
                     ) -> list[tuple[dict[str, Any], Any]]:
    """Ответы на вызовы — в том же порядке, в каком модель их вызвала.

    ⚠️ Вендор сверяет вызов с ответом (живая проба 26.11: «tool call and result
    not match (2013)»). Безопасные вызовы выполняются раньше опасных, поэтому
    порядок по умолчанию может не совпасть с порядком вызовов модели — здесь он
    приводится к правильному.
    """
    if not tool_calls:
        return list(results)
    by_id: dict[Any, Any] = {}
    for call, result in results:
        by_id.setdefault(call.get("id"), (call, result))
    ordered: list[tuple[dict[str, Any], Any]] = []
    taken: set[Any] = set()
    for call in tool_calls:
        key = call.get("id")
        if key in by_id and key not in taken:
            ordered.append(by_id[key])
            taken.add(key)
    for call, result in results:
        if call.get("id") not in taken:
            ordered.append((call, result))
    return ordered


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


def _safe(value: Any) -> Any:
    """Значение, которое можно положить в JSON без потери формы.

    ⚠️ Результат инструмента владелец видит **целиком** (промт #129: подробности
    не выбрасываются), а обрезка живёт только в контексте модели. Поэтому здесь
    значение приводится к JSON-безопасному виду: несериализуемое — строкой,
    структура — сохраняется.
    """
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return str(value)
