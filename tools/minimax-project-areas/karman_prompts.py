#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Канал промтов раздела biblio в КАРМАНе (мандат brain D-190).

Заказ: «дом утилит ходит в КАРМАН, берёт СВОИ промты (живые, новые) вместе с
готовыми планами, уточняет план под себя, выполняет и отправляет промт в
архив». **Архив = «сделано»; не выполнил — не архивируй.**

    python tools/minimax-project-areas/karman_prompts.py status
    python tools/minimax-project-areas/karman_prompts.py list
    python tools/minimax-project-areas/karman_prompts.py archived
    python tools/minimax-project-areas/karman_prompts.py show <id>
    python tools/minimax-project-areas/karman_prompts.py archive <id> --done
    python tools/minimax-project-areas/karman_prompts.py unarchive <id>

    ( `--json` — машинный вывод; в отчётах и письмах значения токена быть не должно )

Внешние входы (в репозитории их быть не может — и адрес сервиса, и значение
токена; D-038):

  * токен раздела — файл ``%USERPROFILE%\.biblio-keys\prompts-token``
    (первый источник), затем переменная ``PROMPTS_TOKEN_BIBLIO``;
  * адрес сервиса — файл ``%USERPROFILE%\.biblio-keys\vault-url``,
    затем переменная ``KARMAN_URL``.

Два урока из живых проверок (письмо brain от 23.11, пул G690):

  1. **Проверять файл, а не env.** Отсутствие переменной в окружении сессии —
     не отсутствие токена: значение лежит в файле.
  2. **Прокси из окружения мешает.** Мёртвый ``HTTP(S)_PROXY`` (выключенный
     Happ) роняет запрос — поэтому запросы идут напрямую, без прокси из
     переменных окружения. Вернуть прокси можно ``KARMAN_USE_ENV_PROXY=1``.

Значение токена наружу не выводится: в отчёте — только источник («file»/«env»).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = "biblio"
TOKEN_ENV = "PROMPTS_TOKEN_BIBLIO"
URL_ENV = "KARMAN_URL"
TOKEN_FILE = "prompts-token"
URL_FILE = "vault-url"
TIMEOUT = 20

#: Коды ответа сервиса — человеческим языком (мандат brain, раздел «Ответы»).
REASONS = {
    400: "плохой id промта",
    401: "токен не принят сервисом",
    403: "токен не от того раздела",
    404: "такого промта в разделе нет",
    503: "канал не настроен: раздел не отдаётся "
         "(токен не выдан комнате или выдача не принята)",
}


# ────────────────────────────────────────────────────────── входы (файл, не env)

def keys_dir() -> Path:
    """Каталог с файлами ключей: по умолчанию ``%USERPROFILE%\\.biblio-keys``."""
    raw = (os.environ.get("BIBLIO_KEYS_DIR") or "").strip()
    return Path(raw) if raw else Path.home() / ".biblio-keys"


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_token() -> tuple[str, str]:
    """(значение, источник) — источник: ``file`` | ``env`` | ``''``."""
    value = _read_file(keys_dir() / TOKEN_FILE)
    if value:
        return value, "file"
    value = (os.environ.get(TOKEN_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


def read_base() -> tuple[str, str]:
    """(адрес сервиса, источник) — источник: ``file`` | ``env`` | ``''``."""
    value = _read_file(keys_dir() / URL_FILE)
    if value:
        return value, "file"
    value = (os.environ.get(URL_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


# ────────────────────────────────────────────────────────── HTTP

def _opener():
    """Открывалка без прокси из переменных окружения (урок G690).

    Прокси из env берём только при явном ``KARMAN_USE_ENV_PROXY=1``: мёртвый
    прокси (выключенный Happ) роняет запрос, а сервис виден и напрямую.
    """
    if (os.environ.get("KARMAN_USE_ENV_PROXY") or "").strip() in ("1", "true", "yes"):
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Один вызов сервиса. Возвращает (код, payload).

    Код ``0`` — вызова не было вовсе (нет токена/адреса или сеть недоступна);
    в payload тогда лежит ``error`` с причиной.
    """
    token, token_src = read_token()
    base, base_src = read_base()
    if not token:
        return 0, {"error": "токена раздела нет ни в файле, ни в env",
                   "token_source": "", "url_source": base_src}
    if not base:
        return 0, {"error": "адрес сервиса не задан (файл vault-url или KARMAN_URL)",
                   "token_source": token_src, "url_source": ""}

    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
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
                   "token_source": token_src, "url_source": base_src}

    payload: dict = {}
    if raw:
        try:
            loaded = json.loads(raw)
            payload = loaded if isinstance(loaded, dict) else {"items": loaded}
        except ValueError:
            payload = {"raw": raw[:400]}
    payload.setdefault("token_source", token_src)
    payload.setdefault("url_source", base_src)
    return code, payload


# ────────────────────────────────────────────────────────── раздел

def normalize(prompt: dict) -> dict:
    """Промт в единый вид; план отличаем от промта (план лежит рядом)."""
    title = (prompt.get("title") or "").strip()
    body = (prompt.get("body") or "").strip()
    head = ""
    for line in body.splitlines():
        if line.strip():
            head = line.strip()
            break
    kind = "план" if title.startswith("План:") or head.startswith("План:") else "промт"
    return {
        "id": prompt.get("id"),
        "title": title or head[:120],
        "kind": kind,
        "created_at": prompt.get("createdAt") or prompt.get("created_at") or "",
        "archived": bool(prompt.get("archived")),
        "body": body,
    }


def status() -> dict:
    """Живой статус канала: читается раздел или нет (и почему)."""
    token, token_src = read_token()
    base, base_src = read_base()
    out = {
        "token_source": token_src,
        "url_source": base_src,
        "has_token": bool(token),
        "has_url": bool(base),
    }
    if not token:
        out.update({"http": 0, "ok": False, "prompts": 0,
                    "reason": "токена раздела нет ни в файле, ни в env "
                              "(файл %s\\%s)" % (keys_dir(), TOKEN_FILE)})
        return out
    if not base:
        out.update({"http": 0, "ok": False, "prompts": 0,
                    "reason": "адрес сервиса не задан (файл %s\\%s или %s)"
                              % (keys_dir(), URL_FILE, URL_ENV)})
        return out
    code, payload = call("GET", "/api/prompts/section?project=%s" % PROJECT)
    prompts = payload.get("prompts") or []
    out["http"] = code
    out["ok"] = code == 200
    out["prompts"] = len(prompts)
    if code == 200:
        out["reason"] = "раздел читается"
    elif code == 0:
        out["reason"] = payload.get("error") or "сеть недоступна"
    else:
        out["reason"] = REASONS.get(code, "HTTP %s" % code)
    return out


def list_prompts(archived: bool = False) -> tuple[int, list]:
    """Живые промты раздела (или архивные при ``archived=True``)."""
    query = "&archived=1" if archived else ""
    code, payload = call("GET", "/api/prompts/section?project=%s%s" % (PROJECT, query))
    if code != 200:
        return code, []
    return code, [normalize(p) for p in (payload.get("prompts") or [])]


def get_prompt(prompt_id: int) -> tuple[int, dict]:
    """Один промт раздела — ищем среди живых, иначе среди архивных."""
    code, items = list_prompts(archived=False)
    if code != 200:
        return code, {}
    for item in items:
        if item["id"] == prompt_id:
            return 200, item
    code, items = list_prompts(archived=True)
    if code != 200:
        return code, {}
    for item in items:
        if item["id"] == prompt_id:
            return 200, item
    return 404, {}


def archive(prompt_id: int, done: bool = False) -> tuple[int, dict]:
    """Отправить промт в архив — **только если задача выполнена**.

    Без ``done`` не архивируем: «не выполнил — не архивируй» (мандат brain).
    """
    if not done:
        return 0, {"error": "не выполнил — не архивируй: "
                           "подтверди выполнение флагом --done"}
    return call("POST", "/api/prompts/section",
                {"project": PROJECT, "id": int(prompt_id), "archived": True})


def unarchive(prompt_id: int) -> tuple[int, dict]:
    """Вернуть промт из архива (архив — не удаление)."""
    return call("POST", "/api/prompts/section",
                {"project": PROJECT, "id": int(prompt_id), "archived": False})


# ────────────────────────────────────────────────────────── CLI

def _explain(code: int) -> str:
    """Человеческое объяснение кода ответа — для CLI и отчётов."""
    if code == 0:
        return status()["reason"]
    return REASONS.get(code, "HTTP %s" % code)


def _print_prompt(item: dict) -> None:
    print("#%s | %s | %s | %s"
          % (item.get("id"), item.get("kind"), item.get("created_at") or "—",
             item.get("title") or "—"))
    body = (item.get("body") or "").strip()
    if body:
        for line in body.splitlines()[:6]:
            print("   " + line[:300])


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    command = argv[0] if argv else "status"

    if command == "status":
        res = status()
        if as_json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print("канал промтов раздела %s: %s" % (PROJECT, res["reason"]))
            print("   HTTP %s, промтов: %s" % (res["http"], res["prompts"]))
            print("   токен: %s | адрес: %s"
                  % (res["token_source"] or "нет", res["url_source"] or "нет"))
        return 0 if res["ok"] else 1

    if command in ("list", "archived"):
        code, items = list_prompts(archived=(command == "archived"))
        if as_json:
            print(json.dumps({"http": code, "prompts": items},
                             ensure_ascii=False, indent=2))
            return 0 if code == 200 else 1
        if code != 200:
            print("не получилось: %s" % _explain(code),
                  file=sys.stderr)
            return 1
        if not items:
            print("Пусто.")
            return 0
        for item in items:
            _print_prompt(item)
        return 0

    if command == "show":
        raw_id = argv[1] if len(argv) > 1 else ""
        if not raw_id.isdigit():
            print("Нужен числовой id промта.", file=sys.stderr)
            return 2
        code, item = get_prompt(int(raw_id))
        if as_json:
            print(json.dumps({"http": code, "prompt": item},
                             ensure_ascii=False, indent=2))
            return 0 if code == 200 else 1
        if code != 200:
            print("не получилось: %s" % _explain(code),
                  file=sys.stderr)
            return 1
        _print_prompt(item)
        return 0

    if command in ("archive", "unarchive"):
        raw_id = argv[1] if len(argv) > 1 else ""
        if not raw_id.isdigit():
            print("Нужен числовой id промта.", file=sys.stderr)
            return 2
        done = "--done" in argv
        if command == "archive":
            code, payload = archive(int(raw_id), done=done)
        else:
            code, payload = unarchive(int(raw_id))
        if as_json:
            print(json.dumps({"http": code, "result": payload},
                             ensure_ascii=False, indent=2))
            return 0 if code == 200 else 1
        if code == 0:
            print("не получилось: %s" % payload.get("error"), file=sys.stderr)
            return 1
        if code != 200:
            print("не получилось: %s" % _explain(code),
                  file=sys.stderr)
            return 1
        print("Промт #%s: archived=%s"
              % (payload.get("id", raw_id), payload.get("archived")))
        return 0

    print("Неизвестная команда: %s. "
          "status | list | archived | show <id> | archive <id> --done | unarchive <id>"
          % command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
