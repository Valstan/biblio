"""Токен копии «МиниГена»: выпуск, хранение, заявка и приём (промт #130).

Заказ владельца (25.11, задание 2 из четырёх): «каждая копия по умолчанию
получает свою папку в репозитории программы и токен только на неё; токен
выпускает владелец по заявке, приходящей ему в его собственный „МиниГен“».

**Что здесь и почему так**

- **токен копии** — секрет, который подтверждает право копии работать **со
  своей папкой** (`copies/<идентификатор копии>/`). Выпускает владелец; копия
  его получает и хранит **вне репозитория** (в сторе на машине), в программе
  виден только путь и **идентификатор** (отпечаток), а не значение;
- **привязка к папке** — токен бесполезен для чужой папки: при выпуске
  владелец считает **доказательство** (HMAC от самого токена над
  «экземпляр+папка»), и копия пересчитывает его. Токен другой копии
  отклоняется — это и есть «сервис», который ограничивает доступ;
- **как токен попадает к копии** — одной строкой: секрет и доказательство
  склеены (`секрет.доказательство`). Владелец отдаёт её копии (в поле окна),
  копия проверяет доказательство **против своей родословной** и кладёт
  секрет в стор. Доказательство секретом не является (из HMAC секрет не
  извлечь), но без него токен не подтвердить, поэтому оно едет вместе с
  секретом;
- **заявка на токен** — копия пишет её в раздел КАРМАНА (машинный канал) с
  родословной и подписью; владелец видит заявку в своём «МиниГене» и решает;
- **«чей это МиниГен»** — по родословной (`instance_id`, семейство, поколение,
  от какой версии форк) и по подписи заявки. Своя копия определяется по
  совпадению идентификатора с родословной **этой** программы; для чужих копий
  подпись предъявляется, а закрепляется (TOFU) владельцем вручную — потому
  что общий канал читают все, у кого он есть, и подделать заявку может любой.

⚠️ Честная граница (из рисков промта): **GitHub не умеет ограничивать токен
папкой внутри репозитория** — fine-grained PAT ограничивается репозиторием и
ролями, а не путём; ограничение по веткам и путям делают правила репозитория.
Поэтому токен копии — это **не** GitHub-кред: он подтверждает личность копии и
привязку к её папке, а правки копии попадают в программу через посредника
(пул наработок и программа владельца открывают PR), а не через прямой write
копии в общий репозиторий.

⚠️ Секрет — только в сторе на машине: в репозиторий, в журнал и в отчёты
значение токена не попадает (D-038). В ответах моста — путь и отпечаток.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import minigen_lineage as lineage

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
#: Стор на машине копии: ключи и токены живут **вне репозитория**.
CREDENTIALS = LOCAL / "credentials"
TOKEN_FILE = CREDENTIALS / "copy-token.txt"
META_FILE = CREDENTIALS / "copy-token-meta.json"
LOCAL_SECRET_FILE = CREDENTIALS / "copy-local-secret.txt"
#: Заявки на токен — в своём разделе КАРМАНА (их читает владелец).
REQUESTS_SECTION = "MiniGen-tokens"
#: Папка копии в общем репозитории программы.
COPIES_FOLDER = "copies"

TOKEN_BYTES = 32
PROOF_DOMAIN = "minigen-copy-token-v1"
REQUEST_KIND = "copy-token-request"
#: Разделитель секрета и доказательства в передаваемой строке.
SEPARATOR = "."
#: Куда кладутся записи о выпущенных токенах (вне репозитория).
ISSUED_DIR = CREDENTIALS / "issued"


def _now() -> int:
    return int(time.time() * 1000)


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        try:                       # Windows: доступ только владельцу
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ───────────────────────────────────────────────────────────── отпечаток

def token_id(secret: str) -> str:
    """Идентификатор токена (отпечаток) — без утечки самого секрета."""
    if not secret:
        return ""
    return hashlib.sha256(("minigen-token-id:" + secret).encode("utf-8")).hexdigest()[:12]


def mask(secret: str) -> str:
    """Маска для интерфейса: видны только последние четыре знака."""
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) > 4 else ""
    return "•" * 8 + tail if tail else "•" * 8


def _canonical(*parts: Any) -> str:
    return "|".join(str(part) for part in parts)


def safe_ident(value: str) -> str:
    """Идентификатор копии, пригодный для папки и имени файла.

    ⚠️ Идентификатор приходит из заявки (то есть из общего раздела), а он
    кладётся и в путь (`copies/<идентификатор>/`), и в имя файла ⇒ путь
    должен быть безопасным: оставляем только буквы, цифры, «-», «_», «.»,
    а пустое и «..» заменяем отпечатком.
    """
    raw = "".join(ch for ch in str(value or "")
                  if ch.isalnum() or ch in "-_.").strip()
    if not raw or raw in (".", "..") or ".." in raw:
        return "copy-%s" % hashlib.sha256(
            str(value or "").encode("utf-8")).hexdigest()[:12]
    return raw


def proof_for(secret: str, instance_id: str, folder: str) -> str:
    """Доказательство привязки токена к папке (HMAC от самого токена).

    Копия, получив токен, пересчитывает доказательство **против своей
    родословной** и сверяет: если токен выдан другой копии — доказательство
    не сойдётся ⇒ доступ к этой папке закрыт.
    """
    if not secret:
        return ""
    message = _canonical(PROOF_DOMAIN, instance_id, folder)
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def check_proof(secret: str, instance_id: str, folder: str, proof: str) -> bool:
    """Верна ли привязка токена к этой папке?"""
    if not secret or not proof:
        return False
    return hmac.compare_digest(proof_for(secret, instance_id, folder), proof)


def pack(secret: str, proof: str) -> str:
    """Передаваемая строка: секрет и доказательство вместе."""
    secret = (secret or "").strip()
    if not secret:
        return ""
    proof = (proof or "").strip()
    return "%s%s%s" % (secret, SEPARATOR, proof) if proof else secret


def unpack(value: str) -> tuple[str, str]:
    """Разобрать передаваемую строку: (секрет, доказательство)."""
    raw = (value or "").strip()
    if SEPARATOR not in raw:
        return raw, ""
    secret, _, proof = raw.rpartition(SEPARATOR)
    return secret.strip(), proof.strip()


# ─────────────────────────────────────────────────────────────── токен

def local_secret() -> tuple[str, str]:
    """Секрет копии для подписи заявок (вне репозитория; создаётся сам)."""
    value = _read(LOCAL_SECRET_FILE)
    if value:
        return value, "file"
    value = secrets.token_urlsafe(TOKEN_BYTES)
    _write(LOCAL_SECRET_FILE, value)
    return value, "file"


def read_token() -> tuple[str, str]:
    """Токен копии из стора (значение, источник)."""
    value = _read(TOKEN_FILE)
    return (value, "file") if value else ("", "")


def _meta() -> dict[str, Any]:
    try:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(data: dict[str, Any]) -> None:
    try:
        META_FILE.parent.mkdir(parents=True, exist_ok=True)
        META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except OSError:
        pass


def instance_folder(instance_id: str = "") -> str:
    """Папка копии в общем репозитории: `copies/<идентификатор копии>/`."""
    ident = instance_id or str(lineage.instance().get("instance_id") or "copy")
    return "%s/%s/" % (COPIES_FOLDER, safe_ident(ident))


def _issued_record_path(instance_id: str) -> Path:
    """Файл записи о выпущенном токене (в сторе, вне репозитория)."""
    return ISSUED_DIR / ("%s.json" % safe_ident(instance_id))


def issue(instance_id: str = "", folder: str = "", name: str = "") -> dict[str, Any]:
    """Выпустить токен копии (действие владельца).

    Возвращает **значение** токена один раз — владелец отдаёт его копии
    (в окно копии, поле «токен»): через общий раздел секреты ходить нельзя,
    раздел читают все, у кого есть канал.

    ⚠️ Токен **чужой** копии не трогает токен владельца: запись о выпуске
    лежит в отдельной папке (`issued/`), иначе выпуск по заявке из другой
    копии «съел» бы токен самого владельца.
    """
    ident = instance_id or str(lineage.instance().get("instance_id") or "copy")
    ident = safe_ident(ident)
    folder = folder or instance_folder(ident)
    secret = secrets.token_urlsafe(TOKEN_BYTES)
    issued = _now()
    proof = proof_for(secret, ident, folder)
    record = {"instance_id": ident, "folder": folder, "name": name,
              "issued_at": issued, "token_id": token_id(secret), "proof": proof}
    _write(_issued_record_path(ident), json.dumps(record, ensure_ascii=False,
                                                  indent=2))
    own_ident, _ = _own()
    if ident == own_ident:
        # выпуск самому себе: секрет сразу ложится в свой стор
        _write(TOKEN_FILE, secret)
        _write_meta(record)
    return {"ok": True, "token": pack(secret, proof), "token_id": token_id(secret),
            "proof": proof, "instance_id": ident, "folder": folder,
            "issued_at": issued, "path": str(_issued_record_path(ident)),
            "why": "токен выпущен для папки %s — отдайте его копии (значение "
                   "показывается один раз)" % folder}


def set_token(value: str, instance_id: str = "", folder: str = "",
              proof: str = "", issued_at: int = 0) -> dict[str, Any]:
    """Сохранить токен, полученный от владельца (копия вставляет его в окно).

    ⚠️ Authority — **своя родословная**: проверка идёт против папки этой
    копии, а не против того, что написано в присланной строке. Токен чужой
    копии не сохраняется вовсе: доступ к чужой папке закрыт.
    """
    raw = (value or "").strip()
    if not raw:
        return {"ok": False, "error": "пустой токен"}
    secret, packed_proof = unpack(raw)
    if not secret:
        return {"ok": False, "error": "пустой токен"}
    own_ident, own_folder = _own()
    proof = (packed_proof or proof or "").strip()
    if proof and not check_proof(secret, own_ident, own_folder, proof):
        # доказательство не сошлось с нашей родословной ⇒ это токен другой
        # копии (или подделка) ⇒ в стор не кладём
        return {"ok": False, "bound": False, "token_id": token_id(secret),
                "instance_id": own_ident, "folder": own_folder,
                "error": "токен не подтверждён под свою папку %s — похоже, он "
                         "выдан другой копии (доступ закрыт)" % own_folder,
                "why": "токен не принят: доказательство не сошлось с родословной "
                       "этой копии"}
    meta = _meta()
    _write(TOKEN_FILE, secret)
    _write_meta({"instance_id": own_ident, "folder": own_folder,
                 "name": str(meta.get("name") or ""),
                 "issued_at": int(issued_at or meta.get("issued_at") or _now()),
                 "token_id": token_id(secret), "proof": proof})
    ok = bool(proof)
    return {"ok": True, "token_id": token_id(secret), "bound": ok,
            "instance_id": own_ident, "folder": own_folder,
            "path": str(TOKEN_FILE),
            "why": ("токен принят и подтверждён: он выдан под папку %s" % own_folder
                    if ok else
                    "токен сохранён, но без доказательства: подтвердить папку %s "
                    "нечем — нужен код из выпуска (секрет.доказательство)"
                    % own_folder)}


def clear_token() -> dict[str, Any]:
    """Убрать токен из стора (значение стирается с диска)."""
    for path in (TOKEN_FILE, META_FILE):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
    return {"ok": True, "why": "токен убран из стора"}


def _own() -> tuple[str, str]:
    """Идентификатор и папка **этой** копии (по живой родословной).

    ⚠️ Живой случай 25.11: папку брали из записи токена, а не из родословной
    ⇒ токен **другой** копии проходил проверку (он ведь подтверждён под
    свою папку — чужую). authority — родословная этой копии.
    """
    ident = str(lineage.instance().get("instance_id") or "copy")
    return ident, instance_folder(ident)


def status() -> dict[str, Any]:
    """Состояние токена копии: путь, отпечаток, привязка (без значения)."""
    secret, source = read_token()
    meta = _meta()
    own_ident, own_folder = _own()
    meta_ident = str(meta.get("instance_id") or "")
    foreign = bool(meta_ident and meta_ident != own_ident)
    issued = int(meta.get("issued_at") or 0)
    proof = str(meta.get("proof") or "")
    bound = (not foreign) and bool(secret) and check_proof(
        secret, own_ident, own_folder, proof)
    out: dict[str, Any] = {
        "has_token": bool(secret),
        "token_id": token_id(secret) or str(meta.get("token_id") or ""),
        "masked": mask(secret),
        "path": str(TOKEN_FILE),
        "source": source,
        "instance_id": own_ident,
        "folder": own_folder,
        "issued_at": issued,
        "bound": bound,
        "reason": "",
    }
    if not secret:
        out["reason"] = ("токена нет — запросите его у владельца "
                         "(copy.token_request)")
    elif foreign:
        out["reason"] = ("токен выдан другой копии (%s) — своей папке он не "
                         "подходит" % meta_ident)
    elif not proof:
        out["reason"] = ("токен есть, но без доказательства — подтвердить папку %s "
                         "нечем (нужен код из выпуска: секрет.доказательство)"
                         % own_folder)
    elif not bound:
        out["reason"] = ("токен есть, но привязка к папке %s не подтвердилась — "
                         "похоже, он выдан другой копии" % own_folder)
    else:
        out["reason"] = "токен выдан под папку %s" % own_folder
    return out


def verify(folder: str = "", instance_id: str = "") -> dict[str, Any]:
    """Проверка доступа: токен есть и выдан **под эту** папку.

    Это и есть ограничение: токен другой копии к этой папке не подходит.
    """
    secret, _ = read_token()
    meta = _meta()
    own_ident, own_folder = _own()
    meta_ident = str(meta.get("instance_id") or "")
    want = (folder or own_folder).rstrip("/")
    if not secret:
        return {"ok": False, "reason": "токена нет — доступа нет",
                "folder": want, "token_id": ""}
    if meta_ident and meta_ident != own_ident:
        return {"ok": False, "folder": want, "token_id": token_id(secret),
                "reason": "токен выдан другой копии (%s) — доступ закрыт"
                          % meta_ident}
    proof = str(meta.get("proof") or "")
    if not check_proof(secret, own_ident, own_folder, proof):
        return {"ok": False, "folder": want, "token_id": token_id(secret),
                "reason": "токен не подтверждён для своей папки %s — "
                          "доступ закрыт" % own_folder}
    if want.rstrip("/") != own_folder.rstrip("/"):
        return {"ok": False, "folder": want, "token_id": token_id(secret),
                "reason": "токен выдан под папку %s, а просили %s — чужая папка, "
                          "доступ закрыт" % (own_folder, want)}
    return {"ok": True, "folder": own_folder, "token_id": token_id(secret),
            "reason": "доступ есть: токен подтверждён под папку %s" % own_folder}


# ────────────────────────────────────────────────────────── заявка

def request_payload(name: str = "") -> dict[str, Any]:
    """Заявка копии на токен: родословная + подпись (без секретов)."""
    data = lineage.instance()
    ident = str(data.get("instance_id") or "copy")
    secret, _ = local_secret()
    payload = {
        "kind": REQUEST_KIND,
        "instance_id": ident,
        "name": (name or "").strip() or ("копия %s" % ident),
        "folder": instance_folder(ident),
        "family": str(data.get("family") or "minigen"),
        "generation": int(data.get("generation") or 1),
        "fork_commit": str((data.get("fork_of") or {}).get("commit") or ""),
        "key_id": hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12],
        "signed_at": _now(),
    }
    payload["signature"] = hmac.new(
        secret.encode("utf-8"),
        _canonical(payload["instance_id"], payload["folder"], payload["key_id"],
                   payload["signed_at"]).encode("utf-8"),
        hashlib.sha256).hexdigest()
    return payload


def publish_request(section: str = REQUESTS_SECTION, name: str = "") -> dict[str, Any]:
    """Отправить заявку на токен в раздел КАРМАНА (машинный канал)."""
    import minigen_karman as karman
    payload = request_payload(name)
    section = (section or REQUESTS_SECTION).strip() or REQUESTS_SECTION
    code, payload_sections = karman.ensure_project(section)
    if code not in (200, 201):
        return {"ok": False, "error": "раздел не заведён: %s" % karman.explain(code),
                "section": section}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    code, answer = karman.publish(section,
                                  "Заявка на токен: копия %s" % payload["instance_id"],
                                  body)
    if code not in (200, 201):
        return {"ok": False, "error": "заявка не ушла: %s" % karman.explain(code),
                "section": section, "instance_id": payload["instance_id"]}
    return {"ok": True, "section": section,
            "instance_id": payload["instance_id"],
            "id": str(answer.get("id") or ""),
            "why": "заявка отправлена в раздел %s — ждём токен от владельца"
                   % section}


def _trust_file() -> Path:
    return LOCAL / "copy-trust.json"


def _trust() -> dict[str, Any]:
    try:
        data = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_trust(data: dict[str, Any]) -> None:
    try:
        _trust_file().parent.mkdir(parents=True, exist_ok=True)
        _trust_file().write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except OSError:
        pass


def trust(instance_id: str, key_id: str = "") -> dict[str, Any]:
    """Закрепить копию (TOFU): её отпечаток подписи запоминается.

    После этого заявки этой копии с другим отпечатком считаются подделкой.
    """
    ident = (instance_id or "").strip()
    if not ident:
        return {"ok": False, "error": "не указан идентификатор копии"}
    data = _trust()
    data[ident] = {"key_id": key_id.strip(), "trusted_at": _now()}
    _write_trust(data)
    return {"ok": True, "instance_id": ident,
            "why": "копия %s закреплена: её отпечаток подписи запомнен" % ident}


def requests(section: str = REQUESTS_SECTION) -> list[dict[str, Any]]:
    """Заявки на токен, которые пришли в раздел (с пометкой «свой/чужой»)."""
    import minigen_karman as karman
    import minigen_prompts as prompts_mod
    section = (section or REQUESTS_SECTION).strip() or REQUESTS_SECTION
    try:
        code, payload = karman.call("GET", "/api/prompts?project=%s" % section)
    except Exception:  # noqa: BLE001
        return []
    if code != 200:
        return []
    own = str(lineage.instance().get("instance_id") or "")
    pinned = _trust()
    out: list[dict[str, Any]] = []
    for item in payload.get("prompts") or []:
        if not isinstance(item, dict):
            continue
        try:
            data = json.loads(str(item.get("body") or ""))
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("kind") != REQUEST_KIND:
            continue
        ident = str(data.get("instance_id") or "")
        key_id = str(data.get("key_id") or "")
        mine = bool(own and ident == own)
        record = pinned.get(ident) or {}
        pinned_id = str(record.get("key_id") or "")
        if mine:
            relation = "своя копия (эта же программа)"
            signature = "подпись не проверяется: чужая подпись"
        elif not pinned_id:
            relation = "чужая копия (отпечаток не закреплён)"
            signature = "подпись предъявлена, отпечаток не закреплён"
        elif pinned_id == key_id:
            relation = "чужая копия (закреплённая)"
            signature = "отпечаток совпадает с закреплённым"
        else:
            relation = "чужая копия (отпечаток не совпал — возможна подделка)"
            signature = "отпечаток не совпал с закреплённым"
        out.append({
            "id": str(item.get("id") or ""),
            "instance_id": ident,
            "name": str(data.get("name") or ""),
            "folder": str(data.get("folder") or ""),
            "family": str(data.get("family") or ""),
            "generation": int(data.get("generation") or 1),
            "fork_commit": str(data.get("fork_commit") or ""),
            "key_id": key_id,
            "signed_at": int(data.get("signed_at") or 0),
            "signature": str(data.get("signature") or ""),
            "relation": relation,
            "signature_state": signature,
            "created": str(item.get("createdAt") or ""),
            "archived": bool(item.get("archivedAt")),
        })
    out.sort(key=lambda item: int(item.get("signed_at") or 0), reverse=True)
    return out
