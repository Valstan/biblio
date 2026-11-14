#!/usr/bin/env python3
"""minimax-switch — автосмена аккаунта MiniMax в областях PC79.

Зачем. Ручная смена аккаунта в запущенном MiniMax Code — это logout → login →
браузер → кнопки. А рантайм MiniMax Code берёт токен **из стора области**, и
ключ записи выводится из **абсолютного пути** каталога `auth`:

    com.minimax.mcode.oauth.prod.en\\0<base64url(sha256(<area>/auth + "\\0" + "mcode-public"))>

Значит «сменить аккаунт» = положить в стор области запись с токеном нужного
аккаунта, пересчитав ключ под путь этой области. Путь области не меняется →
сессии, настройки и контекст CLI остаются прежними, меняется только вход.

Рецепт взят у проектов (Сарафан, ADR-0017 + `modules/minimax_engine.py`,
`materialize_leased_profile`): рантайм требует в записи поля `generation` и
`loginEpoch` — без них CLI отвергает запись («stored OAuth credential record is
invalid»).

Правила, которые скрипт соблюдает сам:
  * **одна копия авторизации** — refresh-токен у MiniMax одноразовый, две копии
    одной авторизации делят его и ломают друг друга ([G623]);
  * **бэкап стора** перед любой записью;
  * **никаких секретов в выводе** — только метаданные;
  * **панель — источник истины** по очкам: `signin/status?timezone_id=Europe/Moscow`;
  * **тихий ремонт — только незапущенные области**: у работающей области вход
    трогать нельзя (владелец сказал: «тихо, но только незапущенные»).

Запуск:
    python minimax-switch.py areas
    python minimax-switch.py areas --json
    python minimax-switch.py best
    python minimax-switch.py accounts --json
    python minimax-switch.py move p-gonba p-brain --apply
    python minimax-switch.py install p-brain --pair pair.json --apply
    python minimax-switch.py pull  p-brain --apply          # пара от КАРМАНА (одна отдача!)
    python minimax-switch.py repair --apply                  # тихий ремонт незапущенных
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

AREAS_ROOT = Path(os.environ.get("MINIMAX_AREAS_ROOT", r"C:\Users\valstan\.minimax-areas"))
PROJECTS_JSON = Path(os.environ.get("MINIMAX_PROJECTS_JSON", r"C:\Users\valstan\bin\mcode-project-areas.json"))
SECRETS_ENV = Path(os.environ.get("MINIMAX_SECRETS_ENV", str(Path.home() / ".brain_matrica" / "secrets.env")))

STORE_REL = Path("auth") / "prod" / "en" / "mcode-public" / "auth.json"
META_NAME = "profile.json"
KEY_PREFIX = "com.minimax.mcode.oauth.prod.en"
CLIENT_ID = "mcode-public"
SCOPES = ["agent.default"]
AUDIENCE = "agent-backend"
PANEL_BASE = "https://agent.minimax.io/minimax-cloud/api/v1/signin"
SIGNIN_TZ = "Europe%2FMoscow"
DEFAULT_TTL_MS = 8 * 60 * 60 * 1000

SSL_CTX = ssl.create_default_context()


# ────────────────────────────── хранилище ──────────────────────────────

def store_path(area: Path) -> Path:
    return area / STORE_REL


def auth_home(area: Path) -> Path:
    return (area / "auth").resolve()


def store_key(area: Path) -> str:
    digest = hashlib.sha256(("%s\0%s" % (str(auth_home(area)), CLIENT_ID)).encode("utf-8")).digest()
    suffix = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return "%s\0%s" % (KEY_PREFIX, suffix)


def read_store(area: Path) -> dict:
    p = store_path(area)
    if not p.exists():
        return {"schemaVersion": 1, "records": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schemaVersion": 1, "records": {}}
    if not isinstance(data.get("records"), dict):
        data["records"] = {}
    return data


def write_store(area: Path, store: dict) -> Path:
    p = store_path(area)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S")))
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def read_meta(area: Path) -> dict:
    p = area / META_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(area: Path, email: str = "", karman_id=None) -> None:
    p = area / META_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if email:
        data["email"] = email
    if karman_id is not None:
        data["karman_account_id"] = int(karman_id)
    data["project"] = area_project(area)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ────────────────────────────── панель MiniMax ──────────────────────────────

def panel(token: str, action: str = "status") -> tuple:
    url = "%s/%s?timezone_id=%s" % (PANEL_BASE, action, SIGNIN_TZ)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=SSL_CTX) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}


def today_cell(payload: dict) -> dict:
    days = ((payload.get("data") or {}).get("days")) or []
    for d in days:
        if d.get("is_today"):
            return d
    return {}


def next_points(payload: dict) -> int:
    """Очки ближайшей активации (по панели): сегодняшняя клетка, если не забрана."""
    today = today_cell(payload)
    if today and not claimed(today):
        return int(today.get("points") or 400)
    days = ((payload.get("data") or {}).get("days")) or []
    no = int(today.get("day_no") or 1)
    future = [d for d in days if int(d.get("day_no") or 0) > no and d.get("status") != 3]
    if not future:
        return 0
    return int(min(future, key=lambda d: int(d.get("day_no") or 0)).get("points") or 400)


def claimed(cell: dict) -> bool:
    return int(cell.get("status") or 0) in (2, 3)


# ────────────────────────────── области ──────────────────────────────

def area_titles() -> dict:
    try:
        data = json.loads(PROJECTS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {p["slug"]: p.get("title", p["slug"]) for p in data.get("projects", [])}


def list_areas() -> list:
    out = []
    if not AREAS_ROOT.exists():
        return out
    for area in sorted(AREAS_ROOT.iterdir()):
        if area.is_dir() and store_path(area).exists():
            out.append(area)
    return out


_RUNNING_CACHE = {"ts": 0.0, "set": set()}


def running_areas() -> set:
    """Области, в которых сейчас запущен MiniMax Code (по --user-data-dir)."""
    now = time.time()
    if now - _RUNNING_CACHE["ts"] < 5:
        return _RUNNING_CACHE["set"]
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'minimax-areas' } | "
        "ForEach-Object { if ($_.CommandLine -match 'minimax-areas\\\\([A-Za-z0-9_-]+)') { $matches[1] } } | "
        "Sort-Object -Unique"
    )
    found = set()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=25,
        )
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            if line:
                found.add(line)
    except Exception:  # noqa: BLE001
        pass
    _RUNNING_CACHE["ts"] = now
    _RUNNING_CACHE["set"] = found
    return found


def live_record(area: Path):
    """Живая запись области: та, чей accessToken отвечает панели."""
    store = read_store(area)
    records = store.get("records") or {}
    if not records:
        return None, None
    own = records.get(store_key(area))
    candidates = [own] if own else []
    candidates += [r for k, r in records.items() if k != store_key(area)]
    for rec in candidates:
        if not isinstance(rec, dict):
            continue
        tok = rec.get("accessToken") or ""
        if not tok:
            continue
        status, payload = panel(tok)
        if status == 200 and (payload.get("base_resp") or {}).get("status_code") == 0:
            return rec, payload
    return (own or next(iter(records.values()))), None


def account_state(area: Path, running: set = None) -> dict:
    running = running if running is not None else running_areas()
    rec, payload = live_record(area)
    meta = read_meta(area)
    slug = area.name.replace("p-", "")
    st = {
        "area": area.name,
        "slug": slug,
        "title": area_titles().get(slug, slug),
        "running": area.name in running,
        "has_record": rec is not None,
    }
    if rec:
        st["has_refresh"] = bool(rec.get("refreshToken"))
        st["expires_at"] = rec.get("expiresAtMs")
    st["email"] = meta.get("email") or (rec or {}).get("email") or ""
    st["karman_id"] = meta.get("karman_account_id")
    if payload is None:
        st["state"] = "нет входа" if rec is None else "токен не отвечает"
        st["live"] = False
    else:
        cell = today_cell(payload)
        st["state"] = "жив"
        st["live"] = True
        st["day_no"] = cell.get("day_no")
        st["points_today"] = cell.get("points")
        st["claimed_today"] = claimed(cell)
        st["next_points"] = next_points(payload)
    st["orphan"] = (not st["live"]) and (not st["running"])
    return st


def find_areas_with_token(token: str) -> list:
    hits = []
    if not token:
        return hits
    for area in list_areas():
        for rec in (read_store(area).get("records") or {}).values():
            if isinstance(rec, dict) and rec.get("accessToken") == token:
                hits.append(area.name)
                break
    return hits


# ────────────────────────────── Карман ──────────────────────────────

def secrets() -> dict:
    out = {}
    try:
        for line in SECRETS_ENV.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except (OSError, ValueError):
        pass
    return out


def karman_base() -> str:
    return (os.environ.get("KARMAN_VAULT_URL") or os.environ.get("VAULT_URL")
            or secrets().get("PROMPTS_URL") or "").rstrip("/")


def karman_token() -> str:
    return os.environ.get("KARMAN_ROOM_TOKEN") or secrets().get("SECRETS_TOKEN") or ""


def karman_available(tier: str = "", project: str = "", area: str = "") -> tuple:
    """GET /api/minimax/accounts/available — отдаёт аккаунт вместе с парой (разово).

    Проект передаём ЗАГОЛОВКОМ (X-MiniMax-Project / X-MiniMax-Area): заголовок не
    проходит через валидацию тела, а значит не может обернуться 400 — а 400 на
    выдаче пары стоит нам одноразового refresh и нового входа в браузере.
    """
    base, token = karman_base(), karman_token()
    if not base or not token:
        return 0, {"error": "нет адреса КАРМАНА или room-токена"}
    url = "%s/api/minimax/accounts/available" % base
    if tier:
        url += "?tier=%s" % tier
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if project:
        headers["X-MiniMax-Project"] = project
    if area:
        headers["X-MiniMax-Area"] = area
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}


def karman_report_activation(email: str, account_id, access: str, refresh: str, expires_ms,
                             project: str = "", area: str = "") -> bool:
    """Вернуть обновлённую пару в Карман (иначе цепочка одноразового refresh умрёт)."""
    base, token = karman_base(), karman_token()
    if not base or not token or not email:
        return False
    body = {"email": email, "access_token": access, "refresh_token": refresh}
    if account_id is not None:
        body["account_id"] = account_id
    if expires_ms:
        body["expires_at_ms"] = expires_ms
    data = json.dumps(body).encode("utf-8")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
               "Accept": "application/json"}
    if project:
        headers["X-MiniMax-Project"] = project
    if area:
        headers["X-MiniMax-Area"] = area
    req = urllib.request.Request("%s/api/minimax/activations" % base, data=data, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        return False


def area_project(area: Path) -> str:
    """Имя проекта по области: p-brain → brain."""
    name = area.name
    return name[2:] if name.startswith("p-") else name


# ────────────────────────────── команды ──────────────────────────────

def build_record(access: str, refresh: str, expires_ms, previous: dict, email: str = "") -> dict:
    """Запись токен-стора для рантайма MiniMax Code.

    ВАЖНО: рантайм **требует** непустой `refresh_token` в записи — без него он
    считает запись битой и отказывается работать вовсе («The stored OAuth
    credential record is invalid»). Если Карман настоящий refresh не прислал
    (он говорит, что ему хватает access), кладём **заглушку**: работает access,
    продлить вход ею нельзя — по истечении срока берём следующий аккаунт
    (так же поступает Сарафан, см. «Телефон» от 14.11).
    """
    rec = {
        "schemaVersion": 1,
        "accessToken": access,
        "tokenType": "Bearer",
        "clientId": CLIENT_ID,
        "scopes": list(SCOPES),
        "audience": AUDIENCE,
        "expiresAtMs": int(expires_ms or (time.time() * 1000 + DEFAULT_TTL_MS)),
        "generation": int((previous or {}).get("generation") or 1),
        "loginEpoch": str(uuid.uuid4()),
    }
    if refresh:
        rec["refreshToken"] = refresh
    else:
        # заглушка: рантайму нужно непустое значение, чтобы принять запись
        rec["refreshToken"] = "stub-" + uuid.uuid4().hex + uuid.uuid4().hex
    if email:
        rec["email"] = email
    return rec


def install_pair(area: Path, access: str, refresh: str, expires_ms, email: str = "", karman_id=None) -> bool:
    store = read_store(area)
    previous = (store.get("records") or {}).get(store_key(area)) or {}
    record = build_record(access, refresh, expires_ms, previous, email)
    write_store(area, {"schemaVersion": 1, "records": {store_key(area): record}})
    if email or karman_id is not None:
        write_meta(area, email=email, karman_id=karman_id)
    status, payload = panel(access)
    ok = status == 200 and (payload.get("base_resp") or {}).get("status_code") == 0
    cell = today_cell(payload) if ok else {}
    print("  записано в %s | панель: %s | клетка=%s очки=%s" % (
        area.name, "ок" if ok else "не отвечает (%s)" % status, cell.get("day_no"), cell.get("points")))
    return ok


def cmd_areas(args) -> int:
    running = running_areas()
    states = [account_state(a, running) for a in list_areas()]
    if args.json:
        print(json.dumps(states, ensure_ascii=False, indent=2))
        return 0
    print("Области: %s\n" % AREAS_ROOT)
    print("%-14s %-18s %-9s %-7s %-8s %-9s %s" % ("область", "состояние", "запущена", "клетка", "очки", "забрано", "следующая"))
    for st in states:
        print("%-14s %-18s %-9s %-7s %-8s %-9s %s" % (
            st["area"], st.get("state", "-"), "да" if st.get("running") else "нет",
            st.get("day_no", "-"), st.get("points_today", "-"),
            ("да" if st.get("claimed_today") else ("нет" if st.get("claimed_today") is False else "-")),
            st.get("next_points", "-")))
    return 0


def cmd_accounts(args) -> int:
    running = running_areas()
    live = []
    for area in list_areas():
        st = account_state(area, running)
        if st.get("live"):
            st["source"] = area.name
            live.append(st)
    if args.json:
        print(json.dumps(live, ensure_ascii=False, indent=2))
        return 0
    print("Живые аккаунты (откуда можно взять):\n")
    for st in live:
        print("  %-14s %-20s очки след.=%-6s забран=%-5s" % (
            st["area"], st.get("email") or "(почта неизвестна)", st.get("next_points"), st.get("claimed_today")))
    return 0


def cmd_best(args) -> int:
    rows = [s for s in (account_state(a) for a in list_areas()) if s.get("live")]
    rows.sort(key=lambda s: (-(s.get("next_points") or 0), s.get("claimed_today") is True, s["area"]))
    print("Живые аккаунты по порядку «сколько очков на следующую активацию»:\n")
    for st in rows:
        print("  %-14s очки след.=%-6s сегодня забран=%-5s клетка=%s" % (
            st["area"], st.get("next_points"), st.get("claimed_today"), st.get("day_no")))
    if not rows:
        print("  (живых аккаунтов нет)")
    return 0


def cmd_install(args) -> int:
    area = AREAS_ROOT / args.area
    if not area.exists():
        print("нет области %s" % area, file=sys.stderr)
        return 1
    data = json.loads(Path(args.pair).read_text(encoding="utf-8"))
    access = str(data.get("access_token") or data.get("accessToken") or "")
    refresh = str(data.get("refresh_token") or data.get("refreshToken") or "")
    expires = data.get("expires_at_ms") or data.get("expiresAtMs")
    if not access:
        print("в файле нет access_token", file=sys.stderr)
        return 1
    dupes = find_areas_with_token(access)
    if dupes and not args.apply:
        print("ВНИМАНИЕ: эта авторизация уже стоит в: %s" % ", ".join(dupes))
        print("          две копии делят одноразовый refresh и ломают друг друга ([G623])")
        print("          если это перенос — используй: move <src> <dst>")
        return 2
    print("Установка пары в %s%s" % (area.name, "" if args.apply else " (проба, без записи)"))
    if not args.apply:
        print("  перезапусти с --apply, чтобы записать")
        return 0
    install_pair(area, access, refresh, expires, data.get("email") or "", data.get("karman_account_id"))
    return 0


def cmd_move(args) -> int:
    src = AREAS_ROOT / args.src
    dst = AREAS_ROOT / args.dst
    for a in (src, dst):
        if not a.exists():
            print("нет области %s" % a, file=sys.stderr)
            return 1
    rec, payload = live_record(src)
    if not rec or not rec.get("accessToken"):
        print("в %s нет живого токена" % src.name, file=sys.stderr)
        return 1
    other = find_areas_with_token(rec["accessToken"])
    if any(o != src.name for o in other):
        print("этот же аккаунт уже стоит в %s — сначала убери оттуда (одна копия!)" %
              ", ".join(o for o in other if o != src.name), file=sys.stderr)
        return 2
    print("Перенос: %s → %s" % (src.name, dst.name))
    print("  аккаунт: access-токен из %s, refresh %s" % (src.name, "есть" if rec.get("refreshToken") else "нет"))
    if not args.apply:
        print("  (проба — перезапусти с --apply)")
        return 0
    install_pair(dst, rec["accessToken"], rec.get("refreshToken") or "", rec.get("expiresAtMs"),
                 rec.get("email") or "", read_meta(src).get("karman_account_id"))
    store = read_store(src)
    records = store.get("records") or {}
    store["records"] = {k: v for k, v in records.items()
                        if not (isinstance(v, dict) and v.get("accessToken") == rec["accessToken"])}
    write_store(src, store)
    print("  из %s запись убрана (одна копия авторизации)" % src.name)
    return 0


def cmd_pull(args) -> int:
    """Взять пару у КАРМАНА и поставить её в область.

    ⚠ Пара отдаётся ОДИН раз: Карман больше её не отдаст, пока проект не
    вернёт обновлённую пару. Поэтому после установки обновлённую пару надо
    вернуть (см. --report).
    """
    area = AREAS_ROOT / args.area
    if not area.exists():
        print("нет области %s" % area, file=sys.stderr)
        return 1
    if not args.apply:
        # Проба НЕ ходит в Карман: пара отдаётся один раз, и «посмотреть» —
        # это уже забрать. Проба показывает только местное состояние.
        st = account_state(area)
        print("Область %s: %s | запущена=%s | аккаунт=%s" % (
            area.name, st.get("state"), "да" if st.get("running") else "нет", st.get("email") or "—"))
        print("  Проба ничего не берёт. Реальный запрос к Карману отдаёт пару ОДИН раз —")
        print("  это цена проверки, поэтому её платим только при --apply.")
        return 0
    status, payload = karman_available(args.tier or "", area_project(area), area.name)
    if status != 200:
        print("Карман ответил %s: %s" % (status, payload.get("error") or payload), file=sys.stderr)
        return 1
    account = payload.get("account") or {}
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    expires = payload.get("expires_at_ms")
    print("Карман отдал: id=%s email=%s tier=%s why=%s (проект %s)" % (
        account.get("id"), account.get("email"), payload.get("tier"), payload.get("why"),
        area_project(area)))
    if not access:
        print("  пара не выдана (reason=%s) — готовых пар нет" % payload.get("reason"))
        return 2
    print("  ⚠ пара отдаётся ОДИН раз — её надо вернуть обновлённой")
    if not args.apply:
        print("  (проба — перезапусти с --apply, чтобы поставить в %s)" % area.name)
        return 0
    install_pair(area, access, refresh, expires, account.get("email") or "", account.get("id"))
    if args.report:
        ok = karman_report_activation(account.get("email") or "", account.get("id"), access, refresh,
                                     expires, area_project(area), area.name)
        print("  обновлённая пара возвращена в Карман: %s" % ("да" if ok else "нет"))
    return 0


def cmd_repair(args) -> int:
    """Тихий ремонт: незапущенные области без живого входа получают пару из Кармана."""
    running = running_areas()
    states = [account_state(a, running) for a in list_areas()]
    orphans = [s for s in states if s.get("orphan")]
    if args.json:
        print(json.dumps({"orphans": orphans}, ensure_ascii=False, indent=2))
        return 0
    if not orphans:
        print("Ремонт не нужен: незапущенных областей без входа нет")
        return 0
    print("Осиротевшие и незапущенные области: %s" % ", ".join(s["area"] for s in orphans))
    if not args.apply:
        print("  (проба — перезапусти с --apply)")
        return 0
    repaired = []
    for st in orphans:
        area = AREAS_ROOT / st["area"]
        print("→ %s (проект %s)" % (st["area"], area_project(area)))
        status, payload = karman_available(args.tier or "", area_project(area), area.name)
        if status != 200:
            print("  Карман ответил %s: %s" % (status, payload.get("error") or payload))
            break
        account = payload.get("account") or {}
        access = str(payload.get("access_token") or "")
        if not access:
            print("  готовых пар нет (reason=%s) — нужен вход владельца в браузере" % payload.get("reason"))
            break
        install_pair(area, access, str(payload.get("refresh_token") or ""), payload.get("expires_at_ms"),
                     account.get("email") or "", account.get("id"))
        if args.report:
            ok = karman_report_activation(account.get("email") or "", account.get("id"), access,
                                          str(payload.get("refresh_token") or ""), payload.get("expires_at_ms"),
                                          area_project(area), area.name)
            print("  обновлённая пара возвращена в Карман: %s" % ("да" if ok else "нет"))
        repaired.append(st["area"])
    print("Починено: %s" % (", ".join(repaired) if repaired else "—"))
    return 0


def cmd_verify(args) -> int:
    for name in args.areas:
        area = AREAS_ROOT / name
        if not area.exists():
            print("%s: нет области" % name)
            continue
        st = account_state(area)
        print("%s: %s | клетка=%s очки=%s забран=%s | запущена=%s" % (
            name, st.get("state"), st.get("day_no"), st.get("points_today"),
            st.get("claimed_today"), "да" if st.get("running") else "нет"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Автосмена аккаунта MiniMax в областях PC79")
    ap.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("areas", help="инвентарь областей")
    p.set_defaults(func=cmd_areas)

    p = sub.add_parser("accounts", help="живые аккаунты (откуда можно взять)")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("best", help="рейтинг живых аккаунтов по очкам")
    p.set_defaults(func=cmd_best)

    p = sub.add_parser("install", help="поставить пару токенов в область")
    p.add_argument("area")
    p.add_argument("--pair", required=True, help="JSON с access_token/refresh_token/expires_at_ms")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("move", help="перенести аккаунт из области в область")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("pull", help="взять пару у КАРМАНА и поставить в область")
    p.add_argument("area")
    p.add_argument("--tier", help="например 1000 — очки нужного класса")
    p.add_argument("--report", action="store_true", help="вернуть обновлённую пару в КАРМАН")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("repair", help="тихий ремонт незапущенных областей")
    p.add_argument("--tier", help="класс очков, который просим у Кармана")
    p.add_argument("--report", action="store_true", help="возвращать обновлённую пару в КАРМАН")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_repair)

    p = sub.add_parser("verify", help="проверить область")
    p.add_argument("areas", nargs="+")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
