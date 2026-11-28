#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ночная карусель Матрицы РМЗ: три полосы, промты из корзины КАРМАНА.

Заказ владельца (25.11.2026, ночь): «ночью гонять все три агента по Матрице РМЗ,
параллельно». Карусель в одной полосе: открыть сессию → дать промт из корзины
КАРМАНА → проверить, что промт выполнен → закрыть сессию → архивировать её →
открыть новую сессию со следующим промтом. И так по кругу, пока промты не кончатся.

Полосы (изоляция по ADR-0011 — один пишущий агент на одно дерево):

  1 «Матрица РМЗ»   — основной клон  D:\\valstan\\REPO\\MatricaRMZ
  2 «Матрица РМЗ 2» — worktree m2   D:\\valstan\\REPO\\MatricaRMZ\\.claude\\worktrees\\m2
  3 «Матрица РМЗ 3» — worktree m3   D:\\valstan\\REPO\\MatricaRMZ\\.claude\\worktrees\\m3\\faza

Почему очереди не «как попало» (разбор 13 живых промтов по файлам):

  * У промтов есть общие горячие файлы: `fileService.ts`, `shared/src/domain/desktop.ts`,
    `AttachmentsPanel.tsx`, `FileSearchPage.tsx`, `preload/index.ts`. Промты, которые
    правят один и тот же файл, **нельзя вести одновременно** — второй будет затирать
    первый. Такие пары выписаны в матрице конфликтов (CONFLICT_PAIRS): 138×141,
    143×144, 144×148, 158×159, 159×160 и т.д.
  * Супервизор берёт промт, только если он не конфликтует ни с одним промтом,
    который сейчас в работе в другой полосе. Так три полосы работают параллельно
    там, где файлы не пересекаются, и не мешают друг другу там, где пересекаются.
  * Тяжёлый кластер {143,144,145,148,155,158,159,160} лежит в общем резерве:
    берёт его та полоса, у которой своя очередь кончилась. Пока в резерве остаётся
    кластер, где файлы совпадают почти у всех, — одновременно из него идёт один.
  * Порядок внутри очереди — по возрастанию номеров (как шли все прошлые партии).

Проверка выполнения (шаг «промт выполнен»):
  1. промт помечен архивным в КАРМАНе — значит агент отработал и заархивировал
     сам (мандат D-190: «архивирует тот, кто выполнил»);
  2. иначе ищем слитый PR, где в теле есть «Промт КАРМАНА #<id>» — если PR есть,
     работу считаем сделанной и архивируем промт сами (и пишем об этом в журнал);
  3. иначе — промт НЕ выполнен: возвращаем его в начало очереди той же полосы
     (до двух попыток) и пишем в журнал.

Состояние — %LOCALAPPDATA%\\mcode-web\\matrica-carousel-state.json, журнал —
matrica-carousel.log там же. Перезапуск супервизора безопасен: номер промта есть
в заголовке сессии, поэтому текущая нитка восстанавливается из базы рантайма.

Запуск:
    python C:\\Users\\valstan\\bin\\matrica-night-carousel.py            # демон (запускалка)
    python C:\\Users\\valstan\\bin\\matrica-night-carousel.py --once     # один проход
    python C:\\Users\\valstan\\bin\\matrica-night-carousel.py --status   # отчёт

⚠️ ИСТОЧНИК ИСТИНЫ (28.11.2026): логика живёт **здесь**, в репозитории
(`tools/minimax-project-areas/carousel/`), а машинный файл — тонкая запускалка,
которая исполняет эту копию. Правка каркаса идёт PR'ом сюда; раскатка на
машину — после мержа. Подробности — [`README.md`](README.md).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BIN = Path(r"C:\Users\valstan\bin")
LOCAL = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\valstan\AppData\Local")) / "mcode-web"

#: Путь к СВОЕМУ скрипту: смотритель перезапускает именно его. Обёртки проектов
#: (minigen/sarafan) обязаны переопределить его на свой файл — иначе смотритель
#: поднимает чужую карусель (так и вышло: три процесса Матрицы вместо трёх разных).
SCRIPT_PATH = Path(__file__).resolve()
LOG = LOCAL / "matrica-carousel.log"
STATE = LOCAL / "matrica-carousel-state.json"
KEYS = Path.home() / ".matricarmz-keys"
PROJECT = "MatricaRMZ"
REPO = "Valstan/MatricaRMZ"

POLL_SEC = 45
CLOSE_GRACE_FOREIGN = 480      # пауза перед закрытием чужой сессии, сек
CLOSE_WAIT_SEC = 900           # сколько ждать завершения «закрывающего» хода, сек
DEAD_TURN_WAIT_SEC = 900       # сколько ждать, пока сторож поднимет оборванный ход, сек
RETRY_COOLDOWN_SEC = 900       # пауза после упавшего хода (кредиты/сеть), сек
MAX_REQUEUE = 2                # сколько раз промт можно вернуть в очередь
PROMPT_ID_RE = re.compile(r"#(\d{2,4})")
#: Окно «дерево занято»: недавно активная чужая сессия тоже занимает папку
#: (её может разбудить автомат управления сессиями) — 10 минут.
TREE_BUSY_WINDOW_MS = 10 * 60 * 1000
#: Сколько ждать появления сессии в базе области, прежде чем считать промт
#: брошенным (28.11.2026). Без этого полоса встаёт НАВСЕГДА: промт отдан,
#: `mcode exec` упал («Sign in to MiniMax»), сессия не появилась — а `pending`
#: не пустой ⇒ полоса «занята», `inflight` держит номер промта, а
#: `conflicts(a, a) == True` ⇒ ни одна полоса не может взять промт из очереди.
#: Карусель крутится вхолостую, промты висят «в работе» без работы.
PENDING_APPEAR_TIMEOUT_SEC = 420

#: Инструкция агенту про архив промта (переопределяется под другой проект).
ARCHIVE_HINT = """10. АРХИВ ПРОМТА — по факту выполнения (архив = «сделано»). Когда задача выполнена и PR слит:
    $env:PROMPTS_TOKEN_MATRICARMZ = (Get-Content "$env:USERPROFILE\\.matricarmz-keys\\prompts-token" -Raw).Trim()
    $env:KARMAN_URL = (Get-Content "$env:USERPROFILE\\.matricarmz-keys\\vault-url" -Raw).Trim()
    $env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'
    node scripts/karman/prompts-section.mjs archive %d
    (503 = канал не настроен, 403 = не тот раздел — тогда не архивируй, запиши это в сводку.)"""

#: Базовая ветка репозитория проекта (у Матрицы — main, у biblio — master).
BASE_BRANCH = "origin/main"
BASE_BRANCH_SHORT = "main"

#: Блок правил для агента. Пусто — используется стандартный (ADR-0011/ADR-0002);
#: строка — свой блок проекта (другой стек, другая дисциплина). Подстановки:
#: (рабочая папка, файлы промта, незакоммиченное в папке, номер промта).
RULES_OVERRIDE = ""


# ────────────────────────────────────────────────────────── модули МиниГена

def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ⚠️ ИСТОЧНИК ИСТИНЫ (28.11.2026): сторож и его `minigen_core` берём **из этого
#    репозитория**, а не из машинного каталога. Раньше каркас грузил
#    `C:\Users\valstan\bin\minimax-sessionwatch.py` ⇒ правка сторожа в git не
#    доезжала до машины (репозиторий отставал на дни), а правка каркаса вообще
#    не могла попасть в репозиторий. `sys.path` дополняем каталогом
#    `tools/minimax-project-areas/`, чтобы `import minigen_core` внутри сторожа
#    взял ТОЖЕ репозиторную копию, а не машинную.
REPO_TOOLS = Path(__file__).resolve().parent.parent
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

sw = load_module("sessionwatch", REPO_TOOLS / "minimax-sessionwatch.py")
core = sw.core


# ────────────────────────────────────────────────────────── полосы и очереди

LANES = [
    {
        "n": 1, "slug": "matricarmz", "title": "Матрица РМЗ",
        "folder": r"D:\valstan\REPO\MatricaRMZ",
        "queue": [],            # полоса 1 сразу берёт из общей тяжёлой очереди
    },
    {
        "n": 2, "slug": "matricarmz2", "title": "Матрица РМЗ 2",
        "folder": r"D:\valstan\REPO\MatricaRMZ\.claude\worktrees\m2",
        "queue": [141, 138, 147],
    },
    {
        "n": 3, "slug": "matricarmz3", "title": "Матрица РМЗ 3",
        "folder": r"D:\valstan\REPO\MatricaRMZ\.claude\worktrees\m3\faza",
        "queue": [142, 150],
    },
]

#: Общий резерв промтов (тяжёлый кластер) — берёт любая полоса, когда своя
#: очередь кончилась.
RESERVE = [143, 144, 145, 148, 155, 158, 159, 160]

#: Матрица конфликтов: пары промтов, которые правят общие файлы ⇒ не могут идти
#: одновременно. Проверено по разбору файлов из раздела «Контекст» каждого промта.
CONFLICT_PAIRS = {
    (138, 141), (138, 155),
    (141, 148), (141, 160),
    (142, 150),
    (143, 144), (143, 145), (143, 148), (143, 158), (143, 159), (143, 160),
    (144, 145), (144, 148), (144, 158), (144, 159), (144, 160),
    (145, 158), (145, 160),
    (148, 158), (148, 159), (148, 160),
    (155, 158), (155, 159), (155, 160),
    (158, 159), (158, 160),
    (159, 160),
}

#: Файлы, которые упоминает почти каждый промт: в сравнении не участвуют.
GENERIC_FILES = {
    "docs/session_handoff.md", "docs/completed.md", "docs/pending_followups.md",
    "docs/pending/", "docs/", "agents.md", "claude.md", "gemini.md",
}

#: Кэш «файлы промта» (из текста в КАРМАНЕ).
_FILES_CACHE: dict = {}


def _norm_path(path: str) -> str:
    """Приводим путь к общему виду (хвост из трёх сегментов) — «services/fileService.ts»."""
    p = path.replace("\\", "/").strip().lower()
    p = re.sub(r"^(\.\./)+", "", p)
    parts = [x for x in p.split("/") if x and x not in (".", "..")]
    if len(parts) >= 3:
        return "/".join(parts[-3:])
    return "/".join(parts)


def prompt_files(prompt_id: int) -> set:
    """Файлы, которые правит промт (из раздела «Контекст» его текста)."""
    if prompt_id in _FILES_CACHE:
        return _FILES_CACHE[prompt_id]
    body = prompt_body(prompt_id)
    out = set()
    for raw in re.findall(r"`([^`\n]{3,160})`", body or ""):
        raw = raw.strip()
        if not raw or " " in raw or raw.startswith("http") or "/" not in raw:
            continue
        head = raw.split(":")[0]
        if not re.search(r"\.[A-Za-z0-9]{1,6}$", head):
            continue
        out.add(_norm_path(head))
    _FILES_CACHE[prompt_id] = out
    return out


def refresh_queues(st: dict) -> None:
    """Подобрать новые живые промты раздела в очереди полос.

    Раздел пополняется владельцем — карусель обязана видеть новые записи,
    а не только те, что были в очереди на момент запуска.
    """
    data = karman_section()
    if not data:
        return
    live = sorted(int(p.get("id") or 0) for p in (data.get("prompts") or []) if p.get("id"))
    if not live:
        return
    known = set(st.get("done_all") or [])
    for lane in LANES:
        ls = st["lanes"][lane["slug"]]
        known |= set(ls.get("queue") or [])
        known |= set(ls.get("done") or [])
        known |= set(ls.get("failed") or [])
        if ls.get("pending"):
            known.add(int(ls["pending"]))
    known |= set(st.get("reserve") or [])
    fresh = [pid for pid in live if pid not in known]
    for pid in fresh:
        lane = min(LANES, key=lambda L: len(st["lanes"][L["slug"]].get("queue") or []))
        ls = st["lanes"][lane["slug"]]
        ls["queue"] = sorted(set(ls.get("queue") or []) | {pid})
        log("ПОЛОСА %d: новый живой промт #%d добавлен в очередь" % (lane["n"], pid))


def conflicts(a: int, b: int) -> bool:
    """Промты конфликтуют, если правят общие файлы.

    Матрица конфликтов (ручная, для известных промтов) + динамика: файлы
    берём из самого текста промта. Файлы «общие для всех» (handoff, COMPLETED,
    PENDING) в сравнении не участвуют — иначе конфликтовали бы все.
    """
    if a == b:
        return True
    if (min(a, b), max(a, b)) in CONFLICT_PAIRS:
        return True
    fa = prompt_files(a)
    fb = prompt_files(b)
    if not fa or not fb:
        return False
    return bool((fa & fb) - GENERIC_FILES)

#: Горячие файлы промта — чтобы полосы не лезли в чужую территорию.
HOT_FILES = {
    138: ["electron-app/src/renderer/src/ui/components/GlobalSearchOverlay.tsx",
          "electron-app/src/renderer/src/ui/components/ToolbarSearchField.tsx",
          "shared/src/domain/tieredSearch.ts", "backend-api/src/routes/search.ts"],
    141: ["electron-app/src/renderer/src/ui/components/EngineHistoryFeedPanel.tsx",
          "electron-app/src/renderer/src/ui/pages/EngineDetailsPage.tsx",
          "electron-app/src/renderer/src/ui/components/CardActionBar.tsx"],
    142: ["electron-app/src/main/ipc/registerIpc.ts", "electron-app/src/renderer/src/ui/layout/Tabs.tsx",
          "android-app/src/core/ipcWiring.ts", "shared/src/domain/sectionAccess.ts"],
    143: ["electron-app/src/main/services/desktopTransferService.ts",
          "electron-app/src/main/ipc/register/desktopTransfer.ts",
          "electron-app/src/renderer/src/ui/components/SendFileToColleagueDialog.tsx",
          "shared/src/domain/desktop.ts", "electron-app/src/main/services/fileService.ts"],
    144: ["shared/src/domain/desktop.ts", "electron-app/src/main/services/fileService.ts",
          "electron-app/src/renderer/src/ui/components/AttachmentsPanel.tsx",
          "electron-app/src/main/ipc/register/mail.ts",
          "electron-app/src/renderer/src/ui/components/DesktopPane.tsx"],
    145: ["electron-app/src/renderer/src/ui/components/DesktopPane.tsx",
          "shared/src/domain/desktop.ts", "shared/src/domain/desktop.test.ts"],
    147: ["shared/src/domain/engineKind.ts", "shared/src/domain/contract.ts",
          "shared/src/domain/contractListFacets.ts"],
    148: ["electron-app/src/renderer/src/ui/components/AttachmentsPanel.tsx",
          "electron-app/src/renderer/src/ui/pages/EngineDetailsPage.tsx",
          "electron-app/src/main/services/fileService.ts"],
    150: ["electron-app/src/renderer/src/ui/shellV3/V3TabShell.tsx",
          "electron-app/src/renderer/src/ui/App.tsx",
          "electron-app/src/main/services/updateService.ts"],
    155: ["electron-app/src/renderer/src/ui/pages/FileSearchPage.tsx",
          "electron-app/src/main/ipc/register/fileSearch.ts",
          "electron-app/src/preload/index.ts",
          "shared/src/domain/fileSearchFolders.ts"],
    158: ["electron-app/src/renderer/src/ui/pages/FileSearchPage.tsx",
          "electron-app/src/main/ipc/register/fileSearch.ts",
          "electron-app/src/main/services/fileService.ts",
          "electron-app/src/main/ipc/register/mail.ts", "shared/src/domain/desktop.ts"],
    159: ["electron-app/src/main/services/fileService.ts",
          "electron-app/src/renderer/src/ui/pages/FileSearchPage.tsx",
          "electron-app/src/preload/index.ts", "electron-app/src/main/services/localImagePrint.ts"],
    160: ["electron-app/src/renderer/src/ui/components/AttachmentsPanel.tsx",
          "electron-app/src/renderer/src/ui/pages/EngineDetailsPage.tsx",
          "electron-app/src/main/ipc/register/mail.ts",
          "electron-app/src/main/services/desktopTransferService.ts",
          "electron-app/src/preload/index.ts", "shared/src/domain/desktop.ts"],
    # ── живые промты 27–28.11.2026 (три полосы параллельно) ──
    176: ["shared/src/domain/productionProgramAddByNumbers.ts",
          "electron-app/src/main/services/localMiniMax/agentTools/registry.ts",
          "electron-app/src/main/services/localMiniMax/agentTools/gate.ts",
          "electron-app/src/main/services/localMiniMax/actionBridge.ts",
          "electron-app/resources/local-minimax/matrica-actions-mcp.mjs",
          "shared/src/domain/auditObject.ts",
          "shared/src/labels/auditAction.ts",
          "electron-app/src/renderer/src/ui/components/ProductionProgramAddEnginesByNumbersDialog.tsx"],
    178: ["electron-app/src/main/services/scannerService.ts",
          "electron-app/src/main/ipc/register/scanner.ts",
          "shared/src/domain/scanDocument.ts",
          "shared/src/ipc/types.ts"],
    179: ["electron-app/src/renderer/src/ui/pages/ProductionProgramPage.tsx",
          "electron-app/src/renderer/src/ui/components/ProductionProgramPrintPreview.tsx",
          "electron-app/src/renderer/src/ui/components/ProductionProgramStageDialog.tsx",
          "electron-app/src/renderer/src/ui/utils/productionProgramExport.ts"],
}

#: Пометки к конкретным промтам — дописываются в текст задания.
#: Нужны там, где границы двух полос ПЕРЕСЕКАЮТСЯ: сказать агенту, чья это
#: территория и что делать при пересечении (канон репо: «границы пересеклись —
#: второй ждёт merge первого»).
PROMPT_NOTES = {
    176: (
        "\n⚠️ ГРАНИЦА С ДРУГОЙ ПОЛОСОЙ (промт #179, полоса 3). Экран производственной "
        "программы (`ProductionProgramPage.tsx`) в эту минуту перекраивает другая полоса: "
        "плашки, полноэкранный план, вертикальные полоски. Свою часть (разрез 2 — домен, "
        "реестр инструментов, гейт, канал действий, MCP-сервер, аудит, сторож, перенос тела "
        "«Добавить списком» в домен) делай целиком — она чужую территорию не задевает.\n"
        "   Пункт «кнопка нейросети рядом с «Добавить списком»» (разрез 3) — это ПРАВО "
        "ProductionProgramPage.tsx, а он сейчас не твой ⇒ делай его ПОСЛЕДНИМ шагом и только "
        "если полоса 3 уже слила свою правку: `git fetch origin && git log --oneline origin/main -15`.\n"
        "   Не слила — НЕ правь эту страницу: запиши пункт в `docs/pending/` и в handoff и "
        "доделывай всё остальное. Лучше готовая часть без чужой правки, чем merge-конфликт.\n"
    ),
    178: (
        "\n⚠️ ГРАНИЦА С ДРУГИМИ ПОЛОСАМИ. Твоя территория — контур скана: `scannerService.ts`, "
        "`ipc/register/scanner.ts`, `shared/src/domain/scanDocument.ts`, `shared/src/ipc/types.ts` "
        "и окно скана. Файлы производственной программы и локального агента (`localMiniMax/**`, "
        "`ProductionProgramPage.tsx`) — чужие в эту минуту: не правь их «на всякий случай».\n"
    ),
    179: (
        "\n⚠️ ГРАНИЦА С ДРУГОЙ ПОЛОСОЙ (промт #176, полоса 1). Локальный агент нейросети в эту "
        "минуту переносит тело «Добавить списком» из модалки в домен и заводит реестр "
        "инструментов: `ProductionProgramAddEnginesByNumbersDialog.tsx`, "
        "`shared/src/domain/productionProgramAddByNumbers.ts`, `services/localMiniMax/**` — "
        "это НЕ твоя территория. Твоя — экран и раскладка: `ProductionProgramPage.tsx`, "
        "компоненты плана, печать и выгрузка.\n"
        "   Если правка диалога «Добавить списком» тебе понадобится — не правь её: запиши "
        "потребность в PENDING и в handoff и двигайся дальше.\n"
    ),
}


# ────────────────────────────────────────────────────────── журнал

def log(msg: str) -> None:
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOCAL.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ────────────────────────────────────────────────────────── КАРМАН (раздел промтов)

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _opener() -> urllib.request.OpenerDirector:
    # Прокси из переменных окружения на этой машине бывает мёртвым (G690) —
    # запросы идут напрямую.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def karman_section(archived: bool = False):
    token = _read(KEYS / "prompts-token")
    base = _read(KEYS / "vault-url")
    if not token or not base:
        log("КАРМАН: нет токена/адреса в %s" % KEYS)
        return None
    url = base.rstrip("/") + "/api/prompts/section?project=%s%s" % (
        PROJECT, "&archived=1" if archived else "")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/json")
    try:
        with _opener().open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        log("КАРМАН: раздел не прочитан (%s)" % exc)
        return None


def karman_archive(prompt_id: int) -> bool:
    token = _read(KEYS / "prompts-token")
    base = _read(KEYS / "vault-url")
    if not token or not base:
        return False
    body = json.dumps({"project": PROJECT, "id": int(prompt_id),
                       "archived": True}).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/api/prompts/section", data=body,
                                 method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    try:
        with _opener().open(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        log("КАРМАН: архив промта #%s не прошёл — HTTP %s" % (prompt_id, exc.code))
        return False
    except Exception as exc:  # noqa: BLE001
        log("КАРМАН: архив промта #%s не прошёл (%s)" % (prompt_id, exc))
        return False


def prompt_body(prompt_id: int) -> str:
    """Текст промта из раздела (живые + архивные)."""
    for archived in (False, True):
        data = karman_section(archived=archived)
        if not data:
            continue
        for p in data.get("prompts") or []:
            if int(p.get("id") or 0) == prompt_id:
                return (p.get("body") or "").strip()
    return ""


# ────────────────────────────────────────────────────────── состояние

def default_state() -> dict:
    return {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reserve": list(RESERVE),
        "inflight": {},        # промт → полоса (что сейчас в работе)
        # ДОРАБОТКИ: промт → {"note": "что осталось доделать", "after_pr": N}
        # (N — номер PR на момент начала доработки; старый слитый PR не зачёт).
        "rework": {},
        "lanes": {
            lane["slug"]: {
                "n": lane["n"],
                "queue": list(lane["queue"]),
                "cur": None,      # {"sid","prompt_id","phase","since","turn_at_close"}
                "pending": 0,     # промт, который мы отдали, но ещё не подтвердили
                "pending_since": 0,  # когда отдали (для таймаута «сессия не появилась»)
                "done": [],
                "failed": [],
                "tries": {},
                "not_before": 0,
                "dead_turn": "",   # sid сессии, чей ход упал (ждём сторож)
            }
            for lane in LANES
        },
    }


def load_state() -> dict:
    # utf-8-sig: файл, записанный с BOM (например, из PowerShell), читаем иначе —
    # json.loads на BOM падает, и состояние молча сбрасывалось в пустое.
    for path in (STATE, Path(str(STATE) + ".bak")):
        try:
            st = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(st, dict) and "lanes" in st:
                return st
        except (OSError, ValueError):
            continue
    return default_state()


def save_state(st: dict) -> None:
    try:
        LOCAL.mkdir(parents=True, exist_ok=True)
        if STATE.exists():                 # прошлая копия — страховка от потери
            try:
                Path(str(STATE) + ".bak").write_bytes(STATE.read_bytes())
            except OSError:
                pass
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ────────────────────────────────────────────────────────── текст задания

def git_dirty(folder: str) -> list:
    """Незакоммиченные изменения в рабочей папке полосы (для предупреждения в задании)."""
    try:
        proc = subprocess.run(["git", "-C", folder, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001
        return []
    if proc.returncode != 0:
        return []
    return [l for l in (proc.stdout or "").splitlines() if l.strip()][:12]


def build_prompt(lane: dict, prompt_id: int, body: str, retry: bool = False,
                 rework_note: str = "") -> str:
    hot = HOT_FILES.get(prompt_id) or []
    hot_text = ("\n".join("  - %s" % h for h in hot) if hot
                else "  - (файлы — по разделу «Контекст» промта)")
    dirty = git_dirty(lane["folder"])
    dirty_text = ("\n   ⚠️ В рабочей папке лежат незакоммиченные изменения (проверь `git status`):\n"
                  + "\n".join("     %s" % d for d in dirty)
                  + "\n     Разберись: чужое не включай в свой коммит и не удаляй; своё (например, "
                    "handoff предыдущей нитки) — забери в свой PR или отмени.\n"
                  ) if dirty else ""
    head = ("ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО — промт КАРМАНА #%d, полоса %d «%s»."
            % (prompt_id, lane["n"], lane["title"]))
    retry_note = ("\n⚠️ ПРЕДЫДУЩАЯ ПОПЫТКА НЕ ЗАВЕРШЕНА (сводки/архива нет, слитого PR нет). "
                  "Прежде чем начинать заново: `git log --oneline -20` и "
                  "`gh pr list --state all --limit 10` — проверь, что уже сделано, "
                  "и доведи ДО КОНЦА, не начиная работу заново.\n") if retry else ""
    rework_text = ("\n".join([
        "",
        "⚠️ ДОРАБОТКА: этот промт уже выполнялся, но проверка выявила недоделки.",
        "Проверь каждый пункт ниже по коду и доделай ТОЛЬКО остаток.",
        "Уже сделанное не переделывай, чужое не трогай, гейты не ломай.",
        "",
        rework_note.strip(),
        "",
    ])) if rework_note.strip() else ""
    head_text = """%s

Полоса %d «%s». Рабочая папка полосы (свой клон/worktree): %s

Ниже — промт #%d из раздела `%s` библиотеки КАРМАНА (зачитка владельца). Выполни его полностью:
по его собственному плану и критериям приёмки.
%s%s%s===== ПРОМТ #%d =====
%s
===== КОНЕЦ ПРОМТА #%d =====
""" % (head, lane["n"], lane["title"], lane["folder"], prompt_id, PROJECT, retry_note,
       rework_text, PROMPT_NOTES.get(prompt_id, ""), prompt_id, body, prompt_id)

    if RULES_OVERRIDE:
        rules = RULES_OVERRIDE % (lane["folder"], hot_text, dirty_text, prompt_id)
    else:
        rules = """Обязательные правила работы (канон репозитория: ADR-0011, ADR-0002, AGENTS.md):

1. ГРАНИЦЫ. Пишешь ТОЛЬКО в своей рабочей папке полосы: %s
   В другие деревья репозитория не ходи, чужие правки не трогай, чужую ветку не переключай.
   Файлы этого промта (твоя территория):
%s%s
2. ВЕТКА И PR (один PR — одна задача). Начало: `git fetch origin`, затем
   `git checkout -b <type>/<slug> %s` (<type> — feat/fix/chore/docs/refactor).
   Прямой push в %s запрещён. В теле PR обязательно напиши строку
   «Промт КАРМАНА #%d» и границы (файлы/задача) — по ней супервизор ищет выполнение.
3. ГЕЙТ. `git push -u origin <ветка>` → `gh pr create` → дождись, пока проверка именно
   твоего PR зарегистрирована (`gh pr checks` показывает её, а не «no checks reported») →
   `gh pr checks --watch` → читай ВЫВОД, а не код выхода. Красный — чинить, не мержить.
   Зелёный — `gh pr merge --squash` (БЕЗ `--delete-branch`: в связке с `--squash` он
   сносит рабочий worktree полосы — грабля M237, твою папку перестанет видеть git).
   Ветку сноси сам после мержа: `git branch -d <ветка>` (или `git push origin --delete`).
   После мержа: `git fetch origin`, следующий промт начинай от свежего %s.
4. ПАРАЛЛЕЛЬНЫЕ ПОЛОСЫ. Кроме тебя работают ещё две полосы по тому же репозиторию
   (в других worktree). Тяжёлые файлы (общие для всего) не правь «на всякий случай»:
   сначала `git fetch origin` и переведи свою ветку на свежий %s; если чужая
   ветка с правкой ещё не слита — этот файл не трогай, запиши потребность в PR и в handoff
   и двигайся дальше по своему промту. Правило репо: «границы пересеклись — второй ждёт
   merge первого и ребейзит свою ветку до начала правок».
5. СТЕНД — общий ресурс машины (PostgreSQL, порты 3001/3002/5173/9222). Перед подъёмом
   проверь порты; чужой стенд не сноси, убирай только свой.
6. WORKTREE И `shared`. `node_modules` в worktree — junction на общее дерево: если
   typecheck ругается на `@matricarmz/shared`, пересобери его в СВОЁМ worktree:
   `corepack pnpm --filter @matricarmz/shared build`.
7. ПРАВКИ ФАЙЛОВ — файловыми инструментами (write/edit), НЕ через PowerShell-конвейер
   (`Get-Content | Set-Content` портит UTF-8 и кириллицу).
8. ДЕЛЕГИРОВАНИЕ субагентам не рассчитывай (пул MiniMax может быть исчерпан —
   «not enough credits»): разведку и проверки делай сам.
9. ПРОД не трогай: деплой и релиз — только по слову владельца.
%s
11. ДОКУМЕНТЫ репозитория: по правилам репо обнови то, что относится к твоей задаче
    (SESSION_HANDOFF / COMPLETED / PENDING). Чужие записи не выпиливай.
12. ФИНАЛ. Напечатай ИТОГОВУЮ СВОДКУ: что сделано (с номерами PR), что не сделано и почему,
    что дальше. После сводки остановись — сессию закроют, работу продолжит новая сессия.
13. ЯЗЫК. Все финальные сообщения, сводки и объяснения — на РУССКОМ (канон репозитория:
    «All final messages, summaries, explanations, and recommendations to the user must be
    in Russian»). Идентификаторы, имена файлов и команды — как есть.
""" % (lane["folder"], hot_text, dirty_text, BASE_BRANCH, BASE_BRANCH_SHORT, prompt_id,
       BASE_BRANCH, BASE_BRANCH, ARCHIVE_HINT % prompt_id)
    return head_text + "\n" + rules


# ────────────────────────────────────────────────────────── сессии

def area_dir(lane: dict) -> Path:
    return core.AREAS_ROOT / ("p-" + lane["slug"])


def lane_snapshot(lane: dict) -> dict:
    """Состояние полосы по базе рантайма: free / busy / done + список сессий."""
    adir = area_dir(lane)
    con = core.connect(core.area_db(adir))
    if not con:
        return {"status": "no_db", "sessions": []}
    try:
        sessions = [s for s in sw.sessions_of(adir)
                    if not s["archived"] and s["kind"] == "conversation"
                    and not sw.is_probe(con, s)]
        if not sessions:
            return {"status": "free", "sessions": []}
        s = sessions[0]
        turn = sw.last_turn(con, s["session_id"])
        final = sw.last_message(con, s["session_id"], final_only=True)
        status = ("busy" if turn.get("status") == "accepted" and not turn.get("completed_at_ms")
                  else "done" if turn.get("status") in ("completed", "failed") else "busy")
        return {"status": status, "sessions": sessions, "session": s,
                "turn": turn, "final": final}
    finally:
        con.close()


def start_session(lane: dict, prompt_text: str) -> bool:
    """Новая сессия в области с заданным текстом (текст идёт через stdin)."""
    env = core.area_env(area_dir(lane))
    cmd = [str(core.MCODE), "exec", "--cwd", lane["folder"], "--input", "-"]
    try:
        LOCAL.mkdir(parents=True, exist_ok=True)
        out = open(LOG, "a", encoding="utf-8")
    except OSError:
        out = subprocess.DEVNULL
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE,
                                stdout=out, stderr=out, creationflags=flags)
    except Exception as exc:  # noqa: BLE001
        log("ПОЛОСА %d: не удалось запустить mcode exec (%s)" % (lane["n"], exc))
        return False
    try:
        proc.stdin.write(prompt_text.encode("utf-8"))
        proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    log("ПОЛОСА %d: запущена сессия (pid %s), папка %s"
        % (lane["n"], proc.pid, lane["folder"]))
    return True


def close_session(lane: dict, session_id: str) -> bool:
    """Корректно закрыть сессию: попросить итоговую сводку и остановиться.

    ⚠️ `force=True` — НЕ лишний флаг (28.11.2026, находка по промту #184):
    сторож сессий не трогает сессии с маркером «ЗАДАНИЕ ОТ УПРАВЛЯЮЩЕГО», а
    карусель **сама** помечает так свои сессии полос ⇒ команда закрытия
    отклонялась («сессия Управляющего — автомат её не трогает»), сессия
    оставалась живой, а карусель подхватывала её снова — цикл «закрываю → не
    наша» (15 минут на попытку, 36 команд закрытия за 74 минуты).
    Карусель — водитель своих полос ⇒ команду она имеет право слать (force).
    """
    area = {"slug": lane["slug"], "dir": area_dir(lane), "folder": lane["folder"]}
    sess = None
    for s in sw.sessions_of(area_dir(lane)):
        if s["session_id"] == session_id:
            sess = s
            break
    if not sess:
        return False
    res = sw.send_to_session(area, sess, sw.CLOSE_COMMAND, background=True, force=True)
    log("ПОЛОСА %d: команда закрытия сессии %s — %s"
        % (lane["n"], session_id[:12], res.get("why", "")))
    return bool(res.get("ok"))


def archive_session(lane: dict, session_id: str) -> bool:
    res = sw.archive_session(area_dir(lane), session_id)
    log("ПОЛОСА %d: сессия %s архивирована (%s)"
        % (lane["n"], session_id[:12], res.get("why", "")))
    return bool(res.get("ok"))


def merged_pr_for(prompt_id: int, min_number: int = 0) -> int:
    """Номер слитого PR, где в теле есть «Промт КАРМАНА #<id>».

    `min_number` — порог номера PR: для ДОРАБОТКИ старый (уже слитый) PR не
    считается выполнением, выполнением считается только PR, созданный после
    начала доработки.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", "repos/%s/pulls?state=closed&per_page=100&sort=updated&direction=desc"
             % REPO],
            capture_output=True, encoding="utf-8", errors="replace", timeout=90)
    except Exception:  # noqa: BLE001
        return 0
    if proc.returncode != 0:
        return 0
    try:
        prs = json.loads(proc.stdout or "[]")
    except ValueError:
        return 0
    needle = ("промт кармана #%d" % prompt_id)
    for pr in prs:
        if not pr.get("merged_at"):
            continue
        number = int(pr.get("number") or 0)
        if number <= min_number:
            continue
        body = ((pr.get("body") or "") + " " + (pr.get("title") or "")).lower()
        if needle in body:
            return number
    return 0


def latest_pr_number() -> int:
    """Максимальный номер PR в репозитории — порог для доработки."""
    try:
        proc = subprocess.run(
            ["gh", "api", "repos/%s/pulls?state=all&per_page=1&sort=created&direction=desc" % REPO],
            capture_output=True, encoding="utf-8", errors="replace", timeout=90)
        if proc.returncode != 0:
            return 0
        prs = json.loads(proc.stdout or "[]")
        return int((prs[0].get("number") or 0)) if prs else 0
    except Exception:  # noqa: BLE001
        return 0


def prompt_archived(prompt_id: int) -> bool:
    """Промт помечен архивным? Смотрим СПИСОК АРХИВНЫХ (в живом его нет)."""
    data = karman_section(archived=True)
    if not data:
        return False
    for p in data.get("prompts") or []:
        if int(p.get("id") or 0) == prompt_id:
            return True
    return False


def verify_prompt(prompt_id: int, min_number: int = 0, rework: bool = False) -> tuple:
    """Промт выполнен? (да/нет, чем подтверждено).

    Для доработки (`rework`) старый архив/старый слитый PR не считаются
    выполнением: ждём новый PR (номер выше `min_number`) и новый архив.
    """
    if not rework and prompt_archived(prompt_id):
        return True, "архив в КАРМАНе"
    pr = merged_pr_for(prompt_id, min_number=min_number)
    if pr:
        if karman_archive(prompt_id):
            return True, "слитый PR #%d; архив проставлен супервизором" % pr
        return True, "слитый PR #%d (архив не проставился)" % pr
    if rework:
        return False, "нет нового слитого PR после начала доработки"
    return False, "ни архива в КАРМАНе, ни слитого PR"


# ────────────────────────────────────────────────────────── ход карусели

def prompt_id_from_title(title: str) -> int:
    m = PROMPT_ID_RE.search(title or "")
    return int(m.group(1)) if m else 0


def _same_dir(a: str, b: str) -> bool:
    """Одна и та же папка (сравнение по абсолютному пути, без учёта регистра)."""
    try:
        return (os.path.normcase(os.path.abspath(str(a).strip()))
                == os.path.normcase(os.path.abspath(str(b).strip())))
    except Exception:  # noqa: BLE001
        return False


def foreign_live_in_tree(lane: dict, ls: dict) -> str:
    """Живая ЧУЖАЯ сессия, работающая в НАШЕМ рабочем дереве (пусто — можно брать).

    Правило «один писатель на дерево» (ADR-0011, разбор владельца 26.11):
    в одном дереве водитель тот, кто занял его первым. Чужая живая сессия в
    нашей папке — значит, вторую мы не поднимаем и ждём. В РАЗНЫХ деревьях
    работа идёт параллельно (у полосы своя папка — отдельный клон/worktree).
    """
    adir = area_dir(lane)
    con = core.connect(core.area_db(adir))
    if not con:
        return ""
    try:
        ours = str((ls.get("cur") or {}).get("sid") or "")
        pending = int(ls.get("pending") or 0)
        for s in sw.sessions_of(adir):
            if s["archived"] or s["kind"] != "conversation":
                continue
            if s["session_id"] == ours:
                continue
            if pending and prompt_id_from_title(s.get("title") or "") == pending:
                continue                      # это наша сессия по промту
            sid_in_title = prompt_id_from_title(s.get("title") or "")
            if sid_in_title and sid_in_title in (ls.get("done") or []):
                continue                      # наша прошлая сессия (промт сдан)
            folder = str(s.get("workspace_dir") or "").strip()
            if not folder or not _same_dir(folder, lane["folder"]):
                continue                      # чужое дерево — не мешает
            turn = sw.last_turn(con, s["session_id"])
            if turn.get("status") == "accepted" and not turn.get("completed_at_ms"):
                return s["session_id"]
            if s.get("status") == "started":
                return s["session_id"]
            # недавно активна (включая «разбужена» автоматом управления
            # сессиями) — тоже занимает дерево: второй писатель там не нужен
            updated = int(s.get("updated_at_ms") or 0)
            if updated and (core.now_ms() - updated) < TREE_BUSY_WINDOW_MS:
                return s["session_id"]
    finally:
        con.close()
    return ""


def handle_lane(lane: dict, ls: dict, snap: dict, now: float) -> str:
    """Разбор состояния полосы. Возвращает: 'busy' | 'free'."""
    if snap["status"] == "no_db":
        log("ПОЛОСА %d: нет базы рантайма области" % lane["n"])
        return "busy"

    sessions = snap.get("sessions") or []
    pending = int(ls.get("pending") or 0)
    cur = ls.get("cur")
    by_sid = {s["session_id"]: s for s in sessions}

    # 1. наша сессия ищется по номеру промта в заголовке (надёжнее, чем «самая
    #    свежая»: в области могут жить чужие незакрытые сессии).
    if pending:
        found = None
        for s in sessions:
            if prompt_id_from_title(s.get("title") or "") == pending:
                found = s
                break
        if found is not None:
            if not cur or cur.get("sid") != found["session_id"]:
                cur = {"sid": found["session_id"], "prompt_id": pending, "phase": "run",
                       "since": now, "turn_at_close": ""}
                ls["cur"] = cur
                log("ПОЛОСА %d: сессия %s — наш промт #%d"
                    % (lane["n"], found["session_id"][:12], pending))
        elif cur is None:
            # сессия ещё не появилась в базе (только что стартовала) — ждём
            return "busy"
        else:
            # нашу сессию убрали из списка (мы её архивировали) — полоса свободна
            ls["cur"] = None
            return "free"

    # 1b. Наша осиротевшая сессия (промт сдан, а сессия ещё жива) — подхватываем
    #     и закрываем по обычному порядку: иначе она навсегда занимает дерево
    #     и следующая сессия идёт вторым писателем в тот же клон.
    adopted = False
    if not pending and cur is None:
        for s in sessions:
            pid = prompt_id_from_title(s.get("title") or "")
            if pid and pid in (ls.get("done") or []):
                cur = {"sid": s["session_id"], "prompt_id": pid, "phase": "run",
                       "since": now, "turn_at_close": ""}
                ls["cur"] = cur
                adopted = True
                log("ПОЛОСА %d: сессия %s — наша прошлая (промт #%d), закрываю"
                    % (lane["n"], s["session_id"][:12], pid))
                break

    # 2. своей сессии нет — полоса свободна.
    #    ⚠️ ЧУЖИЕ сессии (работа владельца в этой области) мы НЕ трогаем: ни
    #    закрываем, ни архивируем. Закрытие чужой сессии прерывает чужую работу —
    #    именно этим «потребовал завершения сессии» объяснялось, почему промт
    #    не дорабатывался. Старые завершённые убирает cleanup_area (только из
    #    панели, без команд сессии).
    if cur is None:
        ls["cur"] = None
        ls["dead_turn"] = ""
        return "free"

    sess = by_sid.get(cur.get("sid"))
    if sess is None:
        ls["cur"] = None
        ls["dead_turn"] = ""
        return "free"

    # ⚠️ КЛЮЧЕВОЕ (26.11): если промта в работе НЕТ (pending == 0) и сессия не
    #    наша осиротевшая — она нам НЕ принадлежит (её оставил прошлый заход
    #    или это работа владельца). Закрывать её нельзя: это ровно то
    #    «потребовал завершения сессии», из-за которого нитки обрывались.
    #    ⚠️ 28.11 (промт #184): проверка срабатывала и на ПОДХВАЧЕННОЙ нами
    #    осиротевшей сессии. Подхватили (cur выставлен) → на следующем проходе
    #    `adopted` уже False (подхват идёт только при cur is None) ⇒ «не наша» ⇒
    #    cur сброшен ⇒ фаза «закрываем» недостижима ⇒ сессия не закрыта ⇒
    #    на следующем проходе подхват снова ⇒ цикл (36 команд закрытия за
    #    74 минуты). Признак нашей осиротевшей — тот же, что и при подхвате:
    #    номер промта в заголовке есть в списке сданных.
    if not pending and not adopted:
        our_pid = prompt_id_from_title(sess.get("title") or "")
        if not (our_pid and our_pid in (ls.get("done") or [])):
            log("ПОЛОСА %d: сессия %s не наша (промта в работе нет) — не трогаю"
                % (lane["n"], str(cur.get("sid"))[:12]))
            ls["cur"] = None
            return "free"

    # страховка: наша сессия обязана называть наш промт в заголовке
    if prompt_id_from_title(sess.get("title") or "") != int(cur.get("prompt_id") or 0):
        log("ПОЛОСА %d: сессия %s не наш промт #%s — не трогаю"
            % (lane["n"], str(cur.get("sid"))[:12], cur.get("prompt_id")))
        ls["cur"] = None
        return "free"

    # ход нашей сессии (она может быть не самой свежей — читаем её отдельно)
    con = core.connect(core.area_db(area_dir(lane)))
    try:
        turn = sw.last_turn(con, cur["sid"])
        final = sw.last_message(con, cur["sid"], final_only=True)
    finally:
        con.close()
    busy = bool(turn.get("status") == "accepted" and not turn.get("completed_at_ms"))

    phase = cur.get("phase")
    if phase == "closing":
        new_turn = (turn.get("turn_id") or "") and turn.get("turn_id") != cur.get("turn_at_close")
        finished = turn.get("status") in ("completed", "failed")
        # ⚠️ Ход ещё идёт ⇒ сессия жива ⇒ архивировать её нельзя: команда
        #    закрытия могла прийти посреди работы (28.11: смена аккаунта
        #    лечащим ⇒ провайдер отказал ⇒ команда ушла в живой код). Агент
        #    после неё продолжает работу и сам допишет сводку — тогда и
        #    архивируем.
        if busy:
            return "busy"
        if (new_turn and finished) or now - (cur.get("since") or now) > CLOSE_WAIT_SEC:
            archive_session(lane, cur["sid"])
            ls["cur"] = None
            ls["dead_turn"] = ""
            return "free"
        return "busy"

    # phase == "run": ход промта идёт или завершился
    if busy:
        return "busy"

    # ⚠️ ХОД УПАЛ, А ИТОГОВОЙ СВОДКИ НЕТ ⇒ агент оборвался на полпути.
    #    Живой случай 28.11.2026, полоса 1, промт #176: в 14:03:27 лечащий
    #    сменил аккаунт области (очки кончались) ⇒ провайдер отказал ⇒ ход
    #    «failed». Карусель читала это как «сессия свободна» и через 40 секунд
    #    слала «Завершай работу в этой сессии правильно» В ЖИВОЙ КОД — агент
    #    продолжал работу, а команда висела в его переписке.
    #    Оборванный ход — это НЕ «сделано», это «нужно поднять»: поднимает
    #    сторож сессий («Продолжай»), карусель ждёт и не вмешивается.
    if turn.get("status") == "failed" and not sw.looks_finished(final.get("text") or ""):
        dead_since = int(turn.get("completed_at_ms") or 0) or int(cur.get("since") or now)
        if ls.get("dead_turn") != cur.get("sid"):
            log("ПОЛОСА %d: ход сессии %s упал, итоговой сводки нет — агент оборвался "
                "(чаще всего смена аккаунта лечащим посреди хода). Закрывать такую "
                "сессию НЕЛЬЗЯ — жду, пока поднимет сторож сессий"
                % (lane["n"], str(cur.get("sid"))[:12]))
            ls["dead_turn"] = cur.get("sid")
        if now - dead_since > DEAD_TURN_WAIT_SEC:
            log("ПОЛОСА %d: оборванный ход сессии %s не поднят за %d с — отдаю промт "
                "в новую сессию" % (lane["n"], str(cur.get("sid"))[:12], DEAD_TURN_WAIT_SEC))
            ls["cur"] = None
            ls["dead_turn"] = ""
            return "free"
        return "busy"
    ls["dead_turn"] = ""

    cur["phase"] = "closing"
    cur["turn_at_close"] = turn.get("turn_id") or ""
    cur["since"] = now
    close_session(lane, cur["sid"])
    return "busy"


def finish_prompt(lane: dict, ls: dict, prompt_id: int, now: float, st: dict = None) -> None:
    """Проверка выполнения промта после закрытия его сессии."""
    if not prompt_id:
        return
    rework = (st or {}).get("rework") or {}
    rec = rework.get(str(prompt_id)) or {}
    ok, why = verify_prompt(prompt_id, min_number=int(rec.get("after_pr") or 0),
                             rework=bool(rec))
    if ok:
        log("ПОЛОСА %d: промт #%d выполнен (%s)" % (lane["n"], prompt_id, why))
        ls["done"] = list(ls.get("done") or []) + [prompt_id]
        ls["pending"] = 0
        ls["tries"] = {k: v for k, v in (ls.get("tries") or {}).items() if k != str(prompt_id)}
        if rec and st is not None:
            rework.pop(str(prompt_id), None)
            st["rework"] = rework
            log("ПОЛОСА %d: ДОРАБОТКА промта #%d закрыта" % (lane["n"], prompt_id))
        return
    tries = ls.setdefault("tries", {})
    tries[str(prompt_id)] = int(tries.get(str(prompt_id), 0)) + 1
    if tries[str(prompt_id)] > MAX_REQUEUE:
        log("ПОЛОСА %d: промт #%d не выполнен (%s) — попытки исчерпаны, промт остаётся "
            "живым в КАРМАНе" % (lane["n"], prompt_id, why))
        ls["failed"] = list(ls.get("failed") or []) + [prompt_id]
        ls["pending"] = 0
        return
    log("ПОЛОСА %d: промт #%d не выполнен (%s) — возвращаю в очередь"
        % (lane["n"], prompt_id, why))
    ls["queue"] = [prompt_id] + list(ls.get("queue") or [])
    ls["pending"] = 0
    ls["not_before"] = now + RETRY_COOLDOWN_SEC


def cleanup_area(lane: dict, keep_sid: str = "") -> None:
    """Убрать из боковой панели старые завершённые сессии полосы — «чтобы не мешались»."""
    adir = area_dir(lane)
    con = core.connect(core.area_db(adir))
    if not con:
        return
    old = []
    try:
        for s in sw.sessions_of(adir):
            if s["archived"] or s["kind"] != "conversation" or s["session_id"] == keep_sid:
                continue
            if s["updated_at_ms"] and (core.now_ms() - s["updated_at_ms"]) < 3 * 3600 * 1000:
                continue
            turn = sw.last_turn(con, s["session_id"])
            if turn.get("status") == "accepted" and not turn.get("completed_at_ms"):
                continue          # живой ход — не трогаем
            old.append(s["session_id"])
    finally:
        con.close()
    for sid in old:
        sw.archive_session(adir, sid)
    if old:
        log("ПОЛОСА %d: убрал из панели %d старых завершённых сессий"
            % (lane["n"], len(old)))


def worker_alive() -> bool:
    """Жив ли рабочий процесс карусели (для режима «смотритель»).

    wmic на этой Windows уже нет — считаем процессы через CIM.
    """
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*matrica-night-carousel*' -and "
          "$_.CommandLine -notlike '*--watch*' -and "
          "$_.ProcessId -ne %d } | Measure-Object).Count" % os.getpid())
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=90)
    except Exception:  # noqa: BLE001
        return True   # не смогли проверить — считаем, что жив (не плодим дубли)
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line) > 0
    return True


def lane_has_session_for(lane: dict, prompt_id: int) -> bool:
    """Живая ли сессия этой полосы по данному промту (по номеру в заголовке).

    Нужна жуткарюсели: «сессия не появилась» — это про ВРЕМЯ, а не про факт.
    Если сессия жива (в том числе после перезапуска рабочего процесса), промт
    НЕ брошен: его надо подхватить, а не вернуть в очередь (иначе на дереве
    появятся два писателя, а старая сессия будет закрыта как «наша прошлая»).
    """
    adir = area_dir(lane)
    for s in sw.sessions_of(adir):
        if s["archived"] or s["kind"] != "conversation":
            continue
        if prompt_id_from_title(s.get("title") or "") == int(prompt_id):
            return True
    return False


def reap_stale_pending(st: dict, now: float) -> None:
    """Снять «залипший» промт: сессия не появилась — подтверждения не было.

    Грабля (28.11.2026, полосы 1 и 2 стояли сутки): `mcode exec` упал с
    «Sign in to MiniMax», сессия в базе области не появилась, а `pending`
    остался непустым ⇒ `handle_lane` вечно возвращал «занята», а номер
    промта оставался в `inflight` ⇒ из-за `conflicts(a, a) == True` ни одна
    полоса не могла взять промт из своей очереди. Карусель крутилась вхолостую.

    Лечение здесь: ждём таймаут появления сессии → снимаем `pending`,
    возвращаем промт в очередь той же полосы и чистим `inflight`.
    """
    inflight = st.setdefault("inflight", {})
    for lane in LANES:
        ls = st["lanes"][lane["slug"]]
        pending = int(ls.get("pending") or 0)
        if not pending or ls.get("cur"):
            continue
        since = float(ls.get("pending_since") or 0)
        if not since or (now - since) < PENDING_APPEAR_TIMEOUT_SEC:
            continue
        # ⚠️ Сессия жива? Тогда это не «брошено», а «сессия ещё не подхвачена»
        #    (перезапуск рабочего процесса, долгий старт). Её надо подхватить —
        #    вернуть промт в очередь значит поднять вторую сессию на том же
        #    дереве ⇒ два писателя в одном дереве.
        if lane_has_session_for(lane, pending):
            continue
        log("ПОЛОСА %d: промт #%d — сессия не появилась за %d с (значит, `mcode exec` "
            "упал) — снимаю «в работе» и возвращаю промт в очередь"
            % (lane["n"], pending, PENDING_APPEAR_TIMEOUT_SEC))
        ls["pending"] = 0
        ls["pending_since"] = 0
        ls["not_before"] = now + RETRY_COOLDOWN_SEC
        ls["queue"] = [pending] + [q for q in (ls.get("queue") or []) if q != pending]
        inflight.pop(str(pending), None)

    # «в работе» = ровно те промты, которые полосы отдали и ещё не отработали.
    # Всё прочее — залипшие записи прошлого захода: они блокируют выбор
    # (conflicts(a, a) == True) и уводят карусель вхолостую.
    real = {}
    for lane in LANES:
        ls = st["lanes"][lane["slug"]]
        p = int(ls.get("pending") or 0)
        if p:
            real[str(p)] = lane["slug"]
    for key in list(inflight.keys()):
        if key not in real:
            log("КАРУСЕЛЬ: промт #%s помечен «в работе», но ни одна полоса его не держит — снимаю"
                % key)
            inflight.pop(key, None)


def tick(st: dict) -> None:
    core.refresh_env()
    now = time.time()
    inflight = st.setdefault("inflight", {})
    refresh_queues(st)
    reap_stale_pending(st, now)

    # 1. что происходит в каждой полосе
    for lane in LANES:
        ls = st["lanes"][lane["slug"]]
        snap = lane_snapshot(lane)
        before = ls.get("cur") or {}
        result = handle_lane(lane, ls, snap, now)
        if result == "free" and before.get("prompt_id"):
            pid = int(before["prompt_id"])
            finish_prompt(lane, ls, pid, now, st)
            inflight.pop(str(pid), None)
            done_all = st.setdefault("done_all", [])
            if pid not in done_all:
                done_all.append(pid)
        cleanup_area(lane, keep_sid=(ls.get("cur") or {}).get("sid") or "")

    # 2. запускаем следующие промты в свободных полосах
    for lane in LANES:
        ls = st["lanes"][lane["slug"]]
        if ls.get("cur"):
            continue
        if now < (ls.get("not_before") or 0):
            continue

        busy_ids = [int(x) for x in inflight]
        queue = list(ls.get("queue") or [])
        reserve = list(st.get("reserve") or [])
        rework_all = st.get("rework") or {}
        rework_ids = set(int(k) for k in rework_all)
        pick = None
        source = None

        while True:
            pick = None
            source = None
            # сначала своя очередь полосы (порядок владельца: по возрастанию номеров)
            for cand in queue:
                if not any(conflicts(cand, b) for b in busy_ids):
                    pick, source = cand, "queue"
                    break
            # своя очередь кончилась (или её голову держит конфликт) — резерв
            if pick is None:
                for cand in reserve:
                    if not any(conflicts(cand, b) for b in busy_ids):
                        pick, source = cand, "reserve"
                        break
            if pick is None:
                break

            # уже выполнено? (архив в КАРМАНе или слитый PR) — не переделываем.
            # ⚠️ ИСКЛЮЧЕНИЕ — ДОРАБОТКА: промт в st["rework"] выполняется заново
            # (старый PR/архив ему не зачёт), и агент получает список остатков.
            if pick not in rework_ids and (prompt_archived(pick) or merged_pr_for(pick)):
                log("ПОЛОСА %d: промт #%d уже выполнен (архив/слитый PR) — пропускаю"
                    % (lane["n"], pick))
                ls["done"] = list(ls.get("done") or []) + [pick]
                if source == "queue":
                    queue.remove(pick)
                    ls["queue"] = queue          # ← запись обратно, иначе промт
                else:                            #   снова попадёт в очередь
                    reserve.remove(pick)          #   и карусель зациклится
                    st["reserve"] = reserve
                continue
            break

        if pick is None:
            continue   # в полосе нечего брать (очередь и резерв пусты)

        body = prompt_body(pick)
        if not body:
            log("ПОЛОСА %d: не нашёл текст промта #%d в КАРМАНе — пропускаю"
                % (lane["n"], pick))
            continue
        retry = bool(ls.get("tries", {}).get(str(pick)))
        rework_rec = rework_all.get(str(pick)) or {}
        rework_note = str(rework_rec.get("note") or "")

        # ⚠️ ОДИН ПИСАТЕЛЬ НА ДЕРЕВО: если наше дерево занято живой чужой
        #    сессией — вторую не поднимаем, ждём (водитель — тот, кто первый).
        blocked = foreign_live_in_tree(lane, ls)
        if blocked:
            if ls.get("tree_wait") != blocked:
                log("ПОЛОСА %d: дерево %s занято чужой живой сессией %s — жду "
                    "(водитель на проекте — она)" % (lane["n"], lane["folder"], blocked[:12]))
                ls["tree_wait"] = blocked
            continue
        ls["tree_wait"] = ""

        text = build_prompt(lane, pick, body, retry=retry, rework_note=rework_note)
        if rework_note:
            log("ПОЛОСА %d: промт #%d — ДОРАБОТКА (старый PR не зачёт, ждём новый)"
                % (lane["n"], pick))
        if start_session(lane, text):
            if source == "queue":
                queue.remove(pick)
                ls["queue"] = queue
            else:
                reserve.remove(pick)
                st["reserve"] = reserve
            ls["pending"] = pick
            ls["pending_since"] = now
            ls["cur"] = None
            inflight[str(pick)] = lane["slug"]
            log("ПОЛОСА %d: взят промт #%d (%s; в очереди полосы: %d, в резерве: %d)"
                % (lane["n"], pick, source, len(ls["queue"]), len(st["reserve"])))
        else:
            ls["queue"] = queue

    save_state(st)


def status(st: dict) -> None:
    print("=" * 78)
    print("Карусель Матрицы РМЗ — состояние на %s" % time.strftime("%Y-%m-%d %H:%M"))
    print("=" * 78)
    for lane in LANES:
        ls = st["lanes"][lane["slug"]]
        snap = lane_snapshot(lane)
        cur = ls.get("cur") or {}
        print("Полоса %d «%s» [%s]: %s | промт: %s | фаза: %s | очередь: %s | выполнено: %s"
              % (lane["n"], lane["title"], lane["slug"], snap["status"],
                 ls.get("pending") or "—", cur.get("phase") or "—",
                 ls.get("queue") or "[]", ls.get("done") or "[]"))
    print("Резерв (общий): %s" % (st.get("reserve") or []))
    print("В работе сейчас: %s" % (st.get("inflight") or {}))
    print("Журнал: %s" % LOG)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="один проход и выход")
    ap.add_argument("--status", action="store_true", help="только отчёт")
    ap.add_argument("--watch", action="store_true",
                    help="режим «смотритель»: поднимает рабочий процесс, если он упал")
    args = ap.parse_args()

    st = load_state()
    if args.status:
        status(st)
        return 0

    if args.watch:
        log("=== смотритель карусели запущен ===")
        while True:
            if not worker_alive():
                log("СМОТРИТЕЛЬ: рабочий процесс не найден — поднимаю заново")
                subprocess.Popen([sys.executable, str(SCRIPT_PATH)],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                time.sleep(90)
            time.sleep(60)
        return 0

    # ⚠️ Дубль рабочего процесса — типичное зло: два демона делят один файл
    #    состояния и открывают сессии наперегонки. Если рядом есть другой рабочий
    #    процесс ЭТОГО проекта — выходим молча.
    if worker_alive():
        log("рабочий процесс уже есть — второй не запускаю")
        return 0

    log("=== карусель Матрицы РМЗ запущена ===")
    while True:
        try:
            tick(st)
        except Exception as exc:  # noqa: BLE001
            log("ОШИБКА прохода: %r" % (exc,))
        if args.once:
            break
        time.sleep(POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
