"""Родословная копий «МиниГена» (заказ владельца 24.11).

Заказ: «в версии минигена можно заложить кодированную информацию, от какой
версии минигена он получил форк… отдельные семейства отслеживать, чтобы по
версии минигена можно было отследить, по какой ветке развития он пошел, с кем
перемешался, какие гены от кого в нем присутствуют».

Что здесь:

- **экземпляр** (`instance.json`) — кто эта копия: непрозрачный
  `instance_id` (случайный, не выводится из имени машины), семейство,
  поколение, от какой версии форк, кто родитель;
- **летопись** (`lineage.jsonl`) —append-only: « форк », « принят ген от
  такого-то », « опубликован ген »;
- **родословная** (`genealogy()`) — то и другое вместе.

Правила:

- **никаких имён машин и путей** в том, что уходит наружу (в пул генов):
  наружу уходит только `instance_id` и хеши коммитов;
- **append-only**: летопись не переписывается — это история;
- модуль — **чистый stdlib**: его читает и раскатка (`minigen_rollout.py`),
  и мост программы, поэтому electron тут не нужен.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
INSTANCE_FILE = LOCAL / "instance.json"
LEDGER_FILE = LOCAL / "lineage.jsonl"

FAMILY = "minigen"
#: Корень папок копий в общем репозитории программы.
COPIES_FOLDER = "copies"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        pass


def program_version() -> dict[str, Any]:
    """Версия установленной программы (`version.json`), если есть."""
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "МиниГен"
    return _read_json(root / "version.json")


def instance() -> dict[str, Any]:
    """Данные этой копии. Создаются при первом обращении."""
    data = _read_json(INSTANCE_FILE)
    if data.get("instance_id"):
        return data
    version = program_version()
    data = {
        "instance_id": "mg-" + uuid.uuid4().hex[:8],
        "family": FAMILY,
        "generation": 1,
        "birth": _now_ms(),
        # от какой версии форк: коммит и время установки программы
        "fork_of": {
            "commit": str(version.get("commit") or ""),
            "installed_at": int(version.get("installed_at") or 0),
        },
        "parent": {},
        "genes": [],
    }
    _write_json(INSTANCE_FILE, data)
    record("born", commit=data["fork_of"].get("commit") or "",
           note="копия создана на версии %s" % (data["fork_of"].get("commit") or "?"))
    return data


def safe_ident(value: str) -> str:
    """Идентификатор копии, пригодный для пути (папка, имя файла).

    ⚠️ Идентификатор приходит из заявки (то есть из общего раздела), а он
    кладётся и в путь (`copies/<идентификатор>/`), и в имя файла ⇒ путь
    должен быть безопасным: оставляем только буквы, цифры, «-», «_», «.»,
    а пустое и «..» заменяем отпечатком.

    Здесь, в родословной, — **единственное** место, где этот путь
    строится: и клон, и токен берут папку отсюда, иначе доказательство
    привязки токена считалось бы от другой строки.
    """
    raw = "".join(ch for ch in str(value or "")
                  if ch.isalnum() or ch in "-_.").strip()
    if not raw or raw in (".", "..") or ".." in raw:
        return "copy-%s" % hashlib.sha256(
            str(value or "").encode("utf-8")).hexdigest()[:12]
    return raw


def copies_folder(instance_id: str = "") -> str:
    """Папка копии в общем репозитории программы: `copies/<идентификатор>/`.

    Папка — по родословной: идентификатор копии не выводится из имени
    машины, наружу уходит только он (D-038).
    """
    ident = safe_ident(instance_id or str(instance().get("instance_id") or "copy"))
    return "%s/%s/" % (COPIES_FOLDER, ident)


def record(event: str, **fields: Any) -> None:
    """Дописать строку в летопись (append-only)."""
    entry = {"ts": _now_ms(), "event": event}
    entry.update({k: v for k, v in fields.items() if v not in (None, "", [])})
    try:
        LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def adopt(gene_id: str, from_instance: str = "", from_commit: str = "",
          note: str = "") -> dict[str, Any]:
    """Принять «ген» (наработку другой копии): родословная обновляется."""
    data = instance()
    genes = list(data.get("genes") or [])
    genes.append({"gene": gene_id, "from": from_instance,
                  "commit": from_commit, "ts": _now_ms()})
    data["genes"] = genes[-50:]
    data["generation"] = int(data.get("generation") or 1) + 1
    if from_instance:
        data["parent"] = {"instance": from_instance, "gene": gene_id,
                          "commit": from_commit}
    _write_json(INSTANCE_FILE, data)
    record("adopt", gene=gene_id, source=from_instance, commit=from_commit,
           note=note)
    return data


def published(gene_id: str, commit: str = "", note: str = "") -> None:
    """Отметить публикацию своей наработки в общий пул."""
    record("publish", gene=gene_id, commit=commit, note=note)


def ledger(limit: int = 100) -> list[dict[str, Any]]:
    """Летопись: последние записи, новые — первыми."""
    try:
        if not LEDGER_FILE.is_file():
            return []
        lines = LEDGER_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= max(int(limit or 1), 1):
            break
    return out


def genealogy() -> dict[str, Any]:
    """Родословная этой копии: кто она, от кого форк, что приняла."""
    data = instance()
    return {
        "instance": data,
        "history": ledger(limit=50),
        "instance_file": str(INSTANCE_FILE),
        "ledger_file": str(LEDGER_FILE),
    }


def stamp(version: dict[str, Any]) -> dict[str, Any]:
    """Вписать родословную в описание версии программы (`version.json`).

    В версию кладётся только то, что не выдаёт машину: `instance_id`,
    семейство, поколение, от какой версии форк, кто родитель.
    """
    data = instance()
    version = dict(version or {})
    version["lineage"] = {
        "instance_id": data.get("instance_id"),
        "family": data.get("family", FAMILY),
        "generation": int(data.get("generation") or 1),
        "fork_of": data.get("fork_of") or {},
        "parent": data.get("parent") or {},
        "genes": len(data.get("genes") or []),
    }
    return version
