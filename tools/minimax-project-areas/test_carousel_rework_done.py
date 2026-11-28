# -*- coding: utf-8 -*-
"""Приёмка: доработка промта не берётся заново, если новый PR уже слит (живой случай 28.11).

Живой случай, который чинится (28.11.2026, полоса 1 «Матрица РМЗ», промт #176):
карусель **взяла доработку промта второй раз** (14:34:51 в журнале
`matrica-carousel.log`), хотя новый слитый PR уже был (PR #2163 слит в 14:40,
доработка закрыта в 14:40:11) ⇒ в дереве `D:\\valstan\\REPO\\MatricaRMZ`
оказались **две сессии на один промт** — «два писателя на одном дереве» (ADR-0011).

Механика дефекта — в выборе промта из очереди (`tick()`): проверка «уже
выполнен?» для промтов-доработок **пропускалась целиком** («старый PR не
зачёт» ⇒ условие не срабатывает никогда), поэтому доработка считалась
невыполненной всегда, пока её не закрывал путь завершения сессии.

Что проверяем (критерии приёмки):

1. **Доработка, чей новый PR уже слит, не берётся заново** — промт пропускается
   (уходит в «выполненные»), новая сессия **не поднимается**.
2. **Порог `after_pr` уважается**: старый (уже слитый до доработки) PR зачётом
   **не** считается — доработка, у которой только старый PR, берётся (иначе
   правка закрыла бы дорогу к самой доработке).
3. **Лог объясняет, ПОЧЕМУ промт пропущен** (номер нового PR и порог) — иначе
   в журнале это выглядит как «промт пропал» (источник путаницы 28.11).
4. **Обычный промт (не доработка) по-прежнему пропускается** по архиву КАРМАНа
   или своему слитому PR — правка не сломала прежнее поведение.
5. **Временная правка**: без проверки «выполнено ли доработка» приёмка падает —
   значит, проверка проходит по делу, а не по неверной причине.

Проверка идёт по **настоящему коду каркаса** (`tick()` из репозитория) —
внешние источники правды (сеть КАРМАНа, `gh`, запуск сессии) подменены.

Запуск: python3 tools/minimax-project-areas/test_carousel_rework_done.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import time as time_module

HERE = pathlib.Path(__file__).resolve().parent
CAROUSEL = HERE / "carousel" / "matrica-night-carousel.py"

#: Промт-доработка из живого случая 28.11 (полоса 1 «Матрица РМЗ»).
PROMPT_ID = 176
#: Порог доработки: номер PR на момент начала доработки (старый PR не зачёт).
AFTER_PR = 2157
#: Новый слитый PR, закрывший доработку (живой случай: #2163).
NEW_PR = 2163

#: Куски кода, которые временная правка возвращает в прежний вид.
#: ⚠️ Правка должна быть ТОЧНОЙ копией прежнего кода — иначе проверка
#: «сломаная копия ведёт себя по-старому» проходит по неверной причине.
BROKEN_REPLACEMENT = (
    '            rework_rec = rework_all.get(str(pick)) or {}\n'
    '            ok, why = (False, "") if rework_rec else verify_prompt(pick)\n'
    '            if ok:')


def load_carousel(tmp: pathlib.Path, broken: str = ""):
    """Каркас карусели — из репозитория (источник истины), с временным каталогом.

    `broken` — «временная правка»: что вернуть в прежний вид, чтобы проверить,
    что приёмка **падает** без этой защиты.
    """
    source = CAROUSEL.read_text(encoding="utf-8-sig")
    original = source
    if broken == "rework":
        # Возвращаем ДЕФЕКТ: проверка «уже выполнен» для доработок
        # пропускается целиком (как было 28.11).
        source = source.replace(
            '            rework_rec = rework_all.get(str(pick)) or {}\n'
            '            ok, why = verify_prompt(pick, min_number=int(rework_rec.get("after_pr") or 0),\n'
            '                                     rework=bool(rework_rec))\n'
            '            if ok:',
            BROKEN_REPLACEMENT)
    if broken:
        # Сломанная копия лежит во временном каталоге ⇒ путь к каталогу
        # инструментов репозитория указываем явно (иначе каркас не найдёт
        # сторожа рядом с собой).
        source = source.replace(
            "REPO_TOOLS = Path(__file__).resolve().parent.parent",
            "REPO_TOOLS = Path(%r)" % str(HERE))
    if broken and source == original:
        raise SystemExit("временная правка «%s» не применилась — код поменялся, "
                         "приёмку надо поправить" % broken)
    path = CAROUSEL
    if broken:
        path = pathlib.Path(tempfile.gettempdir()) / ("carousel-rework-broken-%s.py" % broken)
        path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "carousel_rework%s" % (("_" + broken) if broken else ""), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOCAL = tmp
    mod.LOG = tmp / "carousel.log"
    mod.STATE = tmp / "carousel-state.json"
    return mod


def prepare(car, tmp: pathlib.Path, *, merged_pr: int, archived: bool = False):
    """Подменяем внешние источники правды (сеть КАРМАНа, `gh`, запуск сессии).

    Всё, что касается правки, остаётся НАСТОЯЩИМ: `tick()` выбирает промт и
    решает, брать его или пропустить, — вызывается из каркаса как есть.
    """
    lane = {"n": 1, "slug": "test", "title": "ТЕСТ", "folder": str(tmp), "queue": []}
    car.LANES = [lane]
    car.prompt_archived = lambda pid: archived
    car.merged_pr_for = lambda pid, min_number=0: (
        merged_pr if merged_pr > min_number else 0)
    car.karman_archive = lambda pid: True
    car.prompt_body = lambda pid: "текст промта"
    car.foreign_live_in_tree = lambda lane, ls: ""
    car.build_prompt = lambda lane, pid, body, retry=False, rework_note="": (
        "ЗАДАНИЕ — промт КАРМАНА #%d" % pid)
    car.refresh_queues = lambda st: None
    car.reap_stale_pending = lambda st, now: None
    car.lane_snapshot = lambda lane: {"status": "ok", "sessions": []}
    car.cleanup_area = lambda lane, keep_sid="": None
    car.save_state = lambda st: None
    car.core.refresh_env = lambda: {}
    return lane


def run_tick(car, lane: dict, *, rework: bool):
    """Один проход `tick()` — выбор промта из очереди.

    Возвращает (взятые промты, состояние полосы, состояние «доработок», журнал).
    """
    ls = {"n": 1, "slug": "test", "title": "ТЕСТ",
          "queue": [PROMPT_ID], "reserve": [],
          "cur": None, "pending": 0, "pending_since": 0,
          "done": [], "failed": [], "tries": {},
          "not_before": 0, "dead_turn": ""}
    st = {"lanes": {"test": ls}, "reserve": [], "inflight": {}}
    if rework:
        st["rework"] = {str(PROMPT_ID): {"note": "осталось доделать",
                                          "after_pr": AFTER_PR}}

    taken: list[int] = []

    def fake_start_session(lane_arg, text):
        # Промт, который карусель отдала в работу, — в тексте задания.
        taken.append(int(text.rsplit("#", 1)[1]))
        return True

    car.start_session = fake_start_session
    car.handle_lane = lambda lane_arg, ls_arg, snap, now: "free"

    car.tick(st)
    journal = ""
    if car.LOG.exists():
        journal = car.LOG.read_text(encoding="utf-8")
    return taken, ls, st, journal


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)

        # ── 1) Доработка с новым слитым PR: второй раз НЕ берём ──
        print("1) доработка с новым слитым PR: промт не берётся заново")
        car = load_carousel(tmp)
        prepare(car, tmp, merged_pr=NEW_PR)
        taken, ls, st, journal = run_tick(car, {"n": 1, "slug": "test"}, rework=True)
        check(not taken,
              "доработка взята заново, хотя новый PR #%d уже слит: %s" % (NEW_PR, taken))
        check(PROMPT_ID in (ls.get("done") or []),
              "доработка не помечена выполненной: %s" % (ls.get("done") or []))
        check(not (ls.get("queue") or []),
              "доработка осталась в очереди: %s" % (ls.get("queue") or []))
        check(str(PROMPT_ID) not in (st.get("rework") or {}),
              "доработка не закрыта в состоянии: %s" % (st.get("rework") or {}))
        # 3) лог объясняет ПОЧЕМУ пропущен (номер PR и порог) — иначе «промт пропал»
        check("уже выполнен" in journal,
              "в журнале нет «уже выполнен»: %s" % journal[-300:])
        check(str(NEW_PR) in journal,
              "в журнале нет номера нового PR #%d: %s" % (NEW_PR, journal[-300:]))
        check(str(AFTER_PR) in journal,
              "в журнале нет порога after_pr=%d: %s" % (AFTER_PR, journal[-300:]))

        # ── 2) Порог after_pr: старый PR зачётом не считается ──
        print("2) старый PR не зачёт — доработку берём (иначе правка закрыла бы дорогу)")
        tmp2 = pathlib.Path(tempfile.mkdtemp(dir=tmpdir))
        car = load_carousel(tmp2)
        prepare(car, tmp2, merged_pr=AFTER_PR)
        taken, ls, st, journal = run_tick(car, {"n": 1, "slug": "test"}, rework=True)
        check(taken == [PROMPT_ID],
              "доработка не взята, хотя нового PR выше after_pr нет: %s" % taken)
        check(PROMPT_ID not in (ls.get("done") or []),
              "доработка помечена выполненной по старому PR: %s" % (ls.get("done") or []))
        check(str(PROMPT_ID) in (st.get("rework") or {}),
              "доработка снята из состояния, хотя не выполнена: %s" % (st.get("rework") or {}))

        # ── 3) Обычный промт (не доработка): прежнее поведение сохранено ──
        print("3) обычный промт: по архиву КАРМАНа и по своему PR — пропускается")
        tmp3 = pathlib.Path(tempfile.mkdtemp(dir=tmpdir))
        car = load_carousel(tmp3)
        prepare(car, tmp3, merged_pr=NEW_PR, archived=True)
        taken, ls, st, journal = run_tick(car, {"n": 1, "slug": "test"}, rework=False)
        check(not taken,
              "обычный промт в архиве КАРМАНа взят заново: %s" % taken)
        check(PROMPT_ID in (ls.get("done") or []),
              "обычный промт не помечен выполненным: %s" % (ls.get("done") or []))

        tmp4 = pathlib.Path(tempfile.mkdtemp(dir=tmpdir))
        car = load_carousel(tmp4)
        prepare(car, tmp4, merged_pr=NEW_PR, archived=False)
        taken, ls, st, journal = run_tick(car, {"n": 1, "slug": "test"}, rework=False)
        check(not taken,
              "обычный промт со своим слитым PR взят заново: %s" % taken)
        check(PROMPT_ID in (ls.get("done") or []),
              "обычный промт со своим PR не помечен выполненным: %s" % (ls.get("done") or []))

        # ── 4) Временная правка: без проверки доработки приёмка падает ──
        print("4) временная правка: без проверки «выполнено ли доработка» приёмка падает")
        tmp5 = pathlib.Path(tempfile.mkdtemp(dir=tmpdir))
        broken = load_carousel(tmp5, broken="rework")
        prepare(broken, tmp5, merged_pr=NEW_PR)
        taken, ls, st, journal = run_tick(broken, {"n": 1, "slug": "test"}, rework=True)
        check(taken == [PROMPT_ID],
              "сломаная копия НЕ взяла доработку заново — значит, проверка "
              "проходит по неверной причине: %s" % taken)

    if failures:
        print("❌ приёмка «доработка не берётся заново» (живой случай 28.11): FAIL (%d)"
              % len(failures))
        return 1
    print("✅ приёмка «доработка не берётся заново» (живой случай 28.11): PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
