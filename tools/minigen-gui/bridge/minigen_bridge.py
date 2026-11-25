"""Мост «МиниГена»: JSON-lines канал между окном программы и боевым ядром.

Протокол — одна строка JSON на сообщение, в обе стороны:

    → {"id": "7", "cmd": "areas.list", "args": {}}
    ← {"id": "7", "ok": true, "data": {...}}
    ← {"id": "7", "ok": false, "error": "неизвестная команда: xxx"}

Почему мост на Python, а не на TypeScript: **логика сторожей не переписывается**
(заказ владельца, границы решения от 23.11). Демоны живут на Python, держат
базу рантайма SQLite и знают про грабли `G678`–`G681`. Окно спрашивает ядро —
и получает правду, а не свою копию.

Правила моста:

- **одна реализация**: чтение состояния — импорт боевых модулей, действия —
  вызов их же функций (`send_to_session`, `rotate_session`, `restart_area`),
  а не переписывание логики;
- **ничего не чинит «на всякий случай»**: команда делает ровно то, о чём
  её попросили, и возвращает факт;
- **безопасные действия — сразу, опасные — с подтверждением** (это уже
  сделано на стороне агента: `minigen_tools.py`);
- **секреты не ходят по каналу**: ключ провайдера читается на машине, в
  репозиторий не попадает.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import minigen_state as state          # noqa: E402
import minigen_tools as tools          # noqa: E402
import minigen_code as code            # noqa: E402
import minigen_clone as clone_mod      # noqa: E402
import minigen_genes as genes_mod      # noqa: E402
import minigen_map as map_mod          # noqa: E402
import minigen_update as update_mod    # noqa: E402
import minigen_daemons as daemons      # noqa: E402
import minigen_lock as minigen_lock    # noqa: E402
import minigen_lineage as lineage      # noqa: E402
import minigen_provider as provider_mod  # noqa: E402
import minigen_agents as agents_mod    # noqa: E402
import minigen_ingest as ingest        # noqa: E402
import minigen_karman as karman_mod    # noqa: E402
import minigen_repos as repos_mod      # noqa: E402
from minigen_agent import Agent, ModelUnavailable  # noqa: E402

POINTS_TTL_MS = 60_000


def _title_from(text: str) -> str:
    """Заголовок заявки из зачитки: первая строка, обрезанная до 90 знаков.

    Зачитка — голос, и первая фраза обычно и есть суть. Обрезаем, чтобы
    заголовок не уехал в простыню.
    """
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:90].rstrip()
    return "Без названия"

# «Ящик отчётов»: пометка «до какого отчёта нейросеть уже в курсе».
REPORTS_SEEN = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "minigen-gui" / "reports-seen.json")


def _run(cmd: list[str], timeout: int = 180) -> dict[str, Any]:
    """Запустить внешнюю команду и вернуть её вывод.

    ⚠️ Набор ключей ВСЕГДА одинаковый: `ok`, `code`, `out`, `err`. Раньше при
    таймауте возвращались `ok`/`error` без `err` — и вызывающий код падал с
    `KeyError: 'err'` (ловится в CI: там нет powershell, и ветка таймаута
    срабатывала).
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "",
                "err": "команда не ответила за %d с" % timeout}
    except OSError as exc:
        return {"ok": False, "code": -1, "out": "", "err": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "out": (proc.stdout or "").strip()[-4000:],
        "err": (proc.stderr or "").strip()[-2000:],
    }


def reply(req_id: Any, data: dict[str, Any]) -> dict[str, Any]:
    """Ответ моста по протоколу JSON-lines.

    ⚠️ Команда, которая **упала**, — это ошибка протокола (`ok: false`),
    а не «успешный» ответ с пустым содержимым. Иначе окно получало вместо
    снимка пустоту и молча показывало «0 областей», «демоны 0/0» — а что
    сломалось, не говорило (живой случай 24.11).
    """
    if isinstance(data, dict) and data.pop("__failed__", False):
        return {"id": req_id, "ok": False,
                "error": str(data.get("error") or "команда не сработала")}
    return {"id": req_id, "ok": True, "data": data}


def _copy_area_template(source: Path, target: Path) -> tuple[int, list[str]]:
    """Скопировать шаблон области, пережив занятые файлы.

    Шаблон авторизации — **живая область**: часть её файлов (Cookies
    электрона и подобное) может быть занята процессом. Раньше это роняло
    создание области целиком (`shutil.copytree` → `WinError 32`), а теперь мы
    копируем всё, что читается, а занятое — пропускаем и называем в отчёте.

    Возвращает (скопировано, пропущено).
    """
    copied = 0
    skipped: list[str] = []
    for root, _dirs, files in os.walk(str(source)):
        rel = Path(root).relative_to(source)
        (target / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            src = Path(root) / name
            dst = target / rel / name
            try:
                shutil.copyfile(str(src), str(dst))
                copied += 1
            except OSError:
                skipped.append(str(rel / name).replace("\\", "/"))
    return copied, skipped


def _published_prompt_id(project: str, title: str, text: str) -> Any:
    """id уже опубликованной заявки в разделе (пусто — такой ещё нет).

    Публикация должна быть **идемпотентной**: повторный заход той же
    заявкой не должен плодить копии в разделе (владелец увидит их как
    дубли и не поймёт, что делать).
    """
    code, payload = karman_mod.call("GET", "/api/prompts?project=%s"
                                    % urllib.parse.quote(project))
    if code != 200:
        return ""
    for item in payload.get("prompts") or []:
        if not isinstance(item, dict):
            continue
        same_title = str(item.get("title") or "").strip() == str(title or "").strip()
        same_body = str(item.get("body") or "").strip() == str(text or "").strip()
        if same_title and same_body:
            return item.get("id")
    return ""


class Bridge:
    """Программа «МиниГен» без окна: команды, состояние, чат."""

    def __init__(self) -> None:
        self.mods = state.load_daemons()
        self.core = self.mods["core"]
        self.tools = tools.TOOLS
        self.agent = Agent(self.dispatch, self.tools,
                           token_provider=self._area_token,
                           settings_provider=state.read_settings)
        self.chat_history: list[dict[str, Any]] = []
        self._points: dict[str, dict[str, Any]] = {}
        self._points_at: dict[str, int] = {}
        self._points_lock = threading.Lock()
        self._daemon_stop = threading.Event()
        self._daemon_thread: threading.Thread | None = None
        self.handlers = self._build_handlers()
        self.start_daemon_supervision()

    def start_daemon_supervision(self) -> None:
        """Следить за демонами: поднимать упавшего.

        Старая панель архивирована (23.11) — сторожей держит программа. Пока
        программа жива, упавший демон поднимается в течение минуты; если
        программа закрыта, демон поднимает себя сам (`respawn_self`).

        ⚠️ Надзиратель — **один** (`minigen_lock`): и фоновая копия, и окно
        программы начинают следить, но блокировку получает только один — тот
        и надзирает. Если её держит другой (например, жива фоновая копия, а
        владелец открыл окно), окно просто показывает состояние, не мешая
        надзирателю.
        """
        if not state.read_settings().get("manageDaemons", True):
            return
        if self._daemon_thread is not None:
            return
        self._daemon_thread = threading.Thread(
            target=daemons.supervise_loop,
            args=(self._daemon_stop, minigen_lock.get_lock()), daemon=True)
        self._daemon_thread.start()

    # ───────────────────────────────────────────────────────── реестр команд
    def _build_handlers(self) -> dict[str, Any]:
        return {
            "state.snapshot": self.cmd_snapshot,
            "areas.list": self.cmd_areas_list,
            "areas.state": self.cmd_areas_state,
            "areas.points": self.cmd_areas_points,
            "areas.set_autoheal": self.cmd_set_autoheal,
            "areas.start": self.cmd_start,
            "areas.stop": self.cmd_stop,
            "areas.restart": self.cmd_restart,
            "areas.relogin": self.cmd_relogin,
            "areas.create": self.cmd_create,
            "sessions.list": self.cmd_sessions_list,
            "sessions.read": self.cmd_sessions_read,
            "sessions.send": self.cmd_sessions_send,
            "sessions.rotate": self.cmd_sessions_rotate,
            "sessions.close": self.cmd_sessions_close,
            "network.check": self.cmd_network_check,
            "journal.read": self.cmd_journal_read,
            "stats.summary": self.cmd_stats_summary,
            "settings.get": self.cmd_settings_get,
            "settings.set": self.cmd_settings_set,
            "daemons.status": self.cmd_daemons_status,
            "chat.send": self.cmd_chat_send,
            "chat.confirm": self.cmd_chat_confirm,
            "chat.reset": self.cmd_chat_reset,
            "app.open_folder": self.cmd_open_folder,
            # самообновление программы
            "code.list": self.cmd_code_list,
            "code.read": self.cmd_code_read,
            "code.search": self.cmd_code_search,
            "code.branch": self.cmd_code_branch,
            "code.patch": self.cmd_code_patch,
            "code.tests": self.cmd_code_tests,
            "code.diff": self.cmd_code_diff,
            "code.commit": self.cmd_code_commit,
            "code.pr": self.cmd_code_pr,
            "code.discard": self.cmd_code_discard,
            # демоны «МиниГена» (старая панель архивирована — держит программа)
            "daemons.status": self.cmd_daemons_status,
            "daemons.start": self.cmd_daemons_start,
            "daemons.stop": self.cmd_daemons_stop,
            "daemons.restart": self.cmd_daemons_restart,
            # версия программы
            "program.version": self.cmd_program_version,
            # модель нейросети: каталог (родные настройки платформы) и
            # применение выбора к рабочим областям
            "model.catalog": self.cmd_model_catalog,
            "model.apply": self.cmd_model_apply,
            # сброс сессии на месте (заказ владельца 24.11)
            "sessions.reset": self.cmd_sessions_reset,
            # «ящик отчётов»: сторожи и инструменты отчитываются туда,
            # нейросеть читает (заказ владельца 24.11)
            "reports.list": self.cmd_reports_list,
            # родословная копии: кто эта копия и от какой версии форк
            "lineage.show": self.cmd_lineage_show,
            # карта паутины копий и генов (заказ владельца 24.11)
            "map.show": self.cmd_map_show,
            # свой клон копии на GitHub (заказ владельца 24.11)
            "clone.status": self.cmd_clone_status,
            "clone.setup": self.cmd_clone_setup,
            "clone.sync": self.cmd_clone_sync,
            # пул генов: обмен наработками между копиями
            "genes.list": self.cmd_genes_list,
            "genes.read": self.cmd_genes_read,
            "genes.publish": self.cmd_genes_publish,
            "genes.adopt": self.cmd_genes_adopt,
            # обновление программы из окна (заказ владельца 24.11)
            "update.status": self.cmd_update_status,
            "update.check": self.cmd_update_check,
            "update.start": self.cmd_update_start,
            # агенты «МиниГена»: реестр, создание, восстановление (24.11)
            "agents.list": self.cmd_agents_list,
            "agents.state": self.cmd_agents_state,
            "agents.create": self.cmd_agents_create,
            "agents.apply": self.cmd_agents_apply,
            "agents.registry_restore": self.cmd_agents_registry_restore,
            # заявки: зачитка владельца → заявка для другой нейросети (24.11)
            "ingest.draft": self.cmd_ingest_draft,
            "ingest.save": self.cmd_ingest_save,
            # чужие репозитории — только чтение (24.11)
            "repos.list": self.cmd_repos_list,
            "repos.read": self.cmd_repos_read,
            "repos.profile": self.cmd_repos_profile,
        }

    # Команды, чей вызов **не** отчитывается в «ящик отчётов»: иначе
    # нейросеть получала бы отчёты о собственных вопросах и о чтении
    # отчётов (шум вместо пользы).
    # ⚠️ `update.status` тоже: окно опрашивает его раз в секунду — в ящике
    # отчётов было бы тысячи одинаковых записей.
    NO_REPORT_CMDS = ("chat.", "reports.", "update.status")

    def dispatch(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        handler = self.handlers.get(cmd)
        if handler is None:
            return {"error": "неизвестная команда: %s" % cmd, "__failed__": True}
        try:
            result = handler(args or {})
        except Exception as exc:  # noqa: BLE001
            # Помечаем: команда УПАЛА. Протокол обязан сказать об этом
            # честно (`ok: false`), а не отдать «успешный» пустой ответ —
            # иначе окно показывает нули и молчит о поломке (живой случай
            # 24.11: `fmt_ms()` — TypeError, а в шапке «0 областей»).
            result = {"error": "%s: %s" % (type(exc).__name__, exc), "__failed__": True}
        self._report_call(cmd, result)
        return result

    def _report_call(self, cmd: str, result: dict[str, Any]) -> None:
        """Отчёт о работе инструмента — в «ящик отчётов» (заказ 24.11).

        Инструменты сами рассказывают нейросети, что сделали и где ошиблись.
        """
        if cmd.startswith(self.NO_REPORT_CMDS):
            return
        try:
            error = str((result or {}).get("error") or "")
            if error:
                text = "%s: %s" % (cmd, error)
                level = "error"
            else:
                ok = (result or {}).get("ok")
                text = "%s: %s" % (cmd, "ок" if ok is not False else "сделано")
                level = "info"
            self.core.report(level, "tool", text)
        except Exception:  # noqa: BLE001
            pass        # отчёт — дополнительный канал, его поломка не важна

    # ────────────────────────────────────────────────────────────── состояние
    def _area_token(self, slug: str) -> str:
        """Рабочий токен области (из стора авторизации).

        Именно он ходит к вендору за моделью: ключ из `config.yaml` — заглушка.
        Токен протухает (8 ч), его обновляет лечащий; протух ⇒ модель
        недоступна, и чат честно об этом говорит.
        """
        switch = self.mods.get("switch")
        if switch is None:
            return ""
        area = state.area_by_slug(self.mods, slug)
        if not area:
            return ""
        try:
            store = switch.read_store(area["dir"])
            records = store.get("records") or {}
            own = records.get(switch.store_key(area["dir"]))
            candidates = [own] if isinstance(own, dict) else []
            candidates += [rec for rec in records.values() if isinstance(rec, dict)]
            alive = [rec for rec in candidates if rec.get("accessToken")]
            if not alive:
                return ""
            alive.sort(key=lambda rec: int(rec.get("expiresAtMs") or 0), reverse=True)
            return str(alive[0].get("accessToken") or "")
        except Exception:  # noqa: BLE001
            return ""

    def cmd_snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        snap = state.snapshot(self.mods,
                              include_sessions=bool(args.get("sessions", True)))
        # Очки — из кэша моста (его наполняет кнопка «Обновить очки»).
        # ⚠️ 24.11: без этого столбец «Очки» в окне всегда был «—», и
        # сортировать по нему было нечего.
        for area in snap.get("areas") or []:
            area["points"] = self._points.get(area["slug"])
        return snap

    def cmd_areas_list(self, args: dict[str, Any]) -> dict[str, Any]:
        areas = state.areas_state(self.mods,
                                  include_sessions=bool(args.get("sessions", True)))
        for area in areas:
            area["points"] = self._points.get(area["slug"])
        return {"areas": areas}

    def cmd_areas_state(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = str(args.get("slug") or "")
        area = state.area_by_slug(self.mods, slug)
        if not area:
            return {"error": "нет такой области: %s" % slug}
        item = {
            "slug": area["slug"],
            "title": area["title"],
            "folder": area.get("folder") or "",
            "autoheal": bool(area.get("autoheal")),
            "running": area["dir"].name in (self.core.running_areas() or set()),
            "account": state.local_account(self.mods, area["dir"]),
            "sessions": state.session_verdicts(self.mods, area),
        }
        if self.mods.get("autheal"):
            try:
                item["diagnosis"] = state.jsonable(self.mods["autheal"].diagnose(area))
            except Exception:  # noqa: BLE001
                item["diagnosis"] = {}
        return item

    def cmd_areas_points(self, args: dict[str, Any]) -> dict[str, Any]:
        """Очки и живое состояние входа — по вендору (медленно, поэтому в фоне)."""
        slugs = args.get("slugs")
        if not slugs:
            slugs = [a["slug"] for a in self.core.areas()]
        slugs = [str(s) for s in slugs]
        now = int(time.time() * 1000)
        fresh = [s for s in slugs
                 if now - int(self._points_at.get(s, 0)) < POINTS_TTL_MS]
        stale = [s for s in slugs if s not in fresh]
        if stale:
            threading.Thread(target=self._refresh_points, args=(stale,),
                             daemon=True).start()
        return {
            "points": {s: self._points.get(s) for s in slugs},
            "pending": stale,
            "at": now,
        }

    def _refresh_points(self, slugs: list[str]) -> None:
        switch = self.mods.get("switch")
        if switch is None:
            return
        for slug in slugs:
            area = state.area_by_slug(self.mods, slug)
            if not area:
                continue
            with self._points_lock:
                if slug in self._points:
                    continue
            try:
                st = switch.account_state(area["dir"])
            except Exception:  # noqa: BLE001
                continue
            with self._points_lock:
                self._points[slug] = state.jsonable(st)
                self._points_at[slug] = int(time.time() * 1000)

    # ────────────────────────────────────────────────────────── обслуживание
    def cmd_set_autoheal(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = str(args.get("slug") or "")
        want = bool(args.get("autoheal"))
        cfg = state.read_projects()
        projects = cfg.get("projects") or []
        target = next((p for p in projects if p.get("slug") == slug), None)
        if target is None:
            return {"error": "нет такой области: %s" % slug}
        if bool(target.get("autoheal")) == want:
            return {"ok": True, "autoheal": want, "note": "уже так"}
        target["autoheal"] = want
        ok, err = state.write_projects(cfg)
        if not ok:
            return {"error": "конфиг не записан: %s" % err}
        self.core.log("Панель «МиниГен»: %s — режим обслуживания %s" % (
            slug, "АВТОМАТ" if want else "ВРУЧНУЮ"))
        return {"ok": True, "autoheal": want}

    def _ps_script(self, name: str) -> Path:
        return self.core.BIN / name

    def cmd_start(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = str(args.get("slug") or "")
        if not slug:
            return {"error": "не указана область"}
        script = self._ps_script("mcode-project-start.ps1")
        if not script.is_file():
            return {"error": "нет скрипта запуска: %s" % script}
        res = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-WindowStyle", "Hidden", "-File", str(script), "-Project", slug],
                   timeout=240)
        started = False
        if self.mods.get("autheal"):
            started = bool(self.mods["autheal"].area_process_ids(slug))
        self.core.log("Панель «МиниГен»: десктоп %s — старт%s" % (
            slug, " (окно поднялось)" if started else " (процесс не виден)"))
        return {"ok": res["ok"] and started, "started": started,
                "out": res["out"], "err": res["err"]}

    def cmd_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = str(args.get("slug") or "")
        if not slug:
            return {"error": "не указана область"}
        script = self._ps_script("mcode-project-stop.ps1")
        if not script.is_file():
            return {"error": "нет скрипта остановки: %s" % script}
        res = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-WindowStyle", "Hidden", "-File", str(script), "-Project", slug],
                   timeout=240)
        stopped = True
        killed = 0
        if self.mods.get("autheal"):
            stopped = self.mods["autheal"].wait_area_stopped(slug, 30)
            if not stopped:
                killed = self.mods["autheal"].kill_area_processes(slug)
                stopped = self.mods["autheal"].wait_area_stopped(slug, 20)
        self.core.log("Панель «МиниГен»: десктоп %s — стоп%s" % (
            slug, "" if stopped else " (НЕ ушёл!)"))
        return {"ok": stopped, "killed": killed, "out": res["out"], "err": res["err"]}

    def cmd_restart(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = str(args.get("slug") or "")
        if not slug or not self.mods.get("autheal"):
            return {"error": "нечем перезапускать"}
        self.mods["autheal"].restart_area(slug)
        running = bool(self.mods["autheal"].area_process_ids(slug))
        self.core.log("Панель «МиниГен»: десктоп %s — перезапуск%s" % (
            slug, "" if running else " (не поднялся)"))
        return {"ok": running, "running": running}

    def cmd_relogin(self, args: dict[str, Any]) -> dict[str, Any]:
        slug = str(args.get("slug") or "")
        switch = self.core.BIN / "minimax-switch.py"
        if not switch.is_file():
            return {"error": "нет %s" % switch}
        res = _run([sys.executable, str(switch), "pull", "p-" + slug, "--apply"],
                   timeout=300)
        self.core.log("Панель «МиниГен»: область %s — новый вход (токен)" % slug)
        return {"ok": res["ok"], "out": res["out"], "err": res["err"]}

    def cmd_create(self, args: dict[str, Any]) -> dict[str, Any]:
        """Новая область: каталог данных + авторизация + запись в конфиг.

        `resume=True` — каталог области уже есть, но проект в конфиге не
        заведён (область недоделана: копирование шаблона упало на занятом
        файле). Тогда шаблон **доносится** в существующий каталог, а проект
        регистрируется.
        """
        slug = str(args.get("slug") or "").strip().lower()
        title = str(args.get("title") or slug).strip()
        folder = str(args.get("folder") or "").strip()
        karman = str(args.get("karman") or slug).strip()
        if not slug or not all(ch.isalnum() or ch in "-_" for ch in slug):
            return {"error": "slug: только латиница, цифры, дефис и подчёркивание"}
        cfg = state.read_projects()
        projects = cfg.get("projects") or []
        if any(p.get("slug") == slug for p in projects):
            return {"error": "область %s уже есть" % slug}
        area_dir = state.AREAS_ROOT / ("p-" + slug)
        resume = bool(args.get("resume"))
        if area_dir.exists() and not resume:
            return {"error": "каталог %s уже есть" % area_dir}
        template = str(cfg.get("authTemplate") or "")
        copied = ""
        skipped: list[str] = []
        if template and Path(template).is_dir():
            count, skipped = _copy_area_template(Path(template), area_dir)
            copied = template
        else:
            area_dir.mkdir(parents=True, exist_ok=True)
        projects.append({
            "slug": slug, "title": title, "folder": folder,
            "karman": karman, "autoheal": True,
        })
        ok, err = state.write_projects(cfg)
        if not ok:
            return {"error": "конфиг не записан: %s" % err}
        # Ярлык на рабочем столе — той же командой, что и для остальных
        # областей (своя реализация = вторая правда).
        shortcut_note = ""
        maker = self.core.BIN / "make-mcode-project-shortcuts.ps1"
        if maker.is_file():
            res = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-WindowStyle", "Hidden", "-File", str(maker)], timeout=180)
            shortcut_note = ("ярлыки пересозданы" if res["ok"]
                             else "ярлыки не пересозданы: %s" % (res["err"] or "")[:120])
        else:
            shortcut_note = "нет скрипта ярлыков: %s" % maker
        folder_note = ""
        if folder and not Path(folder).is_dir():
            folder_note = ("рабочая папка не найдена: %s — создай её (или git worktree) "
                           "до запуска области" % folder)
        self.core.log("Панель «МиниГен»: создана область %s (%s) — %s" % (
            slug, title, shortcut_note or "без ярлыков"))
        return {"ok": True, "slug": slug, "dir": str(area_dir),
                "auth_from": copied, "folder": folder,
                "shortcut": shortcut_note, "folder_note": folder_note,
                "skipped": skipped}

    # ────────────────────────────────────────────────────────────── сессии
    def _area(self, slug: str) -> dict[str, Any]:
        area = state.area_by_slug(self.mods, slug)
        if not area:
            raise ValueError("нет такой области: %s" % slug)
        return area

    def _session(self, area: dict[str, Any], session_id: str) -> dict[str, Any]:
        watcher = self.mods.get("sessionwatch")
        if watcher is None:
            raise ValueError("сторож сессий недоступен")
        for sess in watcher.sessions_of(area["dir"]):
            if sess["session_id"] == session_id or sess["session_id"].startswith(session_id):
                return sess
        raise ValueError("сессия не найдена: %s" % session_id)

    def cmd_sessions_list(self, args: dict[str, Any]) -> dict[str, Any]:
        area = self._area(str(args.get("slug") or ""))
        return {"slug": area["slug"],
                "sessions": state.session_verdicts(self.mods, area)}

    def cmd_sessions_read(self, args: dict[str, Any]) -> dict[str, Any]:
        area = self._area(str(args.get("slug") or ""))
        watcher = self.mods.get("sessionwatch")
        session_id = str(args.get("session_id") or "")
        if watcher is None:
            return {"error": "сторож сессий недоступен"}
        con = self.core.connect(self.core.area_db(area["dir"]))
        if not con:
            return {"error": "нет базы области"}
        try:
            sess = self._session(area, session_id)
            rows = self.core.rows(
                con, "select role, created_at_ms, data_json from local_runtime_message_rows "
                     "where session_id=? order by id desc limit ?",
                (sess["session_id"], int(args.get("last") or 20)))
            out = []
            for role, at, data in reversed(rows):
                try:
                    payload = json.loads(data or "{}")
                except ValueError:
                    continue
                text = (payload.get("msg_content") or "").strip()
                if not text:
                    continue
                out.append({"role": role, "at": at, "text": text[:2000]})
            return {"session_id": sess["session_id"], "title": sess["title"],
                    "messages": out}
        finally:
            con.close()

    def cmd_sessions_send(self, args: dict[str, Any]) -> dict[str, Any]:
        area = self._area(str(args.get("slug") or ""))
        watcher = self.mods.get("sessionwatch")
        if watcher is None:
            return {"error": "сторож сессий недоступен"}
        text = str(args.get("text") or "").strip()
        if not text:
            return {"error": "пустое сообщение"}
        sess = self._session(area, str(args.get("session_id") or ""))
        res = watcher.send_to_session(area, sess, text, background=False)
        self.core.log("Панель «МиниГен»: %s — сообщение в сессию %s" % (
            area["slug"], sess["session_id"][:12]))
        return state.jsonable(res)

    def cmd_sessions_close(self, args: dict[str, Any]) -> dict[str, Any]:
        area = self._area(str(args.get("slug") or ""))
        watcher = self.mods.get("sessionwatch")
        if watcher is None:
            return {"error": "сторож сессий недоступен"}
        sess = self._session(area, str(args.get("session_id") or ""))
        res = watcher.send_to_session(area, sess, watcher.CLOSE_COMMAND, background=True)
        self.core.log("Панель «МиниГен»: %s — закрытие сессии %s" % (
            area["slug"], sess["session_id"][:12]))
        return state.jsonable(res)

    def cmd_sessions_rotate(self, args: dict[str, Any]) -> dict[str, Any]:
        area = self._area(str(args.get("slug") or ""))
        watcher = self.mods.get("sessionwatch")
        if watcher is None:
            return {"error": "сторож сессий недоступен"}
        sess = self._session(area, str(args.get("session_id") or ""))
        rep = watcher.rotate_session(area, sess)
        self.core.log("Панель «МиниГен»: %s — ротация сессии %s" % (
            area["slug"], sess["session_id"][:12]))
        return state.jsonable(rep)

    def cmd_sessions_reset(self, args: dict[str, Any]) -> dict[str, Any]:
        """Сбросить сессию НА МЕСТЕ: правильно завершить → очистить → продолжить.

        Заказ владельца (24.11): не открывать новую сессию, а в этой же —
        сессия сама доходит до конца и печатает сводку, рантайм сжимает
        контекст, и та же сессия продолжает нитку.
        """
        area = self._area(str(args.get("slug") or ""))
        watcher = self.mods.get("sessionwatch")
        if watcher is None:
            return {"error": "сторож сессий недоступен"}
        sess = self._session(area, str(args.get("session_id") or ""))
        reset = getattr(watcher, "reset_session", None)
        if reset is None:
            return {"error": "сброс на месте в этой версии сторожа нет"}
        rep = reset(area, sess)
        if not rep.get("ok"):
            # откат на прежнее поведение — ротацию
            rep = watcher.rotate_session(area, sess)
            rep["fallback"] = "ротация"
        self.core.log("Панель «МиниГен»: %s — сброс сессии %s на месте: %s" % (
            area["slug"], sess["session_id"][:12], rep.get("why")))
        return state.jsonable(rep)

    # ──────────────────────────────────────────────────────────────── сеть
    def cmd_network_check(self, args: dict[str, Any]) -> dict[str, Any]:
        self.core.refresh_env()
        return state.jsonable(state.network_state(self.mods, probe=True))

    # ───────────────────────────────────────────────────────────── журнал
    def cmd_journal_read(self, args: dict[str, Any]) -> dict[str, Any]:
        lines = state.read_journal(
            self.mods,
            limit=int(args.get("limit") or 200),
            contains=str(args.get("contains") or ""),
            tag=str(args.get("tag") or ""),
            since=str(args.get("since") or ""))
        return {"lines": lines, "path": str(state.journal_path(self.mods))}

    def cmd_stats_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        return state.jsonable(state.stats_summary(self.mods,
                                                  days=int(args.get("days") or 7)))

    # ──────────────────────────────────────────────────────── ящик отчётов
    def cmd_genes_list(self, args: dict[str, Any]) -> dict[str, Any]:
        """Гены пула: какие наработки есть у копий."""
        return {"genes": state.jsonable(genes_mod.list_genes())}

    def cmd_genes_read(self, args: dict[str, Any]) -> dict[str, Any]:
        """Один ген: манифест, заметка и diff."""
        return state.jsonable(genes_mod.read_gene(str(args.get("gene") or "")))

    def cmd_genes_publish(self, args: dict[str, Any]) -> dict[str, Any]:
        """Опубликовать свою наработку в пул (уходит в общий репозиторий)."""
        return state.jsonable(genes_mod.publish(
            title=str(args.get("title") or ""),
            summary=str(args.get("summary") or ""),
            notes=str(args.get("notes") or "")))

    def cmd_genes_adopt(self, args: dict[str, Any]) -> dict[str, Any]:
        """Внедрить чужой ген: ветка → проверки → PR (решение за владельцем)."""
        return state.jsonable(genes_mod.adopt(
            gene=str(args.get("gene") or ""),
            title=str(args.get("title") or "")))

    def cmd_clone_status(self, args: dict[str, Any]) -> dict[str, Any]:
        """Свой клон копии: где он, какие репозитории, что в родословной."""
        return state.jsonable(clone_mod.status())

    def cmd_clone_setup(self, args: dict[str, Any]) -> dict[str, Any]:
        """Завести репозиторий копии на GitHub и взять клон (+ пул генов).

        Опасное действие: создаёт репозитории в аккаунте владельца — только
        после подтверждения.
        """
        return state.jsonable(clone_mod.setup(
            upstream=str(args.get("upstream") or clone_mod.DEFAULT_UPSTREAM),
            pool=str(args.get("pool") or clone_mod.DEFAULT_POOL),
            private=bool(args.get("private", True))))

    def cmd_clone_sync(self, args: dict[str, Any]) -> dict[str, Any]:
        """Обновить клон и пул (fast-forward, локальные правки не трогаем)."""
        return state.jsonable(clone_mod.sync())

    def cmd_lineage_show(self, args: dict[str, Any]) -> dict[str, Any]:
        """Родословная этой копии: кто она, от какой версии форк, что приняла."""
        return {"lineage": lineage.genealogy()}

    def cmd_map_show(self, args: dict[str, Any]) -> dict[str, Any]:
        """Карта паутины: копии, гены и связи между ними (из пула)."""
        return state.jsonable(map_mod.build())

    # ─────────────────────────────────────────────────────── обновление
    def cmd_update_status(self, args: dict[str, Any]) -> dict[str, Any]:
        """Состояние обновления: что установлено, что в репозитории, идёт ли."""
        return state.jsonable(update_mod.status())

    def cmd_update_check(self, args: dict[str, Any]) -> dict[str, Any]:
        """Проверить, есть ли новая версия (с походом на сервер)."""
        return state.jsonable(update_mod.check())

    def cmd_update_start(self, args: dict[str, Any]) -> dict[str, Any]:
        """Обновить программу: исходники → сборка → раскатка (в фоне)."""
        return state.jsonable(update_mod.start())

    def cmd_reports_list(self, args: dict[str, Any]) -> dict[str, Any]:
        """Отчёты сторожей и инструментов: что сделали и где ошиблись."""
        items = self._reports(
            limit=int(args.get("limit") or 50),
            level=str(args.get("level") or ""),
            since_ms=int(args.get("since") or 0))
        path = str(getattr(self.core, "REPORTS", ""))
        return {"reports": items, "path": path}

    def _reports(self, limit: int = 50, level: str = "",
                 since_ms: int = 0) -> list[dict[str, Any]]:
        """Прочитать отчёты (ядро на машине может быть старее программы)."""
        reader = getattr(self.core, "reports", None)
        if reader is None:
            return []
        try:
            return list(reader(limit=limit, level=level, since_ms=since_ms) or [])
        except Exception:  # noqa: BLE001
            return []

    def _unread_reports(self, limit: int = 25) -> str:
        """Свежие отчёты — подклас в чат (заказ владельца 24.11).

        Нейросеть «Управляющий» сама узнаёт, что сторож и инструменты
        сделали, пока её не спрашивали.
        """
        seen = 0
        try:
            if REPORTS_SEEN.is_file():
                seen = int(json.loads(REPORTS_SEEN.read_text(encoding="utf-8"))
                           .get("ts") or 0)
        except (OSError, ValueError, AttributeError):
            seen = 0
        items = self._reports(limit=limit, since_ms=seen)
        if not items:
            return ""
        newest = max(int(i.get("ts") or 0) for i in items)
        try:
            REPORTS_SEEN.parent.mkdir(parents=True, exist_ok=True)
            REPORTS_SEEN.write_text(json.dumps({"ts": newest}), encoding="utf-8")
        except OSError:
            pass
        lines = []
        for item in reversed(items):       # от старых к новым — как в журнале
            # Время — по Москве (заказ владельца 24.11): одна зона на всю
            # программу, а не зона машины.
            stamp = self.core.fmt_ms(int(item.get("ts") or 0))
            kind = str(item.get("kind") or "log")
            level = str(item.get("level") or "info")
            lines.append("[%s] %s/%s: %s" % (stamp, kind, level,
                                             str(item.get("text") or "")))
        return ("Отчёты программы с прошлого раза (сторож и инструменты):\n"
                + "\n".join(lines))

    # ─────────────────────────────────────────────────────────── настройки
    def cmd_settings_get(self, args: dict[str, Any]) -> dict[str, Any]:
        return state.read_settings()

    def cmd_settings_set(self, args: dict[str, Any]) -> dict[str, Any]:
        return state.write_settings(args or {})

    def cmd_daemons_status(self, args: dict[str, Any]) -> dict[str, Any]:
        return state.jsonable(state.daemons_status(self.mods))

    # ──────────────────────────────────────────────────────────────── чат
    def cmd_chat_send(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "").strip()
        if not text:
            return {"status": "error", "error": "пустой запрос"}
        settings = state.read_settings()
        area_slug = str(args.get("area") or settings.get("modelArea") or "minigen")
        model = str(args.get("model") or settings.get("model") or "")
        # Прокси-переменные живут в реестре, а процесс мог устареть ⇒ перечитываем
        # их перед запросом (иначе модель «недоступна» при живом интернете).
        self.core.refresh_env()
        # Свежие отчёты программы — в контекст хода (заказ владельца 24.11):
        # нейросеть знает, что сторож и инструменты сделали, пока её не
        # спрашивали.
        history = list(self.chat_history)
        feed = self._unread_reports()
        if feed:
            history.append({"role": "user", "content": feed})
        try:
            result = self.agent.ask(history, text, area_slug, model)
        except ModelUnavailable as exc:
            return {"status": "unavailable", "error": str(exc),
                    "text": "", "calls": []}
        if result.get("status") == "done":
            self.chat_history = self._trim_history(result.get("history") or [])
        return state.jsonable(result)

    def cmd_chat_confirm(self, args: dict[str, Any]) -> dict[str, Any]:
        approved = bool(args.get("approved"))
        settings = state.read_settings()
        area_slug = str(settings.get("modelArea") or "minigen")
        model = str(settings.get("model") or "")
        self.core.refresh_env()
        result = self.agent.confirm(approved, self.chat_history, area_slug, model)
        if result.get("status") == "done":
            self.chat_history = self._trim_history(result.get("history") or [])
        return state.jsonable(result)

    def cmd_chat_reset(self, args: dict[str, Any]) -> dict[str, Any]:
        self.chat_history = []
        self.agent.pending = None
        return {"ok": True}

    @staticmethod
    def _trim_history(history: list[dict[str, Any]],
                      limit: int = 40) -> list[dict[str, Any]]:
        """История без системного промпта и без хвоста: не пухнет."""
        out = [m for m in history if m.get("role") != "system"]
        return out[-limit:]

    # ──────────────────────────────────────────────────── самообновление
    def cmd_code_list(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"files": code.list_files(str(args.get("path") or ""))}

    def cmd_code_read(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.read_file(str(args.get("path") or ""),
                              int(args.get("start_line") or 0),
                              int(args.get("end_line") or 0))

    def cmd_code_search(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"hits": code.search(str(args.get("query") or ""),
                                    str(args.get("path") or ""))}

    def cmd_code_branch(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.create_worktree(str(args.get("title") or ""))

    def cmd_code_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.apply_patch(str(args.get("path") or ""),
                                str(args.get("find") or ""),
                                str(args.get("replace") or ""))

    def cmd_code_tests(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.run_tests(str(args.get("target") or "all"))

    def cmd_code_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.diff()

    def cmd_code_commit(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.commit(str(args.get("message") or ""))

    def cmd_code_pr(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.open_pr(str(args.get("title") or ""), str(args.get("body") or ""))

    def cmd_code_discard(self, args: dict[str, Any]) -> dict[str, Any]:
        return code.discard_worktree()

    # ──────────────────────────────────── агенты, заявки и чужие репозитории
    def cmd_agents_list(self, args: dict[str, Any]) -> dict[str, Any]:
        """Агенты реестра: манифесты, репозитории, области, их заявки."""
        snapshot = agents_mod.agent_state()
        snapshot["digest"] = agents_mod.agents_digest()
        for agent in snapshot.get("agents") or []:
            slug = str(agent.get("slug") or "")
            agent["requests"] = agents_mod.list_requests(slug)
        return snapshot

    def cmd_agents_state(self, args: dict[str, Any]) -> dict[str, Any]:
        """Состояние реестра агентов (что заведено, где лежит, что есть)."""
        return agents_mod.agent_state()

    def cmd_agents_create(self, args: dict[str, Any]) -> dict[str, Any]:
        """Создать агента: репозиторий, конституция, скиллы, область.

        ⚠️ Меняет внешний мир (создаёт репозиторий на GitHub, пишет в
        реестр, заводит область) — поэтому инструмент помечен опасным и
        спрашивает подтверждения.
        """
        slug = str(args.get("slug") or "").strip().lower()
        title = str(args.get("title") or "").strip()
        role = str(args.get("role") or "").strip()
        repo = str(args.get("repo") or "").strip()
        template = str(args.get("template") or "").strip()
        area_slug = str(args.get("area") or slug).strip().lower()
        make_area = not bool(args.get("no_area"))
        model = str(args.get("model") or "").strip()

        created = agents_mod.create_agent(slug, title, role, repo_slug=repo,
                                          template=template, area=area_slug,
                                          model=model)
        if created.get("error"):
            return created
        result = dict(created)
        if make_area:
            area_dir = state.AREAS_ROOT / ("p-" + area_slug)
            if area_dir.exists():
                result["area_note"] = "область %s уже была" % area_slug
            else:
                area = self.cmd_create({
                    "slug": area_slug,
                    "title": title or area_slug,
                    "folder": str(created.get("repo_path") or ""),
                    "karman": slug,
                })
                if area.get("error"):
                    result["area_note"] = "область не заведена: %s" % area.get("error")
                else:
                    result["area_dir"] = area.get("dir")
            installed = agents_mod.install_skills(area_slug, Path(created["repo_path"]))
            result["skills_installed"] = installed.get("installed") or []
            if installed.get("error"):
                result["area_note"] = ((result.get("area_note") or "") + "; " +
                                       installed["error"]).strip("; ")
        return result

    def cmd_agents_apply(self, args: dict[str, Any]) -> dict[str, Any]:
        """Восстановить агента на этой машине (из реестра на GitHub)."""
        slug = str(args.get("slug") or "").strip().lower()
        if not slug:
            return {"error": "нужен slug агента"}
        area = str(args.get("area") or "").strip()
        applied = agents_mod.apply_agent(slug, area)
        if applied.get("error"):
            return applied
        result = dict(applied)
        area_slug = str(result.get("area") or "")
        area_dir = state.AREAS_ROOT / ("p-" + area_slug)
        if area_slug and not area_dir.exists():
            created = self.cmd_create({
                "slug": area_slug,
                "title": slug,
                "folder": str(result.get("repo_path") or ""),
                "karman": slug,
            })
            if created.get("error"):
                result["area_note"] = "область не заведена: %s" % created.get("error")
            else:
                result["area_dir"] = created.get("dir")
                again = agents_mod.install_skills(area_slug, Path(result["repo_path"]))
                result["skills_installed"] = again.get("installed") or []
        return result

    def cmd_agents_registry_restore(self, args: dict[str, Any]) -> dict[str, Any]:
        """Вернуть локальный клон реестра к состоянию GitHub.

        ⚠️ Локальные правки клона при этом теряются — поэтому действие
        опасное (спрашивает подтверждения). Источник правды — реестр на
        GitHub: локальная копия, разошедшаяся с ним, показывает не то
        (живой случай 24.11: манифест агента «Идеи» обрезался — окно
        показало агента без названия и области).
        """
        restored = agents_mod.restore_registry()
        if restored.get("error"):
            return restored
        state = agents_mod.agent_state()
        restored["agents"] = state.get("agents") or []
        return restored

    # ──────────────────────────────────────────────────────────── заявки
    def _known_projects(self) -> list[dict[str, Any]]:
        """Проекты, которые зачитка может относиться к.

        Три источника: области «МиниГена», локальные репозитории и разделы
        КАРМАНА. Порядок не важен — важно, что список полный: по нему
        решается, «подходит существующий проект или нужен новый».
        """
        projects: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(slug: str, title: str, keywords: Any = ()) -> None:
            name = (slug or "").strip()
            if not name or name.lower() in seen:
                return
            seen.add(name.lower())
            words = [str(word) for word in (keywords or []) if str(word).strip()]
            projects.append({"slug": name, "title": (title or name).strip(),
                             "keywords": words})

        try:
            for item in state.read_projects().get("projects") or []:
                add(str(item.get("slug") or ""), str(item.get("title") or ""),
                    [str(item.get("karman") or ""), str(item.get("folder") or "")])
        except Exception:  # noqa: BLE001
            pass
        try:
            for repo in repos_mod.list_repos(detail=False):
                add(repo["name"], repo["name"])
        except Exception:  # noqa: BLE001
            pass
        try:
            code, sections = karman_mod.list_sections()
            if code == 200:
                for name in sections:
                    add(name, name)
        except Exception:  # noqa: BLE001
            pass
        return projects

    def cmd_ingest_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        """Черновик заявки по зачитке: проект, разделы, чего не хватает.

        Саму зачитку превращает в текст нейросеть — мост даёт ей каркас
        (разделы канона) и подсказку, в каком проекте это, вероятно, живёт.
        """
        text = str(args.get("text") or "").strip()
        if not text:
            return {"error": "пустая зачитка"}
        projects = self._known_projects()
        guess = ingest.guess_project(text, projects)
        title = str(args.get("title") or "").strip() or _title_from(text)
        wanted = str(args.get("project") or "").strip()
        if wanted:
            known = next((p for p in projects
                          if p["slug"].lower() == wanted.lower()), None)
            guess = {"slug": wanted, "title": known["title"] if known else wanted,
                     "confidence": 1.0, "known": known is not None,
                     "evidence": ["задан владельцем"]}
        skeleton = ingest.render_request({
            "title": title,
            "project": guess["slug"],
            "summary": "<одна фраза: что должно измениться>",
            "Что сделать": "<пронумерованные пункты: что именно сделать — проверяемыми действиями>",
            "Контекст": "<что уже есть в проекте (файлы, модули); чего не хватает>",
            "План": "<3–7 шагов, у каждого — проверяемый результат>",
            "Критерии приёмки": "<- [ ] проверяемый пункт (тест, команда, сценарий)>",
            "Риски и открытые вопросы": "<что может помешать; что не сказано; чьё решение нужно>",
            "Источник": "<зачитка владельца, дата>",
        })
        return {
            "ok": True,
            "title": title,
            "project": guess,
            "projects": [{"slug": p["slug"], "title": p["title"]} for p in projects],
            "sections": list(ingest.SECTIONS),
            "skeleton": skeleton,
            "missing": ingest.missing_sections({
                "Что сделать": "<пункт>", "План": "<шаг>",
                "Критерии приёмки": "<пункт>"}),
            "hint": ("Заполни разделы и сохрани через ingest.save. Проект: %s%s. "
                     "Если ни один проект не подошёл — придумай новый (латиница, "
                     "коротко) и напиши его в поле project."
                     % (guess["slug"] or "<новый>",
                        " (уверенность %.2f)" % guess["confidence"]
                        if guess["confidence"] else "")),
        }

    def cmd_ingest_save(self, args: dict[str, Any]) -> dict[str, Any]:
        """Сохранить заявку: в репозиторий агента и в раздел КАРМАНА."""
        agent = str(args.get("agent") or "ideas").strip() or "ideas"
        body = str(args.get("body") or "")
        title = str(args.get("title") or "").strip()
        project = str(args.get("project") or "").strip()
        summary = str(args.get("summary") or "").strip()

        parsed = ingest.parse_request(body) if body else {}
        draft: dict[str, Any] = {
            "title": title or str(parsed.get("title") or ""),
            "project": project or str(parsed.get("проект") or ""),
            "summary": summary or str(parsed.get("суть") or ""),
        }
        sections = parsed.get("sections") or {}
        given = args.get("sections")
        if isinstance(given, dict):
            sections = {**sections, **{str(k): str(v) for k, v in given.items()}}
        for name in ingest.SECTIONS:
            value = str(given.get(name) if isinstance(given, dict) and name in given
                        else sections.get(name) or "").strip()
            if value:
                draft[name] = value
        if not draft.get("Что сделать"):
            return {"error": "заявка без раздела «Что сделать» — нечего сохранять"}
        missing = ingest.missing_sections(draft)
        text = ingest.render_request(draft)
        folder = (draft.get("project") or "без-проекта").strip()
        filename = ingest.request_filename(draft, time.strftime("%Y-%m-%d"))
        saved = agents_mod.save_request(agent, folder, filename, text)
        if saved.get("error"):
            return saved
        published = self._publish_to_karman(draft.get("project") or folder,
                                            draft.get("title") or "", text)
        return {
            "ok": True,
            "agent": agent,
            "project": draft.get("project") or "",
            "path": saved.get("path"),
            "repo": saved.get("repo"),
            "committed": saved.get("committed"),
            "missing": missing,
            "karman": published,
            "text": text,
        }

    def _publish_to_karman(self, project: str, title: str, text: str) -> dict[str, Any]:
        """Положить заявку в раздел КАРМАНА (раздел заводится, если его нет)."""
        name = (project or "").strip()
        if not name:
            return {"published": False, "reason": "проект не указан — раздел неизвестен"}
        status = karman_mod.status()
        if not status.get("has_secret"):
            return {"published": False,
                    "reason": status.get("reason") or "канал КАРМАНА не настроен"}
        existing = _published_prompt_id(name, title, text)
        if existing:
            return {"published": True, "http": 200, "project": name,
                    "id": existing, "duplicate": True}
        code, payload = karman_mod.ensure_project(name)
        if code != 200:
            return {"published": False, "http": code, "project": name,
                    "reason": "раздел не заведён: %s" % karman_mod.explain(code)}
        code, payload = karman_mod.publish(name, title, text)
        if code not in (200, 201):
            return {"published": False, "http": code, "project": name,
                    "reason": "промт не записан: %s" % karman_mod.explain(code)}
        return {"published": True, "http": code, "project": name,
                "id": payload.get("id"), "added": payload.get("added")}

    # ────────────────────────────────────────────── чужие репозитории (чтение)
    def cmd_repos_list(self, args: dict[str, Any]) -> dict[str, Any]:
        """Репозитории рабочего пространства — читать можно, менять нельзя."""
        return {"repos": repos_mod.list_repos()}

    def cmd_repos_read(self, args: dict[str, Any]) -> dict[str, Any]:
        """Прочитать файл чужого репозитория (секреты закрыты)."""
        return repos_mod.read_file(str(args.get("repo") or ""),
                                   str(args.get("path") or ""),
                                   int(args.get("start_line") or 0),
                                   int(args.get("end_line") or 0))

    def cmd_repos_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        """«Почерк» проекта: README, конституция, языки — чтобы попасть в стиль."""
        return repos_mod.profile(str(args.get("repo") or ""))

    # ──────────────────────────────────────────────────────────── демоны
    def cmd_program_version(self, args: dict[str, Any]) -> dict[str, Any]:
        return state.jsonable(state.program_version())

    # ─────────────────────────────────────────────────── модель нейросети
    def cmd_model_catalog(self, args: dict[str, Any]) -> dict[str, Any]:
        """Каталог моделей области — с **родными** настройками платформы.

        Оператор выбирает в интерфейсе ровно то, что платформа объявляет:
        окна контекста (`contextWindowOptions`), уровни эффорта
        (`thinking.effortOptions`), варианты мышления. Ничего не выдумано —
        поэтому список будет верным и для другой платформы, когда появится.
        """
        settings = state.read_settings()
        area = str(args.get("area") or settings.get("modelArea") or "minigen")
        try:
            cat = provider_mod.catalog(area)
        except provider_mod.ProviderUnavailable as exc:
            return {"error": str(exc)}
        try:
            eff = provider_mod.effective(area, settings)
        except provider_mod.ProviderUnavailable:
            eff = {}
        out = state.jsonable(cat)
        out["effective"] = state.jsonable(eff)
        out["area"] = area
        return out

    def cmd_model_apply(self, args: dict[str, Any]) -> dict[str, Any]:
        """Разложить выбранные модель/контекст/эффорт по рабочим областям.

        Пишем **нативными** ключами (`defaultModel`,
        `defaultModelContextWindow`, `defaultModelThinking.effort`) — так, как
        это объявляет сама платформа, чтобы рантайм принял без возражений.
        """
        settings = state.read_settings()
        area = str(args.get("area") or settings.get("modelArea") or "minigen")
        try:
            eff = provider_mod.effective(area, {**settings, **{
                k: v for k, v in (args or {}).items()
                if k in ("model", "modelContext", "modelEffort")}})
        except provider_mod.ProviderUnavailable as exc:
            return {"error": str(exc)}
        slugs = [str(s) for s in (args.get("areas") or []) if str(s).strip()]
        if not slugs:
            slugs = [a["slug"] for a in self.mods["core"].areas()]
        res = provider_mod.apply_to_areas(
            slugs, eff["model"], int(eff.get("context") or 0),
            str(eff.get("effort") or ""), str(eff.get("variant") or ""))
        res["effective"] = state.jsonable(eff)
        self.core.log("Панель «МиниГен»: настройки модели %s (контекст %s, эффорт %s) "
                      "применены к областям: %d" % (eff["model"], eff.get("context"),
                                                   eff.get("effort"),
                                                   res.get("changed") or 0))
        return state.jsonable(res)

    def cmd_daemons_status(self, args: dict[str, Any]) -> dict[str, Any]:
        return state.jsonable(daemons.status())

    def cmd_daemons_start(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name:
            spec = daemons.by_name(name)
            if not spec:
                return {"error": "нет такого демона: %s" % name}
            return state.jsonable(daemons.start(spec))
        return state.jsonable(daemons.start_all())

    def cmd_daemons_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name:
            spec = daemons.by_name(name)
            if not spec:
                return {"error": "нет такого демона: %s" % name}
            return state.jsonable(daemons.stop(spec))
        return state.jsonable(daemons.stop_all())

    def cmd_daemons_restart(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name:
            spec = daemons.by_name(name)
            if not spec:
                return {"error": "нет такого демона: %s" % name}
            return state.jsonable(daemons.restart(spec))
        return state.jsonable(daemons.restart_all())

    # ────────────────────────────────────────────────────────────── прочее
    def cmd_open_folder(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "")
        if not path or not Path(path).exists():
            return {"error": "нет такой папки: %s" % path}
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["explorer", path])
        except OSError as exc:
            return {"error": str(exc)}
        return {"ok": True, "path": path}


def main() -> int:
    """Слушать stdin, отвечать в stdout.

    Режим `--daemon`: окна нет и запросов нет — программа только держит
    демонов (автозапуск при входе в Windows). Запускать через `pythonw.exe`,
    чтобы не было окна консоли.
    """
    if "--daemon" in sys.argv:
        return daemon_mode()
    bridge = Bridge()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            out.write(json.dumps({"ok": False, "error": "запрос не JSON"},
                                 ensure_ascii=False) + "\n")
            out.flush()
            continue
        req_id = req.get("id")
        cmd = str(req.get("cmd") or "")
        args = req.get("args") or {}
        data = bridge.dispatch(cmd, args)
        out.write(json.dumps(reply(req_id, data),
                             ensure_ascii=False, default=str) + "\n")
        out.flush()
    return 0


def daemon_mode() -> int:
    """Демон-режим: поднять API и следить за демонами, пока не остановят.

    ⚠️ Две роли — **разные** (живой случай 24.11, 08:00):

    - **надзиратель демонов** — ровно один, его держит блокировка
      `supervisor.lock` (кто первый взял — тот и следит);
    - **API** (`:8097`) — порт эксклюзивный, его держит один процесс.

    Раньше они были связаны: фоновый экземпляр, **не сумев взять блокировку**
    (её держало открытое окно владельца), выходил — и вместе с ним умирал
    API. Итог: демоны под присмотром окна, но API мёртв, и сетевой сторож не
    может перезапустить контур. Теперь блокировка не нужна, чтобы поднять
    API: не получил надзор — значит, надзирает тот, кто её взял; а мы всё
    равно держим API.
    """
    # 1) API уже кто-то служит? тогда нам делать нечего.
    try:
        import minigen_api
        if minigen_api.another_instance_running():
            print("МиниГен: фоновый режим уже работает — выхожу")
            return 0
    except Exception:  # noqa: BLE001
        pass          # никто не отвечает — значит, мы первые

    # 2) Надзиратель — по блокировке. Её может держать окно владельца: тогда
    #    надзирает оно, а мы поднимаем API (это отдельная роль).
    lock = minigen_lock.get_lock()
    have_lock = lock.acquire()
    if not have_lock:
        print("МиниГен: надзиратель демонов уже есть — API поднимаю, надзор не веду")

    # 3) API: порт эксклюзивен. Не поднялся ⇒ порт занят ⇒ выходим.
    try:
        import minigen_api
        minigen_api.serve()
    except Exception as exc:  # noqa: BLE001
        # Порт занят (или иная причина) — API не наш, а демонами без API
        # надзирать бессмысленно. Раньше ошибка проглатывалась, и лишний
        # менеджер оставался жить (живой случай 23.11).
        if have_lock:
            lock.release()
        print("МиниГен: локальный API не поднялся (%s) — выхожу" % exc)
        return 0

    bridge = Bridge()
    try:
        bridge.core.log("ДЕМОНЫ: программа «МиниГен» в фоновом режиме — "
                        "демоны подняты и следятся (API на %s)"
                        % minigen_api.base_url())
    except Exception:  # noqa: BLE001
        pass
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
