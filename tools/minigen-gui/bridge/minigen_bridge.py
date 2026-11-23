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
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import minigen_state as state          # noqa: E402
import minigen_tools as tools          # noqa: E402
import minigen_code as code            # noqa: E402
import minigen_daemons as daemons      # noqa: E402
import minigen_lock as minigen_lock    # noqa: E402
import minigen_provider as provider_mod  # noqa: E402
from minigen_agent import Agent, ModelUnavailable  # noqa: E402

POINTS_TTL_MS = 60_000


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
        }

    def dispatch(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        handler = self.handlers.get(cmd)
        if handler is None:
            return {"error": "неизвестная команда: %s" % cmd}
        try:
            return handler(args or {})
        except Exception as exc:  # noqa: BLE001
            return {"error": "%s: %s" % (type(exc).__name__, exc)}

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
        return state.snapshot(self.mods,
                              include_sessions=bool(args.get("sessions", True)))

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
        """Новая область: каталог данных + авторизация + запись в конфиг."""
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
        if area_dir.exists():
            return {"error": "каталог %s уже есть" % area_dir}
        template = str(cfg.get("authTemplate") or "")
        copied = ""
        if template and Path(template).is_dir():
            import shutil
            shutil.copytree(template, area_dir)
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
                "shortcut": shortcut_note, "folder_note": folder_note}

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
        try:
            result = self.agent.ask(self.chat_history, text, area_slug, model)
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
        out.write(json.dumps({"id": req_id, "ok": True, "data": data},
                             ensure_ascii=False, default=str) + "\n")
        out.flush()
    return 0


def daemon_mode() -> int:
    """Демон-режим: поднять демонов и следить, пока не остановят."""
    # ⚠️ Один фоновый режим — один. Два процесса-менеджера демонов это ровно
    # то, от чего предостерегает архивная памятка: две копии лечащего брали
    # аккаунты у КАРМАНА дважды. Проверяем не файлом-флагом (он может
    # остаться от упавшего процесса), а живым ответом API: если на :8097
    # кто-то отвечает — другой экземпляр уже работает, и этот лишний.
    try:
        import minigen_api
        if minigen_api.another_instance_running():
            print("МиниГен: фоновый режим уже работает — выхожу")
            return 0
    except Exception:  # noqa: BLE001
        pass          # никто не отвечает — значит, мы первые

    # Системная блокировка — против гонки: два экземпляра, стартовавшие
    # одновременно, оба увидели бы «никого не отвечает» и оба стали бы
    # надзирателем. Блокировку получает один, второй выходит.
    lock = minigen_lock.get_lock()
    if not lock.acquire():
        print("МиниГен: надзиратель демонов уже есть — выхожу")
        return 0

    try:
        import minigen_api
        minigen_api.serve()
    except Exception as exc:  # noqa: BLE001
        # Порт занят (или иная причина) — надзирателем быть нельзя: процесс
        # без API всё равно следил бы за демонами, а это ровно тот дубль,
        # ради которого всё затевалось. Раньше ошибка проглатывалась, и лишний
        # менеджер оставался жить (живой случай 23.11).
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
