#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ночная карусель «МиниГена» — та же машина, что и у Матрицы РМЗ, но свой проект.

Заказ владельца (26.11.2026, ночь): «запусти агента "МиниГен (разработка)" и давай ему
промты по кругу также как для проекта Матрица РМЗ… гоняй его всю ночь, пока промты для
Минигена не закончатся».

Каркас один — `matrica-night-carousel.py` (карусель: новая сессия → промт → проверка
выполнения → закрытие → архив сессии → следующий промт). Здесь переопределены только
проектные величины:

  * раздел библиотеки — `MiniGen` (11 живых промтов: #90, #92, #94, #96, #98, #100,
    #122, #129, #130, #131, #132);
  * полоса одна — рабочая папка `D:\\valstan\\REPO\\biblio-minigen` (worktree репозитория
    `biblio`), репозиторий для проверки «сделан» — `Valstan/biblio`;
  * источник промтов — канал Мозга (`PROMPTS_INGEST_SECRET`), потому что токен раздела
    `MiniGen` выпущен, но выдача в комнату-получатель (`karman-hotline`) ещё не принята
    ⇒ канал раздела отвечает 503. Как только выдача будет принята (клик владельца в
    КАРМАНе или письмо от КАРМАНА), архив заработает сам: супервизор пробует архивировать
    каждое выполненное задание.

Запуск:
    python C:\\Users\\valstan\\bin\\minigen-night-carousel.py            # демон
    python C:\\Users\\valstan\\bin\\minigen-night-carousel.py --status   # отчёт
    python C:\\Users\\valstan\\bin\\minigen-night-carousel.py --watch    # смотритель
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

# ── каркас карусели (один на все проекты)
_spec = importlib.util.spec_from_file_location("carousel_core", str(Path(__file__).resolve().parent / "matrica-night-carousel.py"))
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

# ── проектные величины «МиниГена»
core.PROJECT = "MiniGen"
core.REPO = "Valstan/biblio"
core.KEYS = Path.home() / ".minigen-keys"
core.STATE = LOCAL / "minigen-carousel-state.json"
core.LOG = LOCAL / "minigen-carousel.log"
core.LANES = [{
    "n": 1, "slug": "minigen", "title": "МиниГен (разработка)",
    # СВОЁ дерево для полосы (ADR-0011): `biblio-minigen` делят с сессией
    # владельца — там идёт приёмка окна, и два писателя в одном дереве
    # пересекаются. `biblio-mg2` — отдельный worktree репозитория `biblio`.
    "folder": r"D:\valstan\REPO\biblio-mg2",
    "queue": [90, 92, 94, 96, 98, 100, 122, 129, 130, 131, 132],
}]
core.RESERVE = []
core.CONFLICT_PAIRS = set()
core.HOT_FILES = {
    90: ["tools/minigen-gui/src/renderer/src/tabs/ChatTab.tsx",
         "tools/minigen-gui/src/renderer/src/api.ts",
         "tools/minigen-gui/src/main/index.ts", "tools/minigen-gui/src/main/bridge.ts"],
    92: ["tools/minigen-gui/bridge/minigen_state.py",
         "tools/minigen-gui/src/main/index.ts", "tools/minigen-gui/src/main/bridge.ts"],
    94: ["tools/minigen-gui/src/shared/types.ts",
         "tools/minigen-gui/src/renderer/src/tabs/AreasTab.tsx"],
    96: ["tools/minimax-project-areas/minimax-sessionwatch.py",
         "tools/minimax-project-areas/minigen_core.py",
         "tools/minimax-project-areas/minigen_rollout.py"],
    98: ["tools/minigen-gui/bridge/minigen_clone.py",
         "tools/minigen-gui/bridge/minigen_lineage.py"],
    100: ["karman/app/api/prompts/section/route.ts",
          "karman/lib/prompts/section-auth.ts",
          "karman/lib/ecosystem/runtime-secrets.ts",
          "karman/lib/ecosystem/vault-client.ts"],
    122: ["tools/minigen-gui/src/renderer/src/tabs/AgentsTab.tsx",
          "tools/minigen-gui/src/shared/types.ts"],
    129: ["tools/minigen-gui/bridge/minigen_agent.py",
          "tools/minigen-gui/src/renderer/src/tabs/ChatTab.tsx",
          "tools/minigen-gui/bridge/minigen_code.py",
          "tools/minigen-gui/src/renderer/src/App.tsx"],
    130: ["tools/minigen-gui/bridge/minigen_clone.py",
          "tools/minigen-gui/bridge/minigen_lineage.py",
          "tools/minigen-gui/bridge/minigen_state.py",
          "tools/minigen-gui/src/shared/types.ts"],
    131: ["tools/minigen-gui/bridge/minigen_genes.py",
          "tools/minigen-gui/bridge/minigen_clone.py",
          "tools/minigen-gui/src/renderer/src/api.ts",
          "tools/minigen-gui/tools/minigen-gui/install.ps1"],
    132: ["tools/minigen-gui/bridge/minigen_agents.py",
          "tools/minigen-gui/bridge/minigen_agent.py",
          "tools/minigen-gui/bridge/minigen_tools.py",
          "tools/minigen-gui/src/shared/types.ts"],
}
core.ARCHIVE_HINT = r"""10. АРХИВ ПРОМТА — по факту выполнения (архив = «сделано»). Токен раздела `MiniGen`    выпущен, но выдача в комнату-получатель (`karman-hotline`) ещё не принята, поэтому
    канал архива может ответить 503. Если так — не архивируй, напиши об этом в сводке:
    архив проставит Управляющий, когда канал откроется. Проверка:
    $env:PROMPTS_TOKEN_MINIGEN = (Get-Content "$env:USERPROFILE\\.minigen-keys\\prompts-token" -Raw).Trim()
    $env:KARMAN_URL = (Get-Content "$env:USERPROFILE\\.minigen-keys\\vault-url" -Raw).Trim()
    $env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'
    Invoke-RestMethod -Method Post -Uri "$env:KARMAN_URL/api/prompts/section" -Headers @{Authorization="Bearer $env:PROMPTS_TOKEN_MINIGEN"} -ContentType 'application/json' -Body '{"project":"MiniGen","id":%d,"archived":true}'
    (200 = промт в архиве; 503 = канал ещё не открыт; 403 = не тот раздел.)"""

#: У репозитория `biblio` ветка по умолчанию — `master` (не `main`).
core.BASE_BRANCH = "origin/master"
core.BASE_BRANCH_SHORT = "master"

#: Смотритель должен поднимать ЭТОТ скрипт, а не базовый каркас.
core.SCRIPT_PATH = Path(__file__).resolve()


# ── источник промтов: канал Мозга (токен раздела MiniGen ждёт приёма выдачи)
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
    """Раздел через канал Мозга (`PROMPTS_INGEST_SECRET`): читает библиотеку целиком."""
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
    """Сначала канал Мозга (работает сейчас), при его молчании — токен раздела."""
    data = _brain_section(archived=archived)
    if data is not None:
        return data
    return core.karman_section(archived=archived)


core.karman_section = karman_section


def worker_alive() -> bool:
    """Жив ли рабочий процесс именно этой карусели («МиниГен», не «Матрица»)."""
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*minigen-night-carousel*' -and "
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
