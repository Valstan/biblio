#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""Канал промтов раздела `MiniGen` в КАРМАНе (мандат brain D-190, промт #100).

Заказ владельца (промт #100): «довести канал промтов до живого состояния»,
а «канал закрыт — это не то, что нужно: выпуск токена раздела может сделать
сам проект, и собственная комната для этого не обязательна».

Разделы разведены по канону «один проект — один раздел»:

- раздел `biblio` — **дом утилит** (`tools/minimax-project-areas/karman_prompts.py`);
- раздел `MiniGen` — **программа «МиниГен»** (этот клиент).

Токен раздела `MiniGen` называется `PROMPTS_TOKEN_MINIGEN` (правило имени —
`sectionSecretName` у КАРМАНА: раздел в верхнем регистре, не-буквы и не-цифры
⇒ `_`). Право на раздел даёт **только** этот токен: чужой токен ⇒ 403,
секрет сервису не выдан ⇒ 503.

    python tools/minimax-project-areas/minigen_karman_prompts.py status
    python tools/minimax-project-areas/minigen_karman_prompts.py list
    python tools/minimax-project-areas/minigen_karman_prompts.py plans
    python tools/minimax-project-areas/minigen_karman_prompts.py archived
    python tools/minimax-project-areas/minigen_karman_prompts.py show <id>
    python tools/minimax-project-areas/minigen_karman_prompts.py archive <id> --done
    python tools/minimax-project-areas/minigen_karman_prompts.py unarchive <id>

    ( `--json` — машинный вывод; в отчётах и письмах значения токена быть не должно )

Внешние входы (в репозитории их быть не может — и адрес сервиса, и значение
токена; D-038):

  * токен раздела — файл ``%USERPROFILE%\.minigen-keys\prompts-token``
    (свой каталог программы), затем переменная ``PROMPTS_TOKEN_MINIGEN``;
  * адрес сервиса — файл ``vault-url`` в том же каталоге, затем файл
    ``%USERPROFILE%\.biblio-keys\vault-url`` (адрес сервиса один для всех, а
    не секрет), затем переменная ``KARMAN_URL``;
  * секрет канала библиотеки (``PROMPTS_INGEST_SECRET``) — переменная, затем
    файл ``prompts-ingest-secret``, затем **vault-комната проекта** (её читает
    токен комнаты ``room-token``) — так же, как это делает программа.

⚠️ **Токен раздела берётся только из своего каталога.** В каталоге дома утилит
(``.biblio-keys``) лежит токен **другого** раздела (``PROMPTS_TOKEN_BIBLIO``) —
подставить его сюда значит спросить не свой раздел и получить 403. Каталог
дома утилит — запасной источник **адреса**, не токена.

**Два канала, один раздел.** Раздел `MiniGen` читается **двумя** путями, и они
живут по-разному:

- **канал раздела** (``GET/POST /api/prompts/section``, токен
  ``PROMPTS_TOKEN_MINIGEN``) — он же архив: без него промт не убрать;
- **канал библиотеки** (``GET /api/prompts?project=...``, общий секрет канала
  ``PROMPTS_INGEST_SECRET``) — им программа читает **свой** раздел (вкладка
  «Промты»).

Живая проверка 26.11 (промт #100): канал раздела отвечает **503** (секрет
сервису не выдан, выдача #36 не принята), а раздел через канал библиотеки
**читается** — 11 промтов. Прежний клиент говорил «канал не настроен» и
промтов не показывал: очередь была видна в программе и невидима в инструменте.
Теперь клиент читает раздел **тем же путём, что и программа**, а состояние
канала архива показывает честно и отдельно.

Три урока из живых проверок (письмо brain от 23.11, пул G690):

  1. **Проверять файл, а не env.** Отсутствие переменной в окружении сессии —
     не отсутствие токена: значение лежит в файле.
  2. **Прокси из окружения мешает.** Мёртвый ``HTTP(S)_PROXY`` (выключенный
     Happ) роняет запрос — поэтому запросы идут напрямую, без прокси из
     переменных окружения. Вернуть прокси можно ``KARMAN_USE_ENV_PROXY=1``.
  3. **Нет токена — ноль запросов наружу.** Канал закрыт — это не повод дёргать
     сервис: честный отказ вместо 503 от чужого токена.

Значение токена наружу не выводится: в отчёте — только источник («file»/«env»).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

#: Раздел библиотеки промтов, который читает программа «МиниГен».
PROJECT = "MiniGen"
#: Переменная окружения с токеном раздела.
TOKEN_ENV = "PROMPTS_TOKEN_MINIGEN"
#: Переменная окружения с адресом сервиса.
URL_ENV = "KARMAN_URL"
#: Файлы с ключами (в каталоге ключей программы).
TOKEN_FILE = "prompts-token"
URL_FILE = "vault-url"
#: Секрет канала библиотеки промтов (общий секрет сервиса): им читается
#: библиотека, в том числе раздел программы. Имя — как у сервиса.
CHANNEL_SECRET = "PROMPTS_INGEST_SECRET"
CHANNEL_SECRET_ENV = "PROMPTS_INGEST_SECRET"
CHANNEL_SECRET_FILE = "prompts-ingest-secret"
#: Токен комнаты vault'а — им читается секрет канала.
ROOM_TOKEN_FILE = "room-token"
ROOM_TOKEN_ENV = "KARMAN_ROOM_TOKEN"
TIMEOUT = 20

#: Коды ответа сервиса — человеческим языком (мандат brain, раздел «Ответы»).
REASONS = {
    400: "плохой id промта",
    401: "токен не принят сервисом",
    403: "токен не от того раздела",
    404: "такого промта в разделе нет",
    429: "сервис просит подождать (частые запросы)",
    503: "канал не настроен: раздел не отдаётся "
         "(токен не выдан комнате сервиса или выдача не принята)",
}

#: Что делать, когда канал архива закрыт: сказать, **кто** и **что** может
#: открыть. Иначе «канал не настроен» — это не причина, а молчание.
ARCHIVE_HINT = ("Архив ставит только канал раздела: его токен должен быть выдан "
                "сервису (выдача в комнату-получателя — её принимает получатель). "
                "Сами промты при этом читаются — по каналу библиотеки.")


def with_archive_hint(text: str = "") -> str:
    """Причина + подсказка «что делать» — **двумя предложениями**.

    ⚠️ Живой случай 26.11 (промт #100): склейка через пробел читалась как
    «не настроен Архив ставит только канал раздела…» — два утверждения в
    одном предложении. Причина и подсказка — разные мысли, и читатель должен
    видеть, где кончается первая.
    """
    base = (text or "").strip() or REASONS.get(503, "")
    if base and not base.endswith((".", "!", "?", ":", ";")):
        base += "."
    return ("%s %s" % (base, ARCHIVE_HINT)).strip()


# ────────────────────────────────────────────────────────── имя секрета раздела

def section_secret_name(section: str) -> str:
    """Имя секрета раздела: ``PROMPTS_TOKEN_<Раздел>`` (правило КАРМАНА).

    ⚠️ Правило — **как у сервиса** (`lib/prompts/section-auth.ts`,
    `sectionSecretName`): раздел в верхнем регистре, а всё, что не буква и
    не цифра, ⇒ один ``_``. Своя копия правила, а не выдумка: подставить чужое
    имя — значит положить токен не туда, и канал молча не откроется.

        MiniGen      → PROMPTS_TOKEN_MINIGEN
        Matrica RMZ  → PROMPTS_TOKEN_MATRICA_RMZ
    """
    cleaned = re.sub(r"[^A-Z0-9]+", "_", str(section or "").strip().upper())
    return "PROMPTS_TOKEN_%s" % cleaned


#: Имя секрета своего раздела (то, что сервис ждёт).
SECTION_SECRET = section_secret_name(PROJECT)


# ────────────────────────────────────────────────────────── входы (файл, не env)

def keys_dirs() -> list[Path]:
    """Каталоги с файлами ключей — по порядку поиска.

    1. ``%USERPROFILE%\\.minigen-keys`` — свой каталог программы (или
       ``MINIGEN_KEYS_DIR``);
    2. ``%USERPROFILE%\\.biblio-keys`` — дом утилит на том же компьютере.

    ⚠️ Второй каталог — запасной источник **адреса** сервиса, но **не
    токена**: там лежит токен другого раздела, и подставить его сюда — значит
    спросить не свой раздел (403). Токен берётся из первого каталога.
    """
    out: list[Path] = []
    raw = (os.environ.get("MINIGEN_KEYS_DIR") or "").strip()
    if raw:
        out.append(Path(raw))
    else:
        out.append(Path.home() / ".minigen-keys")
    house = Path.home() / ".biblio-keys"
    if house not in out:
        out.append(house)
    return out


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _find(file_name: str, folders: list[Path]) -> tuple[str, str]:
    """(значение, каталог-источник) по списку каталогов ключей."""
    for folder in folders:
        value = _read_file(folder / file_name)
        if value:
            return value, folder.name
    return "", ""


def read_token() -> tuple[str, str]:
    """(значение, источник) — источник: ``file`` | ``env`` | ``''``.

    Токен — **только из своего каталога**: в каталоге дома утилит лежит
    токен другого раздела, и подставить его сюда — спросить не своё.
    """
    value, _folder = _find(TOKEN_FILE, keys_dirs()[:1])
    if value:
        return value, "file"
    value = (os.environ.get(TOKEN_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


def read_base() -> tuple[str, str]:
    """(адрес сервиса, источник) — источник: ``file`` | ``env`` | ``''``.

    Адрес один для всех разделов ⇒ запасной источник — каталог дома утилит.
    """
    value, _folder = _find(URL_FILE, keys_dirs())
    if value:
        return value, "file"
    value = (os.environ.get(URL_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


def read_room_token() -> tuple[str, str]:
    """(токен комнаты, источник) — им читается секрет канала из vault'а."""
    value, _folder = _find(ROOM_TOKEN_FILE, keys_dirs())
    if value:
        return value, "file"
    value = (os.environ.get(ROOM_TOKEN_ENV) or "").strip()
    if value:
        return value, "env"
    return "", ""


def read_channel_secret() -> tuple[str, str]:
    """(секрет канала библиотеки, источник).

    Источник: ``env`` | ``file`` | ``vault`` | ``''``. Секрет лежит в
    vault-комнате проекта — читает его токен комнаты (как это делает программа:
    `bridge/minigen_karman.py` → `vault_secret`).
    """
    value = (os.environ.get(CHANNEL_SECRET_ENV) or "").strip()
    if value:
        return value, "env"
    value, _folder = _find(CHANNEL_SECRET_FILE, keys_dirs())
    if value:
        return value, "file"
    value = _vault_secret(CHANNEL_SECRET)
    if value:
        return value, "vault"
    return "", ""


def _vault_secret(key: str) -> str:
    """Секрет из vault-комнаты проекта (по токену комнаты).

    Ошибка тут **не** исключение: канал может быть закрыт — и это не повод
    ронять инструмент.
    """
    token, _src = read_room_token()
    base, _base_src = read_base()
    if not token or not base:
        return ""
    url = base.rstrip("/") + "/api/secrets?key=" + urllib.parse.quote(key)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/json")
    try:
        with _opener().open(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, urllib.error.HTTPError):
        return ""
    try:
        payload = json.loads(raw)
    except ValueError:
        return ""
    return str((payload.get("secrets") or {}).get(key) or "")


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
    """Один вызов **канала раздела** (токен раздела).

    Код ``0`` — вызова не было вовсе (нет токена/адреса или сеть недоступна);
    в payload тогда лежит ``error`` с причиной.
    """
    token, token_src = read_token()
    base, base_src = read_base()
    if not token:
        return 0, {"error": "токена раздела %s нет ни в файле, ни в env" % SECTION_SECRET,
                   "token_source": "", "url_source": base_src}
    return call_with(token, method, path, body, token_src=token_src)


def call_with(token_value: str, method: str, path: str,
              body: dict | None = None, token_src: str = "") -> tuple[int, dict]:
    """Один вызов сервиса с **точным** токеном (каналом).

    Нужен, чтобы читать раздел по секрету канала, не выдавая его за токен
    раздела: право на канал — у того, чей секрет в запросе.
    """
    base, base_src = read_base()
    if not base:
        return 0, {"error": "адрес сервиса не задан (файл vault-url или %s)" % URL_ENV,
                   "token_source": token_src, "url_source": ""}

    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token_value)
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


def _section_path(archived: bool = False) -> str:
    """Путь канала раздела. Имя раздела — экранированное (кириллица)."""
    query = "&archived=1" if archived else ""
    return "/api/prompts/section?project=%s%s" % (urllib.parse.quote(PROJECT, safe=""), query)


def _library_path(archived: bool = False) -> str:
    """Путь канала библиотеки: тот же раздел, но по общему секрету канала.

    Им читается раздел в самой программе (вкладка «Промты») — то есть путь
    живой, даже когда канал раздела (архива) ещё не настроен.
    """
    query = "&archived=1" if archived else ""
    return "/api/prompts?project=%s%s" % (urllib.parse.quote(PROJECT, safe=""), query)


def read_prompts(archived: bool = False) -> dict:
    """Прочитать промты раздела. Возвращает:

    - ``http`` — код ответа того канала, который ответил (200 — прочитали);
    - ``path`` — ``section`` (канал раздела) или ``library`` (канал библиотеки);
    - ``section_http`` — код канала раздела всегда (503 ⇒ архив недоступен);
    - ``prompts`` — прочитанные промты; ``error`` — причина, если не вышло.

    ⚠️ Канал раздела может отвечать 503 (секрет сервису не выдан), а раздел при
    этом **читается** по секрету канала. Прежний клиент на 503 просто молчал:
    очередь была видна в программе и невидима в инструменте.
    """
    code, payload = call("GET", _section_path(archived=archived))
    if code == 200:
        return {"http": 200, "path": "section", "section_http": 200,
                "prompts": [normalize(p) for p in (payload.get("prompts") or [])],
                "error": ""}
    if code != 503:
        return {"http": code, "path": "section", "section_http": code,
                "prompts": [], "error": payload.get("error") or ""}
    secret, _src = read_channel_secret()
    if not secret:
        return {"http": code, "path": "section", "section_http": code,
                "prompts": [], "error": payload.get("error") or ""}
    code2, payload2 = call_with(secret, "GET", _library_path(archived=archived))
    if code2 != 200:
        return {"http": code, "path": "section", "section_http": code,
                "prompts": [], "error": payload.get("error") or ""}
    return {"http": 200, "path": "library", "section_http": code,
            "prompts": [normalize(p) for p in (payload2.get("prompts") or [])],
            "error": ""}


def status() -> dict:
    """Живой статус каналов: читается ли раздел и в каком канале."""
    token, token_src = read_token()
    base, base_src = read_base()
    out = {
        "project": PROJECT,
        "secret": SECTION_SECRET,
        "token_source": token_src,
        "url_source": base_src,
        "has_token": bool(token),
        "has_url": bool(base),
    }
    if not token:
        out.update({"http": 0, "ok": False, "prompts": 0, "read_path": "",
                    "section_http": 0,
                    "reason": "токена раздела %s нет ни в файле, ни в env "
                              "(файл %s в каталоге ключей программы)"
                              % (SECTION_SECRET, TOKEN_FILE)})
        return out
    if not base:
        out.update({"http": 0, "ok": False, "prompts": 0, "read_path": "",
                    "section_http": 0,
                    "reason": "адрес сервиса не задан (файл %s или %s)"
                              % (URL_FILE, URL_ENV)})
        return out
    res = read_prompts()
    out["http"] = res["http"]
    out["prompts"] = len(res["prompts"])
    out["read_path"] = res["path"]
    out["section_http"] = res.get("section_http", res["http"])
    out["ok"] = res["http"] == 200
    if res["path"] == "library":
        out["reason"] = ("раздел читается по секрету канала; канал раздела "
                         "(архив) отвечает %s" % res["section_http"])
    elif res["http"] == 200:
        out["reason"] = "раздел читается (канал раздела)"
    elif res["http"] == 0:
        out["reason"] = res.get("error") or "сеть недоступна"
    else:
        out["reason"] = REASONS.get(res["http"], "HTTP %s" % res["http"])
    return out


def list_prompts(archived: bool = False) -> tuple[int, list]:
    """Живые промты раздела (или архивные при ``archived=True``)."""
    res = read_prompts(archived=archived)
    return res["http"], res["prompts"]


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


def list_plans() -> tuple[int, list]:
    """Планы раздела (вид «план») — план идёт в паре к своему промту."""
    code, items = list_prompts()
    if code != 200:
        return code, []
    return code, [item for item in items if item["kind"] == "план"]


def archive(prompt_id: int, done: bool = False) -> tuple[int, dict]:
    """Отправить промт в архив — **только если задача выполнена**.

    Без ``done`` не архивируем: «не выполнил — не архивируй» (мандат brain).
    """
    if not done:
        return 0, {"error": "не выполнил — не архивируй: "
                           "подтверди выполнение флагом --done"}
    code, payload = call("POST", "/api/prompts/section",
                         {"project": PROJECT, "id": int(prompt_id), "archived": True})
    if code == 503:
        payload = dict(payload or {})
        payload["error"] = with_archive_hint(payload.get("error") or "")
    return code, payload


def unarchive(prompt_id: int) -> tuple[int, dict]:
    """Вернуть промт из архива (архив — не удаление)."""
    code, payload = call("POST", "/api/prompts/section",
                         {"project": PROJECT, "id": int(prompt_id), "archived": False})
    if code == 503:
        payload = dict(payload or {})
        payload["error"] = with_archive_hint(payload.get("error") or "")
    return code, payload


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
            print("   канал раздела (архив): HTTP %s | прочитано по: %s"
                  % (res.get("section_http"), res.get("read_path") or "—"))
            print("   токен раздела: %s (%s) | адрес: %s"
                  % (res["secret"], res["token_source"] or "нет",
                     res["url_source"] or "нет"))
        return 0 if res["ok"] else 1

    if command in ("list", "archived", "plans"):
        res = read_prompts(archived=(command == "archived"))
        code, items = res["http"], res["prompts"]
        if command == "plans":
            code, items = list_plans()
        if as_json:
            print(json.dumps({"http": code, "prompts": items,
                              "read_path": res.get("path") or "",
                              "section_http": res.get("section_http")},
                             ensure_ascii=False, indent=2))
            return 0 if code == 200 else 1
        if code != 200:
            print("не получилось: %s" % _explain(code),
                  file=sys.stderr)
            return 1
        if res.get("path") == "library":
            print("(прочитано по секрету канала; канал раздела — HTTP %s)"
                  % res.get("section_http"))
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
            # ⚠️ Причина из ответа сервиса — с подсказкой «что делать» (у 503
            # архива её добавляет `archive`); общий код ответа — запасной вариант.
            reason = str(payload.get("error") or "").strip() or _explain(code)
            print("не получилось: %s" % reason, file=sys.stderr)
            return 1
        print("Промт #%s: archived=%s"
              % (payload.get("id", raw_id), payload.get("archived")))
        return 0

    print("Неизвестная команда: %s. "
          "status | list | plans | archived | show <id> | archive <id> --done | unarchive <id>"
          % command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
