"""Тесты совета специалистов «МиниГена» (заказ владельца 25.11, промт #132).

Запуск:  python -m unittest discover -s bridge/tests -t . -v

Что проверяем (по критериям приёмки из промта):

1. **В репозитории лежат 5–6 ролей** с описанием, что оценивает каждая, и
   реестр, из которого они читаются.
2. **Задание проходит обсуждение советом** и заканчивается решением с
   назначенными исполнителями.
3. **Решение содержит**: что делать, что не делать, кто исполнитель, что
   требует уточнения у владельца.
4. **Работа распределяется между отдельными исполнителями**, а не делается
   одним (у каждого — свой ход и свои инструменты).
5. **Выход за рамки ролей**: совет это фиксирует и предлагает нового
   специалиста; новая роль появляется в реестре **только после решения
   владельца**.
6. **Число раундов на задачу не выходит за `MAX_ROUNDS`**, а на простой
   задаче обсуждение короткое.
7. **Термины разведены**: «вердикт» — состояние сессии, решение совета так не
   называется.

⚠️ Модель в тестах не нужна: голоса совета подменяются (`FakeAgent`), поэтому
проверяется логика конвейера, а не модель.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BRIDGE_DIR))

import minigen_agent as agent_mod    # noqa: E402
import minigen_council as council    # noqa: E402
import minigen_tools as tools_mod    # noqa: E402

TASK = "Сделай вкладку «Промты» в окне и проверь, что она открывается."

TRIAGE_JSON = json.dumps({
    "summary": "задание про вкладку",
    "roles": ["ux", "designer", "coder"],
    "why": "нужны UX и внешний вид",
}, ensure_ascii=False)

JUDGE_JSON = json.dumps({
    "to_do": ["сделать вкладку", "покрыть тестом"],
    "not_to_do": ["не переписывать мост"],
    "executors": ["coder", "reviewer", "presenter"],
    "ask_owner": [],
    "summary": "вкладка и её приёмка",
}, ensure_ascii=False)

JUDGE_WITH_NEW_ROLE = json.dumps({
    "to_do": ["проверить безопасность"],
    "not_to_do": [],
    "executors": ["coder"],
    "ask_owner": ["нужно ли хранить ключи в репозитории"],
    "new_role": {"title": "Специалист по безопасности",
                 "role": "оценивает секреты и доступы",
                 "why": "задача про ключи — своей роли нет"},
}, ensure_ascii=False)


class FakeAgent(agent_mod.Agent):
    """Агент с подменёнными голосами совета — модель не нужна.

    `cost` — сколько раундов «съедает» голос: по умолчанию один (голос
    ответил сразу), а `cost=99` — голос копает до конца своего предела.
    """

    def __init__(self, replies: dict[str, str], cost: int = 1) -> None:
        super().__init__(dispatch=lambda cmd, args: {}, tools=tools_mod.TOOLS)
        self.replies = replies
        self.cost = cost
        self.turns: list[dict] = []

    def _role_turn(self, system, messages, tool_names, cfg, rounds, budget):
        self.turns.append({"system": system, "tools": list(tool_names),
                           "rounds": rounds})
        budget["left"] = max(0, int(budget.get("left", 1))
                             - min(rounds, self.cost))
        text = ""
        for marker, value in self.replies.items():
            if marker in system:
                text = value
                break
        return {"status": "done", "text": text, "calls": [], "history": messages}


def council_replies(judge: str = JUDGE_JSON) -> dict[str, str]:
    return {
        "секретарь": TRIAGE_JSON,
        "судья": judge,
        "UX": "Кнопки лучше собрать в одну группу.",
        "Дизайнер": "Цвета совпадают с остальным окном.",
    }


# ───────────────────────────────────────────────────────── роли и реестр

class RolesTest(unittest.TestCase):
    """В репозитории программы лежат роли совета и реестр."""

    def test_registry_has_five_or_six_roles(self) -> None:
        roles = council.roles()
        self.assertGreaterEqual(len(roles), 5, "в совете должно быть 5–6 ролей")
        self.assertLessEqual(len(roles), 6, "в совете должно быть 5–6 ролей")

    def test_every_role_has_a_brief_and_criteria(self) -> None:
        for item in council.roles():
            slug = str(item.get("slug") or "")
            self.assertTrue(slug, "роль без slug")
            self.assertTrue(item.get("title"), "роль %s без названия" % slug)
            self.assertIn(str(item.get("kind")), ("advisor", "executor"))
            brief = council.brief_of(item)
            self.assertTrue(brief.strip(), "у роли %s нет брифа" % slug)
            for section in ("Что оценивает", "Критерии",
                            "Когда мнение обязательно"):
                self.assertIn(section, brief,
                              "в брифе роли %s нет раздела «%s»" % (slug, section))

    def test_executors_are_separate_from_advisors(self) -> None:
        """Исполнители — отдельные от советников (код, проверка, презентация)."""
        executors = [str(item.get("slug")) for item in council.executors()]
        self.assertIn("coder", executors)
        self.assertIn("reviewer", executors)
        self.assertIn("presenter", executors)
        advisors = [str(item.get("slug")) for item in council.roles()
                    if str(item.get("kind")) == "advisor"]
        for slug in executors:
            self.assertNotIn(slug, advisors)

    def test_roles_limit_is_bounded(self) -> None:
        self.assertEqual(council.max_roles({}), council.DEFAULT_MAX_ROLES)
        self.assertLessEqual(council.max_roles({"councilMaxRoles": 99}), 6)
        self.assertGreaterEqual(council.max_roles({"councilMaxRoles": 0}), 1)

    def test_default_roles_match_the_program_setting(self) -> None:
        """Дефолт ролей в модуле и в настройках программы — один и тот же.

        ⚠️ Было: в модуле 2, в настройках 3 — и доктринг модуля обещал «две»,
        а программа отвечала «три». Владелец читал одно, программа делала
        другое.
        """
        import minigen_state as state
        self.assertEqual(council.DEFAULT_MAX_ROLES,
                         int(state.DEFAULT_SETTINGS.get("councilMaxRoles") or 0))


# ────────────────────────────────────────────────────── формат решения

class DecisionTest(unittest.TestCase):
    """Решение совета — четыре поля, исполнители из известных ролей."""

    def test_decision_has_four_fields(self) -> None:
        decision = council.normalize_decision(
            json.loads(JUDGE_JSON), ["coder", "reviewer", "presenter"])
        for field in council.DECISION_FIELDS:
            self.assertIn(field, decision)
        self.assertEqual(decision["to_do"], ["сделать вкладку", "покрыть тестом"])
        self.assertEqual(decision["not_to_do"], ["не переписывать мост"])
        self.assertEqual(decision["executors"], ["coder", "reviewer", "presenter"])
        self.assertEqual(decision["ask_owner"], [])

    def test_unknown_executor_is_dropped(self) -> None:
        raw = {"to_do": ["x"], "executors": ["выдуманный", "coder"]}
        decision = council.normalize_decision(raw, ["coder", "reviewer"])
        self.assertEqual(decision["executors"], ["coder"])

    def test_new_role_is_a_proposal(self) -> None:
        raw = json.loads(JUDGE_WITH_NEW_ROLE)
        decision = council.normalize_decision(raw, ["coder"])
        self.assertIsNotNone(decision["new_role"])
        self.assertEqual(decision["new_role"]["title"],
                         "Специалист по безопасности")
        self.assertEqual(decision["ask_owner"],
                         ["нужно ли хранить ключи в репозитории"])

    def test_decision_is_not_called_a_verdict(self) -> None:
        """«Вердикт» — состояние сессии; решение совета так не называется."""
        text = council.format_discussion(
            [{"slug": "ux", "title": "UX", "text": "кнопки в одну группу"}],
            council.normalize_decision(json.loads(JUDGE_JSON),
                                       ["coder", "reviewer", "presenter"]))
        self.assertIn("Решение совета", text)
        self.assertNotIn("вердикт", text.lower())


# ─────────────────────────────────────────────────────────── конвейер

class PipelineTest(unittest.TestCase):
    """Задание проходит обсуждение и заканчивается решением."""

    def _run(self, replies: dict[str, str]) -> tuple[dict, FakeAgent]:
        fake = FakeAgent(replies)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = fake._council([{"role": "user", "content": TASK}], {},
                                budget, {}, TASK)
        return outcome, fake

    def test_task_goes_through_discussion_to_decision(self) -> None:
        outcome, fake = self._run(council_replies())
        decision = outcome.get("decision") or {}
        self.assertEqual(decision.get("executors"), ["coder", "reviewer", "presenter"])
        self.assertIn("Решение совета", outcome.get("council_text") or "")
        self.assertIn("UX", outcome.get("council_text") or "")
        # решения идут в контекст основного хода агента
        context = outcome["history"][-1]["content"]
        self.assertIn("Что делать", context)
        self.assertIn("Кто исполнитель", context)

    def test_executors_work_separately_with_their_own_tools(self) -> None:
        """Работа распределяется между отдельными исполнителями."""
        outcome, fake = self._run(council_replies())
        executor_turns = [turn for turn in fake.turns
                          if "ты — исполнитель" in turn["system"].lower()]
        self.assertEqual(len(executor_turns), 3, "исполнителей должно быть три")
        tool_sets = [set(turn["tools"]) for turn in executor_turns]
        self.assertNotEqual(tool_sets[0], tool_sets[1],
                            "у исполнителей должны быть свои инструменты")
        self.assertIn("code.tests", tool_sets[1])   # у проверяющего есть проверки

    def test_advisors_are_selected_by_the_secretary_and_capped(self) -> None:
        outcome, fake = self._run(council_replies())
        advisor_turns = [turn for turn in fake.turns
                         if "ты — специалист совета" in turn["system"].lower()]
        # исполнителя секретарь не зовёт — его назначает судья
        self.assertTrue(advisor_turns, "совет должен кого-то позвать")
        for turn in advisor_turns:
            self.assertNotIn("исполнитель", turn["system"].lower())
        # сколько голосов прозвучит — решает бюджет совета, а не фантазия
        self.assertLessEqual(len(advisor_turns), council.max_roles({}))

    def test_a_bigger_budget_gives_more_voices(self) -> None:
        """Лестница владельца: больше раундов у совета — больше голосов.

        ⚠️ Раундов на голос нужно минимум четыре (живая проба 26.11: при двух
        голос успевал только читать, а ответ не прозвучал — мнение пустое).
        Поэтому при дефолтном бюджете (12) прозвучит **один** голос, а при
        20 раундах совета и запасе работы 4 — **два** (второго не даёт
        подборка: секретарь зовёт двух советников, третий — исполнитель).
        """

        def voices(settings: dict) -> int:
            greedy = FakeAgent(council_replies(), cost=99)
            budget = {"left": agent_mod.MAX_ROUNDS}
            greedy._council([{"role": "user", "content": TASK}], {}, budget,
                            settings, TASK)
            return len([turn for turn in greedy.turns
                        if "ты — специалист совета" in turn["system"].lower()])

        self.assertEqual(voices({}), 1)
        self.assertEqual(voices({"councilRounds": 8,
                                 "councilWorkFloor": 12}), 1)
        self.assertEqual(voices({"councilRounds": 20,
                                 "councilWorkFloor": 4}), 2)

    def test_rounds_stay_within_the_limit(self) -> None:
        """Число раундов на задачу не выходит за MAX_ROUNDS."""
        fake = FakeAgent(council_replies())
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = fake._council([{"role": "user", "content": TASK}], {},
                                budget, {}, TASK)
        spent = int(outcome.get("rounds") or 0)
        # совет + работа — вместе не больше предела задачи
        self.assertLessEqual(spent + budget["left"], agent_mod.MAX_ROUNDS)
        self.assertLessEqual(spent, agent_mod.COUNCIL_ROUNDS)
        self.assertGreaterEqual(budget["left"], agent_mod.WORK_FLOOR,
                                "работе должен остаться запас шагов")

    def test_council_takes_its_share_and_work_keeps_its_floor(self) -> None:
        """Совет занимает часть бюджета задачи, работа — не меньше WORK_FLOOR.

        ⚠️ Живой случай 25.11 (проба самоотладки, промт #129): бюджет был
        общий, и совет съедал его целиком — на работу оставалось 4 шага, а
        задача «найди дефект и почини» столько не занимает: проба остановилась
        на «достигнут лимит шагов» и PR не открыла. Теперь совет занимает
        часть, работа — гарантированный запас.
        """
        fake = FakeAgent(council_replies())
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = fake._council([{"role": "user", "content": TASK}], {},
                                budget, {}, TASK)
        spent = int(outcome.get("rounds") or 0)
        self.assertGreater(spent, 0, "совет должен занять часть бюджета")
        self.assertLessEqual(spent, agent_mod.COUNCIL_ROUNDS)
        self.assertGreaterEqual(budget["left"], agent_mod.WORK_FLOOR)
        self.assertLessEqual(spent + budget["left"], agent_mod.MAX_ROUNDS)
        self.assertLessEqual(agent_mod.COUNCIL_ROUNDS + agent_mod.WORK_FLOOR,
                             agent_mod.MAX_ROUNDS)

    def test_greedy_voices_do_not_starve_the_judge(self) -> None:
        """Голоса не съедают бюджет совета: решение выносится всегда.

        ⚠️ Без резерва под судью голоса забирали всё, и совет заканчивался
        без решения — обсуждение было потрачено впустую (план промта #132:
        «задание проходит весь конвейер и выдаёт решение»).
        """
        greedy = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = greedy._council([{"role": "user", "content": TASK}], {},
                                  budget, {}, TASK)
        decision = outcome.get("decision") or {}
        self.assertTrue(decision.get("to_do"), "решение должно быть")
        self.assertEqual(decision.get("executors"),
                         ["coder", "reviewer", "presenter"])
        judge_turns = [turn for turn in greedy.turns
                       if "ты — судья" in turn["system"].lower()]
        self.assertEqual(len(judge_turns), 1, "судья должен высказаться")
        self.assertGreaterEqual(budget["left"], agent_mod.WORK_FLOOR)

    def test_every_executor_gets_its_share(self) -> None:
        """Работа делится между исполнителями, а не делается одним."""
        greedy = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        greedy._council([{"role": "user", "content": TASK}], {}, budget, {},
                        TASK)
        executor_turns = [turn for turn in greedy.turns
                          if "ты — исполнитель" in turn["system"].lower()]
        self.assertEqual(len(executor_turns), 3,
                         "все три исполнителя должны получить свою часть")
        for turn in executor_turns:
            self.assertGreaterEqual(turn["rounds"], council.MIN_EXECUTOR_ROUNDS,
                                    "исполнитель должен успеть ответить")

    def test_a_council_without_a_decision_gives_its_rounds_back(self) -> None:
        """Совет без решения — работа всё равно получает бюджет задачи."""
        empty = json.dumps({"to_do": [], "not_to_do": [], "executors": [],
                             "ask_owner": []}, ensure_ascii=False)
        greedy = FakeAgent(council_replies(judge=empty), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = greedy._council([{"role": "user", "content": TASK}], {},
                                  budget, {}, TASK)
        self.assertNotIn("Решение совета", outcome.get("council_text") or "")
        self.assertGreaterEqual(budget["left"], agent_mod.WORK_FLOOR)
        self.assertLessEqual(budget["left"], agent_mod.MAX_ROUNDS)

    def test_the_secretary_calls_no_more_voices_than_the_budget_affords(self) -> None:
        """Секретарь не зовёт больше голосов, чем бюджет совета оплатит.

        ⚠️ Иначе часть голосов остаётся без слова («не уложился в 2 шагов») —
        и это видно владельцу в переписке: совет вроде обсуждал, а говорил
        один.
        """
        greedy = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        greedy._council([{"role": "user", "content": TASK}], {}, budget, {},
                        TASK)
        advisor_turns = [turn for turn in greedy.turns
                         if "ты — специалист совета" in turn["system"].lower()]
        self.assertTrue(advisor_turns, "совет должен кого-то позвать")
        for turn in advisor_turns:
            self.assertGreaterEqual(turn["rounds"], council.MIN_VOICE_ROUNDS,
                                    "голос должен успеть прочитать и ответить")

    def test_council_can_be_switched_off(self) -> None:
        self.assertTrue(council.enabled({}))
        self.assertFalse(council.enabled({"councilEnabled": False}))

    def test_continuation_is_not_a_new_task(self) -> None:
        """«Продолжай» — это продолжение нитки, совету не нужно."""
        self.assertTrue(council.is_continuation("Продолжай"))
        self.assertTrue(council.is_continuation("да"))
        self.assertFalse(council.is_continuation(TASK))


class CouncilFileTaskTest(unittest.TestCase):
    """Задание с приложенным файлом: совет должен видеть содержимое.

    ⚠️ Живой случай 25.11 (проба промта #90): роли совета живут в своём
    контексте и файла оператора не видели — «Супер-программист» бросался искать
    его по диску и не укладывался в шаги («специалист не уложился в 5 шагов»).
    Теперь содержимое файла приходит совету словом.
    """

    def test_the_council_receives_the_attached_file(self) -> None:
        fake = FakeAgent(council_replies())
        captured: dict[str, str] = {}

        def fake_council(messages, cfg, budget, settings, task):
            captured["task"] = task
            return {"status": "done", "history": messages,
                    "council_text": "", "decision": {}}

        fake._council = fake_council
        fake.settings_provider = lambda: {"councilEnabled": True}
        fake._model_config = lambda area_slug, model="": {"model": "тест"}
        fake._loop = lambda messages, cfg, calls, **kwargs: {
            "status": "done", "text": "ок", "calls": [], "history": messages}
        fake.ask([], "Сколько выключателей на щитке?", "minigen", "",
                 council_note="Файл «щиток.txt» (текст, 27 Б):\n"
                             "На щитке 17 выключателей.")
        self.assertIn("17 выключателей", captured.get("task", ""))
        self.assertIn("щиток.txt", captured.get("task", ""))

    def test_without_a_file_the_task_is_unchanged(self) -> None:
        fake = FakeAgent(council_replies())
        captured: dict[str, str] = {}

        def fake_council(messages, cfg, budget, settings, task):
            captured["task"] = task
            return {"status": "done", "history": messages,
                    "council_text": "", "decision": {}}

        fake._council = fake_council
        fake.settings_provider = lambda: {"councilEnabled": True}
        fake._model_config = lambda area_slug, model="": {"model": "тест"}
        fake._loop = lambda messages, cfg, calls, **kwargs: {
            "status": "done", "text": "ок", "calls": [], "history": messages}
        fake.ask([], TASK, "minigen", "")
        self.assertEqual(captured.get("task"), TASK)


# ──────────────────────────────────────────────── бюджет совета (настройка)

class BudgetSettingsTest(unittest.TestCase):
    """Бюджет совета — настройка владельца, а не число в коде.

    ⚠️ Риск промта #132, названный владельцем: «шесть специалистов × раунды =
    долго и дорого. Нужен предел (сколько ролей реально отвечает на задачу,
    сколько раундов) — числа предлагает исполнитель, решение за владельцем».
    Числа из живых проб 26.11 — дефолт; решение владельца — в настройках окна.
    """

    def test_default_budget_matches_the_program_setting(self) -> None:
        """Дефолты модуля и настроек программы — одни и те же.

        ⚠️ Как с числом ролей (в модуле было «две», в настройках — «три»):
        источник правды должен быть один, иначе модуль и программа разойдутся
        и владелец будет читать одно, а программа делать другое.
        """
        import minigen_state as state
        self.assertEqual(council.DEFAULT_COUNCIL_ROUNDS,
                         int(state.DEFAULT_SETTINGS.get("councilRounds") or 0))
        self.assertEqual(council.DEFAULT_WORK_FLOOR,
                         int(state.DEFAULT_SETTINGS.get("councilWorkFloor") or 0))

    def test_owner_numbers_are_read(self) -> None:
        plan = council.budget({"councilRounds": 6, "councilWorkFloor": 18}, 24)
        self.assertEqual(plan["council"], 6)
        self.assertEqual(plan["work"], 18)

    def test_defaults_when_the_owner_did_not_touch_them(self) -> None:
        plan = council.budget({}, 24)
        self.assertEqual(plan["council"], council.DEFAULT_COUNCIL_ROUNDS)
        self.assertEqual(plan["work"], council.DEFAULT_WORK_FLOOR)

    def test_garbage_falls_back_to_the_defaults(self) -> None:
        """Мусор в настройке — не повод сломать ход: дефолт."""
        plan = council.budget({"councilRounds": "сколько?",
                               "councilWorkFloor": -5}, 24)
        self.assertEqual(plan["council"], council.DEFAULT_COUNCIL_ROUNDS)
        self.assertEqual(plan["work"], council.DEFAULT_WORK_FLOOR)

    def test_the_council_yields_to_the_work_floor(self) -> None:
        """«Совету 20, работе 12» в 24 раунда не влезает — уступает совет.

        ⚠️ Запас работы — это гарантия («работа никогда не остаётся без
        шагов»), а не пожелание: сначала режется аппетит совета.
        """
        plan = council.budget({"councilRounds": 20, "councilWorkFloor": 12}, 24)
        self.assertEqual(plan["council"], 12)
        self.assertEqual(plan["work"], 12)

    def test_the_budget_never_exceeds_the_task_cap(self) -> None:
        """Даже «совету 99 и работе 99» — раундов на задачу не больше 24."""
        plan = council.budget({"councilRounds": 99, "councilWorkFloor": 99}, 24)
        self.assertLessEqual(plan["council"] + plan["work"], plan["cap"])
        self.assertGreaterEqual(plan["council"], council.MIN_COUNCIL_ROUNDS)
        self.assertGreaterEqual(plan["work"], council.MIN_WORK_FLOOR)

    def test_the_cap_holds_even_for_a_tiny_task_budget(self) -> None:
        """Предел мал — совет и работа всё равно не выходят за него.

        ⚠️ Гарантия запаса работы при очень маленьком пределе уступает
        жёсткому лимиту: иначе не помещается ни совет, ни работа.
        """
        for cap in (2, 3, 4, 6, 8, 24):
            plan = council.budget({"councilRounds": 99,
                                   "councilWorkFloor": 99}, cap)
            self.assertLessEqual(plan["council"] + plan["work"], plan["cap"],
                                 "совет и работа не влезают в предел %d" % cap)
            self.assertGreaterEqual(plan["council"], council.MIN_COUNCIL_ROUNDS)

    def test_the_council_spends_no_more_than_the_owner_allowed(self) -> None:
        """Владелец урезал совет — совет занимает меньше, работа — не меньше."""
        greedy = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = greedy._council([{"role": "user", "content": TASK}], {},
                                  budget, {"councilRounds": 4,
                                           "councilWorkFloor": 12}, TASK)
        spent = int(outcome.get("rounds") or 0)
        self.assertLessEqual(spent, 4, "совет не должен превысить свою долю")
        self.assertGreaterEqual(budget["left"], 12,
                                "работе должен остаться запас шагов")

    def test_the_owner_can_give_the_council_more(self) -> None:
        """Владелец расширил совет — совет занимает больше, но не за счёт
        предела задачи: раундов на задачу всё равно не больше `MAX_ROUNDS`."""
        greedy = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = greedy._council([{"role": "user", "content": TASK}], {},
                                  budget, {"councilRounds": 20,
                                           "councilWorkFloor": 4}, TASK)
        spent = int(outcome.get("rounds") or 0)
        self.assertGreater(spent, agent_mod.COUNCIL_ROUNDS,
                           "совет должен занять больше прежнего дефолта")
        self.assertLessEqual(spent + budget["left"], agent_mod.MAX_ROUNDS)

    def test_a_small_budget_still_leaves_room_for_a_voice(self) -> None:
        """⚠️ Дыра, найденная живой пробой 26.11: при `councilRounds=8` весь
        бюджет совета уходил в резерв (судья + исполнители), и совет
        «обсуждал» в пустоту — ни одного голоса. Обсуждение и есть смысл
        совета ⇒ резерв уступает место хотя бы одному голосу."""
        fake = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = fake._council([{"role": "user", "content": TASK}], {},
                                budget, {"councilRounds": 8,
                                         "councilWorkFloor": 12}, TASK)
        advisor_turns = [turn for turn in fake.turns
                          if "ты — специалист совета" in turn["system"].lower()]
        self.assertTrue(advisor_turns, "совет должен хоть кого-то позвать")
        self.assertLessEqual(int(outcome.get("rounds") or 0), 8,
                             "совет не должен превысить свою долю")
        self.assertGreaterEqual(budget["left"], 12,
                                "работе должен остаться запас шагов")

    def test_the_specialist_knows_how_many_steps_it_has(self) -> None:
        """⚠️ Живая проба 26.11: голос с двумя раундами пытался прочитать
        четыре места и **не успел ответить** — его мнение в обсуждении было
        пустым («не уложился в 2 шагов»). Поэтому в промпте сказано прямо:
        столько-то шагов, прочитай одно место."""
        fake = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        fake._council([{"role": "user", "content": TASK}], {}, budget, {},
                      TASK)
        advisor_turns = [turn for turn in fake.turns
                         if "ты — специалист совета" in turn["system"].lower()]
        self.assertTrue(advisor_turns, "совет должен кого-то позвать")
        for turn in advisor_turns:
            self.assertIn("шагов", turn["system"])
            self.assertIn("одно", turn["system"])

    def test_the_council_still_calls_a_specialist_if_the_secretary_fails(self) -> None:
        """⚠️ Живая проба 26.11: секретарь назвал только исполнителя — совет
        остался без единого голоса («никого не позвали»), а решение вынесено.
        Совет без обсуждения — не совет ⇒ зовём первых советников реестра."""
        replies = council_replies()
        replies["секретарь"] = json.dumps({"roles": ["coder"]},
                                          ensure_ascii=False)
        greedy = FakeAgent(replies, cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = greedy._council([{"role": "user", "content": TASK}], {},
                                  budget, {}, TASK)
        advisor_turns = [turn for turn in greedy.turns
                         if "ты — специалист совета" in turn["system"].lower()]
        self.assertTrue(advisor_turns, "совет должен кого-то позвать")
        self.assertNotIn("никого не позвали",
                         outcome.get("council_text") or "")

    def test_a_tiny_budget_does_not_break_the_round_limit(self) -> None:
        """⚠️ Дыра, найденная сверкой 26.11: урезанный совет выходил за свою
        долю — резерв под судью и исполнителей (7 раундов) не помещался в
        бюджет совета в 2 раунда, и раундов на задачу становилось больше
        `MAX_ROUNDS` (приёмка промта #132)."""
        greedy = FakeAgent(council_replies(), cost=99)
        budget = {"left": agent_mod.MAX_ROUNDS}
        outcome = greedy._council([{"role": "user", "content": TASK}], {},
                                  budget, {"councilRounds": 2,
                                           "councilWorkFloor": 24}, TASK)
        spent = int(outcome.get("rounds") or 0)
        self.assertLessEqual(spent + budget["left"], agent_mod.MAX_ROUNDS)
        self.assertLessEqual(spent, 2, "совет не должен превысить свою долю")
        self.assertGreaterEqual(budget["left"], council.MIN_WORK_FLOOR)


# ────────────────────────────────────────────────────── новый специалист

class NewSpecialistTest(unittest.TestCase):
    """Роль появляется в совете только после решения владельца."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._old_local = council.LOCAL_DIR
        council.LOCAL_DIR = Path(self.tmp.name) / "council"

    def tearDown(self) -> None:
        council.LOCAL_DIR = self._old_local
        self.tmp.cleanup()

    def test_role_is_added_only_after_the_owner_agrees(self) -> None:
        before = len(council.roles())
        res = council.add_role("security", "Специалист по безопасности",
                               "оценивает секреты и доступы", "нужна роль")
        self.assertTrue(res["ok"], res)
        after = council.roles()
        self.assertEqual(len(after), before + 1)
        added = next(item for item in after if item.get("slug") == "security")
        self.assertIn("безопасност", (council.brief_of(added) or "").lower())
        # в репозиторий роль не попала — репозиторий правит человек
        repo = json.loads(council.REGISTRY_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("security", [str(item.get("slug"))
                                       for item in repo.get("roles") or []])

    def test_bad_slug_is_refused(self) -> None:
        res = council.add_role("Плохое Имя", "Роль", "роль")
        self.assertFalse(res["ok"])
        self.assertIn("slug", res["error"])


if __name__ == "__main__":
    unittest.main()
