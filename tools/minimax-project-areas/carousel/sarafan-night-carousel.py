#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ночная карусель «Сарафана» — тот же движок, что и у Матрицы РМЗ и МиниГена.

Заказ владельца (26.11.2026, ночь): «запусти агента "Сарафан" и дай ему задание из
корзины промтов, пусть делает».

Каркас — `matrica-night-carousel.py` (карусель: новая сессия → промт → проверка
выполнения → закрытие → архив сессии → следующий промт). Здесь переопределены
проектные величины и **собственный блок правил** — у Сарафана другой стек
(Python/pytest, а не pnpm) и другая дисциплина (прод-гейт владельца).

  * раздел библиотеки — `SARAFAN`; живой промт один: **#161** «Перенос Сарафана на
    локальный компьютер: Windows, агенты и своя база»;
  * полоса одна — рабочая папка `D:\\valstan\\REPO\\sarafan`, репозиторий для проверки
    «сделан» — `Valstan/sarafan`, базовая ветка — `main`;
  * источник промтов — канал Мозга (`PROMPTS_INGEST_SECRET`): токен раздела лежит в
    комнате `setka` на боевом боксе, на этой машине его нет.

Запуск:
    python C:\\Users\\valstan\\bin\\sarafan-night-carousel.py            # демон
    python C:\\Users\\valstan\\bin\\sarafan-night-carousel.py --status   # отчёт
    python C:\\Users\\valstan\\bin\\sarafan-night-carousel.py --watch    # смотритель
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BIN = Path(r"C:\Users\valstan\bin")
LOCAL = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\valstan\AppData\Local")) / "mcode-web"

_spec = importlib.util.spec_from_file_location("carousel_core", str(Path(__file__).resolve().parent / "matrica-night-carousel.py"))
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

# ── проектные величины «Сарафана»
core.PROJECT = "SARAFAN"
core.REPO = "Valstan/sarafan"
core.KEYS = Path.home() / ".sarafan-keys"
core.STATE = LOCAL / "sarafan-carousel-state.json"
core.LOG = LOCAL / "sarafan-carousel.log"
core.LANES = [{
    "n": 1, "slug": "sarafan", "title": "Сарафан",
    "folder": r"D:\valstan\REPO\sarafan",
    "queue": [161],
}]
core.RESERVE = []
core.CONFLICT_PAIRS = set()
core.HOT_FILES = {
    161: ["docs/", "infra/", "database/models.py", "main.py", "tasks/celery_app.py",
          "web/templates", "web/api", "gateway_mcp/", "modules/minimax_engine.py",
          "modules/deepseek_client.py", "modules/opencode_engine.py", "config/opencode.py"],
}
core.BASE_BRANCH = "origin/main"
core.BASE_BRANCH_SHORT = "main"

#: Смотритель должен поднимать ЭТОТ скрипт, а не базовый каркас.
core.SCRIPT_PATH = Path(__file__).resolve()

#: Свои правила для этого проекта — канон Сарафана (Python, pytest, прод-гейт).
core.RULES_OVERRIDE = r"""Обязательные правила работы (канон репозитория Сарафана: AGENTS.md, ADR-0002,
#025 «деструктивные операции — только по слову владельца в том же ходу»):

1. ГРАНИЦЫ. Пишешь ТОЛЬКО в своей рабочей папке: %s
   В другие клоны не ходи, чужие правки не трогай, чужую ветку не переключай.
   Файлы этого промта (твоя территория):
%s%s
2. ВЕТКА И PR (один PR — одна задача). Начало: `git fetch origin`, затем
   `git checkout -b <type>/<slug> origin/main` (<type> — feat/fix/chore/docs/refactor).
   Прямой push в main запрещён. В теле PR обязательно напиши строку
   «Промт КАРМАНА #%d», границы (файлы/задача) и Test plan.
3. ГЕЙТ. Локально: `pre-commit run --all-files` и `.\venv\Scripts\python.exe -m pytest tests/ -q`.
   Сверяй и КОЛИЧЕСТВО собранных тестов: «12 passed» вместо ~4500 — это не зелёный прогон,
   а не тот путь или сломанный сбор. В CI обязательная проверка называется ровно
   `test (3.12)`: жди именно её (`gh pr checks <N> --required --watch`), а не «все чек��».
   `--admin` не помогает (enforce_admins: true), `--auto` в репозитории выключен.
   Мерж — только при зелёном гейте: `gh pr merge --squash --delete-branch --subject "<тема>" --body-file body.md --match-head-commit <sha>`
   (без `--subject`/`--body-file` он лезет в интерактивный редактор и в headless падает).
   Красный гейт — чинить, а не обходить.
4. ПРОД НЕ ТРОГАТЬ. Миграции с данными (ALTER/DROP/DELETE/UPDATE), ротация токенов,
   остановка юнитов, изменение конфигурации systemd, закрытие прод-сервера — только
   по явному слову владельца В ТОМ ЖЕ ХОДЕ, иначе не делать. Инвентаризация боевого
   контура, план переезда, локальный стенд, чтение с прода (в т.ч. pg_dump) — можно.
   Пункт «закрыть прод-сервер» из промта — это решение владельца: подготовь всё
   (инструкция отката, план выключения, проверки), но не выключай.
5. ЛОКАЛЬНЫЙ СТЕНД. Python 3.12 и venv, PostgreSQL, Redis, FastAPI и Celery — на этой
   машине, отдельной базой; продовую базу не трогай и локальные правки не выкатывай.
   Порты и учётные данные стенда — в `docs/` и в машине проекта, секреты в git не клади.
6. АРХИВ ПРОМТА — по факту выполнения (архив = «сделано»). Токен раздела `SARAFAN`
   лежит в комнате `setka` на боевом боксе; с этой машины его нет, поэтому архив
   проставит Управляющий (признак выполнения — слитый PR с пометкой «Промт КАРМАНА #161»).
   Если хочешь закрыть сам — с бокса: `ssh sarafan "cd ~/SETKA && python3 scripts/prompts_fetch.py --archive 161"`
   (токен там подхватывается из комнаты; если не подхватится — напиши это в сводку).
7. ПРАВКИ ФАЙЛОВ — файловыми инструментами (write/edit), НЕ через PowerShell-конвейер
   (`Get-Content | Set-Content` портит UTF-8 и кириллицу).
8. ДЕЛЕГИРОВАНИЕ субагентам не рассчитывай (пул MiniMax может быть исчерпан —
   «not enough credits»): разведку и проверки делай сам.
9. ДОКУМЕНТЫ репозитория: `docs/SESSION_HANDOFF.md` (нитка), `docs/PENDING_FOLLOWUPS.md`
   (новые техдолги), инвентаризация переезда — отдельным документом в `docs/`.
   Чужие записи не выпиливай.
10. ФИНАЛ. Напечатай ИТОГОВУЮ СВОДКУ: что сделано (с номерами PR), что не сделано и почему,
    что дальше. После сводки остановись — сессию закроют, работу продолжит новая сессия.
11. ЯЗЫК. Все финальные сообщения, сводки и объяснения — на РУССКОМ. Идентификаторы,
    имена файлов и команды — как есть.
"""


# ── источник промтов: канал Мозга (токен раздела лежит на боксе, здесь его нет)
def _brain_secrets() -> dict:
    out = {}
    try:
        raw = (Path.home() / ".brain_matrica" / "secrets.env").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _brain_section(archived: bool = False):
    sec = _brain_secrets()
    base = (sec.get("PROMPTS_URL") or "").rstrip("/")
    token = sec.get("PROMPTS_INGEST_SECRET") or ""
    if not base or not token:
        return None
    url = base + "/api/prompts?project=%s%s" % (core.PROJECT, "&archived=1" if archived else "")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def karman_section(archived: bool = False):
    data = _brain_section(archived=archived)
    if data is not None:
        return data
    return core.karman_section(archived=archived)


core.karman_section = karman_section


def worker_alive() -> bool:
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*sarafan-night-carousel*' -and "
          "$_.CommandLine -notlike '*--watch*' -and "
          "$_.ProcessId -ne %d } | Measure-Object).Count" % os.getpid())
    try:
        proc = core.subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                   capture_output=True, text=True, timeout=90)
    except Exception:  # noqa: BLE001
        return True
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line) > 0
    return True


core.worker_alive = worker_alive


if __name__ == "__main__":
    sys.exit(core.main())
