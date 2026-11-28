# -*- coding: utf-8 -*-
"""Приёмка системы мозга проекта «МиниГен» (промт КАРМАНА #199).

Заказ владельца (28.11.2026, раздел `MiniGen`): «каждый проект должен завести
у себя систему мозга — конституцию, линзу и совет экспертов — и проводить через
неё каждое новое задание, а не разбирать каждое заново».

Приёмка проверяет **не наличие файлов, а связность системы**:

1. **набор согласован** — конституция, линза, порядок приёма, реестр экспертов
   лежат в репозитории и ссылаются друг на друга;
2. **у каждой роли есть проверяемый выход** — что делает, что запускает, что
   отдаёт дальше (а не «посмотрел и сказал»);
3. **роли заведены один раз** — реестр и документ про роли совпадают, и в
   реестре нет второй копии каждой роли;
4. **оба маршрута описаны** — простая задача идёт «координатор → программист»,
   сложная — через совет, и потолок совета записан;
5. **линза не расходится с кодом** — каждый путь, названный в линзе, существует
   (иначе карта протухает тихо, и это главный риск промта);
6. **правило «изменился проект — изменилась карта» есть в правилах проекта** —
   в `AGENTS.md`, а не в заметке на полях;
7. **эксперты пользуются накопленным опытом** — опыт лежит в уже принятых
   местах проекта, а не в новом хранилище;
8. **роли, переиспользующие совет программы, ссылаются на существующие брифы**;
9. **одно задание прошло через систему целиком** и совет обкатан на заданиях
   разного характера — роли включаются по-разному, а не всегда все сразу.

Запуск: python tools/minimax-project-areas/test_brain_system.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BRAIN = REPO / "docs" / "brain"

#: Документы набора. Порядок — как в заказе: конституция, линза, приём, реестр.
DOCS = {
    "index": BRAIN / "README.md",
    "constitution": BRAIN / "CONSTITUTION.md",
    "lens": BRAIN / "LENS.md",
    "intake": BRAIN / "INTAKE.md",
    "council": BRAIN / "COUNCIL.md",
    "registry": BRAIN / "registry.json",
}

#: Девять ролей, которые реестр обязан содержать (критерии приёмки промта).
REQUIRED_ROLES = (
    "coordinator",
    "programmer",
    "scout",
    "critic",
    "tester",
    "anthropogenesis",
    "jester",
    "efficiency",
    "necessity",
)

#: Что обязано быть записано про каждую роль: без этого «роли» — это слово.
ROLE_FIELDS = ("slug", "title", "kind", "does", "launches", "hands_off",
               "output", "mandatory_when")

#: Где в репозитории живёт опыт проекта. Второе хранилище заводить нельзя.
EXPERIENCE_PATHS = (
    "docs/SESSION_HANDOFF.md",
    "mailbox/to-brain",
    "tools/minigen-gui/bridge/agent-templates/ideas/skills",
    "tools/minigen-gui/docs/knowledge-pack.md",
)

#: Куда смотрят роли, когда ищут опыт проекта.
EXPERIENCE_SOURCES = (
    "docs/SESSION_HANDOFF.md",
    "mailbox/to-brain",
    "tools/minigen-gui/docs/knowledge-pack.md",
)

#: Пути, относительно которых резолвится короткая ссылка (напр. `bridge/x.py`).
BASES = (
    REPO,
    REPO / "tools" / "minigen-gui",
    REPO / "tools" / "minimax-project-areas",
)


def read(path: Path) -> str:
    """Текст файла; отсутствующий файл — пустая строка (проверка скажет)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def norm(value: object) -> str:
    """Текст в нижний регистр без лишних знаков — для сравнения по смыслу."""
    text = str(value or "").lower().replace("«", "").replace("»", "")
    text = text.replace("(", " ").replace(")", " ")
    text = text.replace("**", "").replace("`", "").replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()


def title_stem(title: str) -> str:
    """Название роли без уточнения в скобках: «Шут («Иванушка-дурачок»)» → «Шут»."""
    text = str(title or "").split("(")[0]
    return norm(text)


# ───────────────────────────────────────────────────────── документы набора

def check_docs(failures: list[str]) -> None:
    print("1) набор документов системы мозга")
    for name, path in DOCS.items():
        if not path.is_file():
            failures.append("нет документа: %s" % path.relative_to(REPO))
            print("   FAIL: нет документа: %s" % path.relative_to(REPO))
    if not failures:
        print("   ок: %d документов на месте" % len(DOCS))


def check_links(failures: list[str]) -> None:
    """Набор — это связный набор: индекс ведёт в каждый документ, из каждого
    документа можно вернуться в индекс. Иначе это просто набор файлов."""
    print("2) документы связаны между собой")
    index = DOCS["index"]
    index_text = read(index)
    for path in DOCS.values():
        if path.suffix != ".md" or path == index or not path.is_file():
            continue
        if path.name not in index_text:
            failures.append("индекс не ведёт к %s" % path.name)
            print("   FAIL: индекс не ведёт к %s" % path.name)
        if index.name not in read(path):
            failures.append("из %s нельзя вернуться в индекс (%s)"
                            % (path.name, index.name))
            print("   FAIL: из %s нельзя вернуться в индекс" % path.name)
    if not failures:
        print("   ок: индекс ведёт в каждый документ, из каждого — обратно")


# ──────────────────────────────────────────────────────────── реестр ролей

def load_registry(failures: list[str]) -> dict:
    print("3) реестр экспертов")
    raw = read(DOCS["registry"])
    try:
        data = json.loads(raw)
    except ValueError as exc:
        failures.append("реестр не прочитан: %s" % exc)
        print("   FAIL: реестр не прочитан: %s" % exc)
        return {}
    if not isinstance(data, dict):
        failures.append("реестр — не объект")
        print("   FAIL: реестр — не объект")
        return {}
    roles = data.get("roles")
    if not isinstance(roles, list):
        failures.append("в реестре нет списка ролей")
        print("   FAIL: в реестре нет списка ролей")
        return data
    slugs = [str(item.get("slug") or "") for item in roles if isinstance(item, dict)]
    print("   ролей в реестре: %d (%s)" % (len(roles), ", ".join(slugs)))
    if len(slugs) != len(set(slugs)):
        failures.append("в реестре есть повторяющиеся роли — роль заводится один раз")
        print("   FAIL: в реестре есть повторяющиеся роли")
    for slug in REQUIRED_ROLES:
        if slug not in slugs:
            failures.append("в реестре нет роли: %s" % slug)
            print("   FAIL: в реестре нет роли: %s" % slug)
    for item in roles:
        if not isinstance(item, dict):
            continue
        for field in ROLE_FIELDS:
            if not str(item.get(field) or "").strip():
                failures.append("роль %s: не записано «%s»"
                                % (item.get("slug") or "?", field))
                print("   FAIL: роль %s: не записано «%s»"
                      % (item.get("slug") or "?", field))
    if not failures:
        print("   ок: %d ролей, у каждой записано: %s"
              % (len(roles), ", ".join(ROLE_FIELDS)))
    return data


def check_role_rules(registry: dict, failures: list[str]) -> None:
    """Проверяемый выход каждой роли — по смыслу, а не по совпадению строк."""
    print("4) у каждой роли есть проверяемый выход")
    roles = {str(item.get("slug")): item
             for item in (registry.get("roles") or []) if isinstance(item, dict)}

    def field(slug: str, name: str) -> str:
        return norm((roles.get(slug) or {}).get(name))

    checks = (
        # Программист: стек проекта и лучшие скиллы проекта, а не общие.
        ("programmer", "does", ("стек",), "знает стек проекта"),
        ("programmer", "does", ("скилл",), "берёт лучшие скиллы проекта"),
        ("programmer", "reads", ("skills",), "читает скиллы проекта"),
        # Разведчик: только названный участок.
        ("scout", "does", ("названн",), "ходит по названному участку"),
        ("scout", "does", ("весь проект",), "не штудирует весь проект"),
        ("scout", "output", ("модул", "связ"), "отдаёт модули и связи"),
        # Критик: вывод по коду и по плану.
        ("critic", "output", ("код",), "вывод по коду"),
        ("critic", "output", ("план",), "вывод по плану"),
        # Тестер: вердикт по стенду и «тупик или нет».
        ("tester", "output", ("стенд",), "вердикт по стенду"),
        ("tester", "output", ("тупик",), "решает или уходит в тупик"),
        ("tester", "does", ("интерфейс",), "воссоздаёт ситуации в интерфейсе"),
        # Антропогенез: размеры, расположение, ввод и чтение + устройство.
        ("anthropogenesis", "output", ("кноп",), "размеры и расположение кнопок"),
        ("anthropogenesis", "output", ("шрифт",), "размеры шрифтов"),
        ("anthropogenesis", "output", ("компьютер", "телефон"), "учитывает устройство"),
        # Шут: идеи за рамками, решение не принимает.
        ("jester", "does", ("рамк",), "идеи за рамками проекта"),
        ("jester", "does", ("нелеп",), "допускает нелепые идеи"),
        ("jester", "does", ("не принимает",), "решение не принимает"),
        # Эффективность: решение из трёх вариантов + дубли.
        ("efficiency", "output", ("новый",), "вариант «новый модуль»"),
        ("efficiency", "output", ("доработать",), "вариант «доработать»"),
        ("efficiency", "output", ("общий",), "вариант «общий модуль»"),
        ("efficiency", "output", ("дубл",), "ищет дубли функций и кода"),
        # Нужность: нужна ли функция.
        ("necessity", "output", ("нужна",), "вердикт: нужна ли функция"),
        ("necessity", "does", ("лишн",), "ловит лишнее"),
    )
    for slug, field_name, needles, human in checks:
        text = field(slug, field_name)
        if not text:
            failures.append("роль %s: пустое поле «%s»" % (slug, field_name))
            print("   FAIL: роль %s: пустое поле «%s»" % (slug, field_name))
            continue
        missing = [n for n in needles if n not in text]
        if missing:
            failures.append("роль %s: в «%s» нет %s"
                            % (slug, field_name, ", ".join(missing)))
            print("   FAIL: роль %s: в «%s» нет %s"
                  % (slug, field_name, ", ".join(missing)))
    if not failures:
        print("   ок: у всех ролей выход проверяем")


def check_routes(registry: dict, failures: list[str]) -> None:
    """Оба маршрута описаны и работают; потолок совета записан."""
    print("5) маршруты приёма и потолок совета")
    routes = {str(item.get("id")): item
              for item in (registry.get("routes") or []) if isinstance(item, dict)}
    for rid in ("simple", "complex"):
        if rid not in routes:
            failures.append("в реестре нет маршрута: %s" % rid)
            print("   FAIL: в реестре нет маршрута: %s" % rid)
    simple = routes.get("simple") or {}
    complex_route = routes.get("complex") or {}
    if simple.get("council") is not False:
        failures.append("простая задача не идёт без совета")
        print("   FAIL: простая задача не идёт без совета")
    if complex_route.get("council") is not True:
        failures.append("сложная задача не идёт через совет")
        print("   FAIL: сложная задача не идёт через совет")
    simple_steps = [str(s) for s in (simple.get("steps") or [])]
    if "coordinator" not in simple_steps or "programmer" not in simple_steps:
        failures.append("простой маршрут не «координатор → программист»")
        print("   FAIL: простой маршрут не «координатор → программист»")
    for stray in ("council", "scout", "critic", "tester"):
        if stray in simple_steps:
            failures.append("в простом маршруте лишний шаг: %s" % stray)
            print("   FAIL: в простом маршруте лишний шаг: %s" % stray)
    simple_checks = [str(s) for s in (simple.get("checks") or [])]
    for needed in ("critic", "tester"):
        if needed not in simple_checks:
            failures.append("в простом маршруте нет проверки: %s" % needed)
            print("   FAIL: в простом маршруте нет проверки: %s" % needed)
    complex_steps = [str(s) for s in (complex_route.get("steps") or [])]
    if "scout" not in complex_steps or "council" not in complex_steps:
        failures.append("в сложном маршруте нет разведчика или совета")
        print("   FAIL: в сложном маршруте нет разведчика или совета")
    complex_checks = [str(s) for s in (complex_route.get("checks") or [])]
    for needed in ("critic", "tester"):
        if needed not in complex_checks:
            failures.append("в сложном маршруте нет проверки: %s" % needed)
            print("   FAIL: в сложном маршруте нет проверки: %s" % needed)
    limits = registry.get("limits") or {}
    try:
        simple_limit = int(limits.get("simple_task_experts"))
    except (TypeError, ValueError):
        simple_limit = -1
        failures.append("не записан предел для простой задачи")
        print("   FAIL: не записан предел для простой задачи")
    if simple_limit != 1:
        failures.append("простая задача должна идти с одним экспертом, а не с %s"
                        % simple_limit)
        print("   FAIL: простая задача должна идти с одним экспертом")
    try:
        max_roles = int(limits.get("max_experts_per_council"))
    except (TypeError, ValueError):
        max_roles = -1
        failures.append("не записан потолок совета")
        print("   FAIL: не записан потолок совета")
    if max_roles < 1 or max_roles > 6:
        failures.append("потолок совета вне границы 1–6: %s" % max_roles)
        print("   FAIL: потолок совета вне границы 1–6")
    if not failures:
        print("   ок: простой маршрут без совета, сложный — через совет")


# ──────────────────────────────────────────────────────── опыт и владелец

def check_experience(registry: dict, failures: list[str]) -> None:
    """Эксперты копят опыт там же, где проект уже хранит знания."""
    print("6) эксперты пользуются накопленным опытом")
    for rel in EXPERIENCE_PATHS:
        if not (REPO / rel).exists():
            failures.append("место опыта не найдено: %s" % rel)
            print("   FAIL: место опыта не найдено: %s" % rel)
    roles = {str(item.get("slug")): item
             for item in (registry.get("roles") or []) if isinstance(item, dict)}
    coordinator = roles.get("coordinator") or {}
    reads = " ".join(str(r) for r in (coordinator.get("reads") or []))
    for source in EXPERIENCE_SOURCES:
        if source not in reads:
            failures.append("координатор не поднимает опыт: %s" % source)
            print("   FAIL: координатор не поднимает опыт: %s" % source)
    intake = norm(read(DOCS["intake"]))
    for rel in EXPERIENCE_PATHS:
        leaf = rel.rsplit("/", 1)[-1]
        if leaf and norm(leaf) not in intake:
            failures.append("в порядке приёма не сказано, куда писать опыт: %s" % rel)
            print("   FAIL: в порядке приёма не сказано, куда писать опыт: %s" % rel)
    if not failures:
        print("   ок: опыт поднимается и пишется в существующие места")


def check_owner(registry: dict, failures: list[str]) -> None:
    """У системы есть владелец в проекте — она не «в голове» у одного агента."""
    print("7) у системы есть владелец")
    owner = registry.get("owner") or {}
    for field_name in ("role", "who", "who_maintains", "rule"):
        if not str(owner.get(field_name) or "").strip():
            failures.append("в реестре не записано «owner.%s»" % field_name)
            print("   FAIL: в реестре не записано «owner.%s»" % field_name)
    index = norm(read(DOCS["index"]))
    if "кто владеет системой" not in index:
        failures.append("в index нет раздела «Кто владеет системой»")
        print("   FAIL: в index нет раздела «Кто владеет системой»")
    if not failures:
        print("   ок: владелец назван, и видно со стороны")


# ───────────────────────────────────────────────────── линза против кода

def repo_paths() -> set[str]:
    """Все пути репозитория (без node_modules и .git) — для поиска по хвосту."""
    out: set[str] = set()
    skip = {"node_modules", ".git", "__pycache__", "out", "release", ".venv"}
    for root, dirs, files in __import__("os").walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip]
        rel_root = Path(root).relative_to(REPO)
        for name in files:
            out.add((rel_root / name).as_posix())
        for name in dirs:
            out.add((rel_root / name).as_posix())
    return out


#: Корни репозитория, из которых начинается настоящий путь (файл или папка).
ROOT_DIRS = ("tools", "docs", "mailbox", ".claude", ".opencode")

#: Расширения, по которым путь считается файлом.
PATH_SUFFIXES = (".py", ".md", ".json", ".ts", ".tsx", ".cmd", ".ps1", ".yaml",
                  ".yml", ".txt")


def lens_paths(text: str) -> list[str]:
    """Пути, названные в линзе: из обратных кавычек.

    Путь — это либо файл с расширением, либо папка под корнем репозитория.
    Всё остальное (команды, env-переменные, названия наборов агентов) — не путь.
    """
    found: list[str] = []
    for span in re.findall(r"`([^`\n]+)`", text):
        span = span.strip()
        if not span:
            continue
        if " " in span:
            # Команда вида `python tools/x.py` — берём первый кусок.
            head = span.split(" ")[0]
            span = head if is_path_like(head) else ""
        if not span or not is_path_like(span):
            continue
        found.append(span)
    return found


def is_path_like(span: str) -> bool:
    """Похоже ли это на путь в репозитории."""
    if span.startswith(("C:", "\\", "http")) or "=" in span:
        return False
    if not re.match(r"^[\w./\-*\\]+$", span):
        return False
    if span.endswith(PATH_SUFFIXES):
        return True
    first = span.replace("\\", "/").split("/")[0]
    return first in ROOT_DIRS


def check_lens_against_code(failures: list[str]) -> None:
    """Линза не расходится с кодом: названный путь обязан существовать."""
    print("8) линза не расходится с кодом")
    text = read(DOCS["lens"])
    if not text:
        failures.append("линза не прочитана")
        print("   FAIL: линза не прочитана")
        return
    known = repo_paths()
    checked = 0
    for rel in lens_paths(text):
        checked += 1
        exists = False
        for base in BASES:
            candidate = base / rel
            if candidate.exists():
                exists = True
                break
        if not exists and "*" in rel:
            for base in BASES:
                if list(base.glob(rel)):
                    exists = True
                    break
        if not exists:
            tail = "/" + rel.lstrip("/")
            if any(p.endswith(tail) for p in known):
                exists = True
        if not exists:
            failures.append("линза называет путь, которого нет: %s" % rel)
            print("   FAIL: линза называет путь, которого нет: %s" % rel)
    print("   путей в линзе проверено: %d" % checked)
    for layer in ("Слой 1", "Слой 2", "Слой 3", "Слой 4"):
        if layer not in text:
            failures.append("в линзе нет %s" % layer)
            print("   FAIL: в линзе нет %s" % layer)


def check_update_rule(failures: list[str]) -> None:
    """Правило «изменился проект — изменилась карта» — в правилах проекта."""
    print("9) правило обновления линзы есть в правилах проекта")
    agents = read(REPO / "AGENTS.md")
    if "Система мозга проекта" not in agents:
        failures.append("в AGENTS.md нет раздела «Система мозга проекта»")
        print("   FAIL: в AGENTS.md нет раздела «Система мозга проекта»")
    text = norm(agents)
    if "изменился проект" not in text:
        failures.append("в AGENTS.md нет правила «изменился проект — изменилась карта»")
        print("   FAIL: в AGENTS.md нет правила «изменился проект — изменилась карта»")
    for needle in ("линзу", "реестр"):
        if needle not in text:
            failures.append("в правиле обновления нет слова «%s»" % needle)
            print("   FAIL: в правиле обновления нет слова «%s»" % needle)
    if "docs/brain" not in agents:
        failures.append("в AGENTS.md нет указателя на docs/brain")
        print("   FAIL: в AGENTS.md нет указателя на docs/brain")
    for path in (REPO / ".claude" / "commands" / "council.md",
                 REPO / ".opencode" / "commands" / "council.md"):
        if not path.is_file():
            failures.append("нет исполнимой памятки приёма: %s"
                            % path.relative_to(REPO))
            print("   FAIL: нет исполнимой памятки приёма: %s"
                  % path.relative_to(REPO))
    claude = REPO / ".claude" / "commands" / "council.md"
    opencode = REPO / ".opencode" / "commands" / "council.md"
    if claude.is_file() and opencode.is_file():
        if read(claude) != read(opencode):
            failures.append("копии памятки приёма разошлись (.claude и .opencode)")
            print("   FAIL: копии памятки приёма разошлись")
    if not failures:
        print("   ок: правило есть в AGENTS.md, памятки приёма на месте и синхронны")


# ──────────────────────────────────────────── связь с советом программы

def check_program_council(registry: dict, failures: list[str]) -> None:
    """Роли, переиспользующие совет программы, ссылаются на существующие брифы."""
    print("10) связь с советом в программе")
    program_registry = (REPO / "tools" / "minigen-gui" / "bridge" / "council"
                        / "registry.json")
    try:
        program = json.loads(read(program_registry))
    except ValueError:
        failures.append("реестр совета программы не прочитан")
        print("   FAIL: реестр совета программы не прочитан")
        return
    program_slugs = {str(item.get("slug"))
                     for item in (program.get("roles") or []) if isinstance(item, dict)}
    for item in registry.get("roles") or []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        linked = item.get("in_program_role")
        if not linked:
            continue
        if str(linked) not in program_slugs:
            failures.append("роль %s ссылается на несуществующую роль программы: %s"
                            % (slug, linked))
            print("   FAIL: роль %s ссылается на несуществующую роль программы: %s"
                  % (slug, linked))
    council_text = read(DOCS["council"])
    if "minigen_council.py" not in council_text:
        failures.append("в реестре ролей нет ссылки на совет программы")
        print("   FAIL: в реестре ролей нет ссылки на совет программы")
    if not failures:
        print("   ок: ссылки на совет программы существуют")


# ────────────────────────────────────────── роли в документе и в приёме

def check_roles_documented(registry: dict, failures: list[str]) -> None:
    """Реестр и документ про роли — одно и то же (роль заведена один раз)."""
    print("11) роли описаны в документе и в порядке приёма")
    council = read(DOCS["council"])
    intake = norm(read(DOCS["intake"]))
    for item in registry.get("roles") or []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        title = str(item.get("title") or "")
        if slug and ("`%s`" % slug) not in council:
            failures.append("роль %s не описана в COUNCIL.md" % slug)
            print("   FAIL: роль %s не описана в COUNCIL.md" % slug)
        stem = title_stem(title)
        if stem and stem not in intake:
            failures.append("роль «%s» не упомянута в порядке приёма" % title)
            print("   FAIL: роль «%s» не упомянута в порядке приёма" % title)
    if not failures:
        print("   ок: роли описаны и упомянуты в приёме")


# ───────────────────────────────────── прогон задания и обкатка совета

def check_walkthrough(failures: list[str]) -> None:
    """Одно задание прошло через систему; совет обкатан на трёх характерах."""
    print("12) задание прошло через систему целиком")
    walk = BRAIN / "WALKTHROUGH-2026-11-28.md"
    if not walk.is_file():
        failures.append("нет разбора прогона: %s" % walk.relative_to(REPO))
        print("   FAIL: нет разбора прогона: %s" % walk.relative_to(REPO))
        return
    text = norm(read(walk))
    stages = ("кого позвали", "что решил совет", "что сделал программист",
              "что сказал критик", "что показал стенд")
    for stage in stages:
        if stage not in text:
            failures.append("в разборе нет этапа «%s»" % stage)
            print("   FAIL: в разборе нет этапа «%s»" % stage)
    kinds = ("мелкое", "крупное", "спорное")
    for kind in kinds:
        if kind not in text:
            failures.append("в обкатке нет задания характера «%s»" % kind)
            print("   FAIL: в обкатке нет задания характера «%s»" % kind)
    # Разбор лежит в передаче смены — не в отдельном хранилище.
    handoff_raw = read(REPO / "docs" / "SESSION_HANDOFF.md")
    # Разбор задачи — **последний** в передаче смены (смена новая сверху).
    blocks = handoff_raw.split("Разбор задачи:")
    if len(blocks) < 2:
        failures.append("в SESSION_HANDOFF.md нет разбора задачи")
        print("   FAIL: в SESSION_HANDOFF.md нет разбора задачи")
        section = ""
    else:
        # Сам блок разбора (до следующего заголовка), а не вся запись о смене:
        # иначе проверка ловит чужое «#199» и проходит не по той причине.
        section = blocks[1].split("\n### ")[0].split("\n## ")[0]
    section_norm = norm(section)
    for stage in stages:
        if stage not in section_norm:
            failures.append("в разборе в SESSION_HANDOFF.md нет этапа «%s»" % stage)
            print("   FAIL: в разборе в SESSION_HANDOFF.md нет этапа «%s»" % stage)
    # Разбор должен называть PR: по нему видно, какая правка закрыла задание.
    # Именно PR, а не номер промта: «#199» — это промт, а не правка.
    if not re.search(r"PR[^\n]{0,24}#\s*\d{2,4}", section):
        failures.append("в разборе в SESSION_HANDOFF.md не назван PR")
        print("   FAIL: в разборе в SESSION_HANDOFF.md не назван PR")
    # Разбор называет промт, который он закрывает: правило — на любую
    # задачу, а не на номер конкретного промта.
    if not re.search(r"промт\s*#\s*\d+", section_norm):
        failures.append("в разборе не назван промт, который он закрывает")
        print("   FAIL: в разборе не назван промт, который он закрывает")
    # Запись о смене оформлена по канону закрытия смены: «Статус»,
    # «Что сделано», «Проверено», «Следующий шаг», «Открытые вопросы».
    entry_lines: list[str] = []
    inside = False
    for line in handoff_raw.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = True
            continue
        if inside:
            entry_lines.append(line)
    entry = "\n".join(entry_lines)
    entry_headings = [line for line in entry_lines if line.startswith("###")]
    for heading in ("Статус", "Что сделано", "Проверено", "Следующий шаг",
                    "Открытые вопросы"):
        if not any(heading in line for line in entry_headings):
            failures.append("в записи о смене нет раздела «%s»" % heading)
            print("   FAIL: в записи о смене нет раздела «%s»" % heading)
    if not failures:
        print("   ок: разбор прогона есть, обкатка трёх характеров на месте")


def check_acceptance_untouched(failures: list[str]) -> None:
    """Экспертная система не подменяет приёмку «ветка + PR + решение за человеком»."""
    print("13) приёмка не размыта")
    intake = norm(read(DOCS["intake"]))
    for needle in ("ветка", "pr", "решение за человеком"):
        if needle not in intake:
            failures.append("в порядке приёма нет «%s»" % needle)
            print("   FAIL: в порядке приёма нет «%s»" % needle)


def main() -> int:
    failures: list[str] = []
    check_docs(failures)
    check_links(failures)
    registry = load_registry(failures)
    if registry:
        check_role_rules(registry, failures)
        check_routes(registry, failures)
        check_experience(registry, failures)
        check_owner(registry, failures)
        check_program_council(registry, failures)
        check_roles_documented(registry, failures)
    check_lens_against_code(failures)
    check_update_rule(failures)
    check_walkthrough(failures)
    check_acceptance_untouched(failures)
    if failures:
        print("❌ приёмка системы мозга: FAIL (%d)" % len(failures))
        for item in failures:
            print("   - " + item)
        return 1
    print("✅ приёмка системы мозга: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
