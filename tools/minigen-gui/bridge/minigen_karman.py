"""Канал промтов КАРМАНА для «МиниГена»: создать раздел и положить промт.

Заказ владельца (24.11): «все мои зачитки промптов добавлять в хранилище
промтов на кармане и рассортировывать их по проектам, уметь создавать новые
проекты, если такого проекта не существует».

**Контракт Кармана** (прочитан в его исходниках, `karman/docs/prompts.md`):

- `POST /api/prompts` — записать промт: `{project, title, body}` → `201
  {id, project, title, created_at}`. Это **машинный канал Мозга**: секрет один
  (`PROMPTS_INGEST_SECRET`), и пишет он от имени живого аккаунта владельца;
- `POST /api/prompts/projects` — `{projects: [...]}`: завести разделы
  (только добавление — чужие разделы не трогаются);
- `GET /api/prompts/projects` — текущий список разделов;
- `POST /api/prompts/section` — **архивирование** своего промта (это отдельный
  канал, токен раздела; им запись не делается — см. `karman_prompts.py`).

**Где лежит секрет.** В репозитории секретов быть не может (D-038), поэтому:

- адрес сервиса — файл `%USERPROFILE%\\.biblio-keys\\vault-url`, затем
  переменная `KARMAN_URL`;
- секрет канала — файл `%USERPROFILE%\\.biblio-keys\\prompts-ingest-secret`,
  затем переменная `PROMPTS_INGEST_SECRET`.

**Fail-closed.** Если секрета нет — заявка **не теряется**: она лежит в
репозитории агента (GitHub), а мост честно говорит «в КАРМАН не опубликовано:
секрет канала не выдан». Молчаливая потеря заявки — худший вариант.

**Прокси.** Запросы идут напрямую, без `HTTP(S)_PROXY` из окружения: мёртвый
прокси (выключенный Happ) роняет запрос (урок G690, 23.11). Вернуть прокси
можно `KARMAN_USE_ENV_PROXY=1`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TIMEOUT = 20

URL_FILE = "vault-url"
SECRET_FILE = "prompts-ingest-secret"
URL_ENV = "KARMAN_URL"
SECRET_ENV = "PROMPTS_INGEST_SECRET"

#: Коды ответа — человеческим языком.
REASONS = {
    400: "сервис не принял тело запроса",
    401: "секрет канала не принят сервисом",
    403: "секрет не от того канала",
    404: "нет такого адреса у сервиса",
    429: "сервис просит подождать (частые запросы)",
    503: "канал не настроен: секрет не задан на стороне сервиса",
}


def keys_dir() -> Path:
    """Каталог с ключами: по умолчанию ``%USERPROFILE%\\.biblio-keys``."""
    raw = (os.environ.get("BIBLIO_KEYS_DIR") or "").strip()
    return Path(raw) if raw else Path.home() / ".biblio-keys"


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_base() -> tuple[str, str]:
    """(адрес сервиса, источник) — источник: ``file`` | ``env`` | ``''``."""
    value = _read_file(keys_dir() / URL_FILE)
    if value:
        return value, "file"
    value = (os.environ.get(URL_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


def read_secret() -> tuple[str, str]:
    """(секрет канала, источник) — источник: ``file`` | ``env`` | ``''``."""
    value = _read_file(keys_dir() / SECRET_FILE)
    if value:
        return value, "file"
    value = (os.environ.get(SECRET_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


def _opener():
    """Открывалка без прокси из окружения (урок G690)."""
    if (os.environ.get("KARMAN_USE_ENV_PROXY") or "").strip() in ("1", "true", "yes"):
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Один вызов сервиса. Возвращает (код, payload).

    Код ``0`` — вызова не было вовсе (нет адреса/секрета или сеть недоступна);
    в payload тогда лежит ``error`` с причиной.
    """
    secret, secret_src = read_secret()
    base, base_src = read_base()
    if not base:
        return 0, {"error": "адрес сервиса не задан (файл vault-url или KARMAN_URL)",
                   "secret_source": secret_src, "url_source": base_src}
    if not secret:
        return 0, {"error": "секрет канала не задан (файл %s или %s)"
                          % (SECRET_FILE, SECRET_ENV),
                   "secret_source": "", "url_source": base_src}
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + secret)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _opener().open(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            raw = ""
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return 0, {"error": "сеть недоступна: %s" % (reason,),
                   "secret_source": secret_src, "url_source": base_src}
    payload: dict = {}
    if raw:
        try:
            loaded = json.loads(raw)
            payload = loaded if isinstance(loaded, dict) else {"items": loaded}
        except ValueError:
            payload = {"raw": raw[:400]}
    payload.setdefault("secret_source", secret_src)
    payload.setdefault("url_source", base_src)
    return code, payload


def explain(code: int) -> str:
    """Человеческое объяснение кода ответа."""
    if code == 0:
        return "запрос не ушёл"
    return REASONS.get(code, "HTTP %s" % code)


def list_sections() -> tuple[int, list[str]]:
    """Разделы библиотеки промтов (текущий список)."""
    code, payload = call("GET", "/api/prompts/projects")
    if code != 200:
        return code, []
    sections = payload.get("sections") or []
    names: list[str] = []
    for item in sections:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("project") or "")
            if name:
                names.append(name)
    return code, names


def ensure_project(name: str) -> tuple[int, dict]:
    """Заводи раздел (если его нет). Только добавление — чужие не трогаем.

    Возвращает (код, payload), где payload — ``{"added": [...], "sections":
    [...]}`` либо ``{"error": ...}``.
    """
    project = (name or "").strip()
    if not project:
        return 400, {"error": "пустое имя раздела"}
    if len(project) > 64:
        return 400, {"error": "имя раздела длиннее 64 символов"}
    return call("POST", "/api/prompts/projects", {"projects": [project]})


def publish(project: str, title: str, body: str) -> tuple[int, dict]:
    """Положить промт в раздел (машинный канал Мозга)."""
    project = (project or "").strip()
    if not project:
        return 400, {"error": "без раздела промт некуда положить"}
    if not (body or "").strip():
        return 400, {"error": "промт пуст"}
    payload = {"project": project, "title": (title or "").strip()[:200],
               "body": body}
    return call("POST", "/api/prompts", payload)


def status() -> dict[str, Any]:
    """Живой статус канала: читается ли он и что в нём есть."""
    secret, secret_src = read_secret()
    base, base_src = read_base()
    out: dict[str, Any] = {
        "has_url": bool(base),
        "has_secret": bool(secret),
        "secret_source": secret_src,
        "url_source": base_src,
        "http": 0,
        "ok": False,
        "sections": [],
        "reason": "",
    }
    if not base:
        out["reason"] = ("адрес сервиса не задан (файл %s или %s)"
                         % (URL_FILE, URL_ENV))
        return out
    if not secret:
        out["reason"] = ("секрет канала не задан (файл %s или %s) — заявки "
                         "сохраняются в репозитории агента, в КАРМАН не уходят"
                         % (SECRET_FILE, SECRET_ENV))
        return out
    code, sections = list_sections()
    out["http"] = code
    out["ok"] = code == 200
    out["sections"] = sections
    if code == 200:
        out["reason"] = "канал живой, разделов: %d" % len(sections)
    elif code == 0:
        out["reason"] = "сеть недоступна или нет секрета"
    else:
        out["reason"] = explain(code)
    return out
