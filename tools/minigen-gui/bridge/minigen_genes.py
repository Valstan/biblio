"""Пул генов: обмен наработками между копиями «МиниГена» (заказ 24.11).

Заказ: «все копии минигена должны уметь обмениваться между собой кусками
документации, новыми фишками и идеями… можно даже вести родословную каждого
нового клона… какие гены от кого в нем присутствуют». Метафора владельца —
обмен генетической информацией между бактериями.

**Ген** — это одна наработка: манифест + diff + заметка. Формат простой и
проверяемый:

```
genes/<идентификатор копии>/<идентификатор гена>/
    manifest.json   ← что это, кто автор, на какой версии сделано, какие файлы
    patch.diff      ← изменения (унифицированный diff, применяется git apply)
    notes.md        ← зачем и как это работает
```

Пул — отдельный репозиторий GitHub (`Valstan/minigen-genepool`), клон рядом
с данными программы. Обмен — обычные push/pull: копия публикует свой ген и
читает чужие.

Правила (важны, потому что гены — это чужой код):

- **внедрение — только через ветку + PR + зелёные проверки**: решение
  остаётся за владельцем; автоматически чужой код в программу не попадает;
- **родословная**: принятый ген записывается в летопись копии (от кого, какой
  коммит, **в какой коммит этой копии он встал**), а в пуле — свой файл
  `lineage/<копия>.jsonl` (по файлу на копию: общий журнал конфликтовал бы
  при слиянии);
- **откат** (промт КАРМАНА #187): ген можно откатить — `git revert` коммита
  гена в ветке, где он стоит; после отката состояние совпадает с состоянием
  до внедрения, а ген помечается «не ставить» и в список чужих наработок
  больше не попадает;
- **оценки**: любой ген можно оценить от 1 до 10 (звёздочки); голоса лежат в
  том же файле копии ⇒ одновременная запись не конфликтует, а рейтинг
  считается по всем копиям;
- **внешний вид**: ген, трогающий оформление, помечается заранее, а
  внедрить его без явного решения владельца нельзя;
- **наружу — только идентификатор копии и хеши коммитов**: ни имён машин, ни
  путей (D-038).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import minigen_code as code
import minigen_lineage as lineage

GENES_DIR = "genes"
LINEAGE_DIR = "lineage"
MAX_NOTES = 8000

#: Оценка гена — звёздочки от 1 до 10 (заказ владельца 28.11).
RATING_MIN = 1
RATING_MAX = 10


# ─────────────────────────────────────── внешний вид программы (промт #187)

#: Что считаем «внешним видом» программы.
#:
#: ⚠️ Границу выбрал исполнитель (в промте сказано «исполнителю решать»).
#: Требование владельца одно: **по умолчанию установка гена вид не меняет,
#: а если меняет — это видно заранее**. Поэтому ген, трогающий эти файлы,
#: помечается в списке заранее, а внедрять его без явного решения владельца
#: нельзя (`adopt(..., allow_appearance=False)` — отказ с объяснением).
#:
#: В список попадает: темы и стили, иконки, шрифты, раскладка и каркас окна.
APPEARANCE_SUFFIXES = (".css", ".scss", ".sass", ".less",
                        ".svg", ".ico",
                        ".woff", ".woff2", ".ttf", ".otf", ".eot")
#: Папки, где лежит оформление целиком.
APPEARANCE_PARTS = ("theme", "themes", "icons", "fonts", "layout", "styles")
#: Имена файлов оформления (без расширения).
APPEARANCE_NAMES = ("theme", "themes", "style", "styles", "layout",
                    "palette", "fonts", "icons")
#: Расширения, в которых имя оформления что-то значит (`theme.json`,
#: `theme.ts` — оформление; `theme.py` — нет, это код).
APPEARANCE_EXTS = (".css", ".scss", ".sass", ".less", ".json", ".ts", ".tsx",
                   ".js", ".jsx", ".html", ".svg", ".ico")
#: Каркас окна: там раскладка панелей и шапки.
APPEARANCE_FILES = ("src/renderer/src/app.tsx",)


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().lower()


def appearance_files(files: list[str]) -> list[str]:
    """Какие из файлов трогают внешний вид программы."""
    out: list[str] = []
    for name in files or []:
        path = _norm(name)
        if not path:
            continue
        parts = [part for part in path.split("/") if part]
        base = parts[-1] if parts else ""
        suffix = base[base.rfind("."):] if "." in base else ""
        stem = base[:len(base) - len(suffix)] if suffix else base
        if path.endswith(APPEARANCE_SUFFIXES):
            out.append(name)
            continue
        if len(parts) > 1 and any(part in APPEARANCE_PARTS for part in parts[:-1]):
            out.append(name)
            continue
        if stem in APPEARANCE_NAMES and suffix in APPEARANCE_EXTS:
            out.append(name)
            continue
        if any(path.endswith(item) for item in APPEARANCE_FILES):
            out.append(name)
    return out


def appearance_of(gene: dict[str, Any]) -> dict[str, Any]:
    """Трогает ли ген внешний вид программы (по манифесту или по файлам)."""
    data = gene.get("appearance")
    if isinstance(data, dict) and "touches" in data:
        return {"touches": bool(data.get("touches")),
                "files": [str(item) for item in (data.get("files") or [])]}
    files = appearance_files([str(item) for item in (gene.get("files") or [])])
    return {"touches": bool(files), "files": files}


def pool_dir() -> Path:
    """Каталог пула наработок.

    ⚠️ В локальном режиме (сети нет, заказ владельца 25.11) пул — обычная
    папка на машине: гены кладутся в неё, а обмен идёт **вручную** (выгрузить
    папку — отнести соседней копии). Git-клон пула, если он уже есть, читается
    как раньше.
    """
    import minigen_clone
    try:
        import minigen_copy
        if minigen_copy.is_local():
            local = minigen_copy.LOCAL / "genepool-local"
            if not (local / ".git").is_dir():
                return local
    except Exception:  # noqa: BLE001
        pass
    return minigen_clone.POOL_DIR


def is_pool_git() -> bool:
    """Пул — git-репозиторий (тогда гены можно коммитить и пушить)?"""
    return (pool_dir() / ".git").is_dir()


def _now() -> int:
    return int(time.time() * 1000)


def _pool_ready() -> tuple[bool, str]:
    path = pool_dir()
    if _local_mode():
        # Локальный режим: сети нет, пул — обычная папка на машине. Её
        # создаёт первая же публикация, поэтому «папки нет» — не отказ:
        # публикация её создаст.
        return True, ""
    if not (path / ".git").is_dir():
        return False, "клона пула нет — сначала clone.setup"
    return True, ""


def _local_mode() -> bool:
    """Режим «только локально, без сети»?"""
    try:
        import minigen_copy
        return bool(minigen_copy.is_local())
    except Exception:  # noqa: BLE001
        return False


def _git(args: list[str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
    return code.git(args, cwd, timeout=timeout)


def gene_id(instance_id: str = "") -> str:
    """Идентификатор гена: `gene-<копия>-<номер>` (например `gene-mg-693f8fdd-001`)."""
    ident = instance_id or str(lineage.instance().get("instance_id") or "copy")
    number = 1
    base = pool_dir() / GENES_DIR / ident
    try:
        for item in base.iterdir():
            match = re.match(r"gene-%s-(\d+)$" % re.escape(ident), item.name)
            if match:
                number = max(number, int(match.group(1)) + 1)
    except OSError:
        pass
    return "gene-%s-%03d" % (ident, number)


def _gene_dir(gene: str) -> Path:
    """Каталог гена в пуле: `genes/<копия>/<ген>`."""
    match = re.match(r"^gene-(.+)-(\d+)$", gene or "")
    ident = match.group(1) if match else ""
    return pool_dir() / GENES_DIR / ident / gene


def _manifest(gene: str) -> Path:
    return _gene_dir(gene) / "manifest.json"


def list_genes() -> list[dict[str, Any]]:
    """Все гены пула (новые — первыми): с рейтингом и пометкой «трогает вид»."""
    out: list[dict[str, Any]] = []
    base = pool_dir() / GENES_DIR
    if not base.is_dir():
        return out
    for manifest_path in base.glob("*/*/manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data["path"] = str(manifest_path.parent.relative_to(pool_dir())).replace("\\", "/")
            out.append(data)
    out.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
    votes = ratings()
    for item in out:
        gene = str(item.get("id") or "")
        item["rating"] = votes.get(gene) or _empty_rating()
        look = appearance_of(item)
        item["touches_appearance"] = look["touches"]
        item["appearance_files"] = look["files"]
    return out


# ─────────────────────────────────────────── оценки генов (звёздочки 1–10)

def _empty_rating() -> dict[str, Any]:
    return {"votes": 0, "average": 0.0, "my_vote": 0, "voters": []}


def pool_events() -> list[dict[str, Any]]:
    """Все события летописи пула — по всем копиям.

    Файл на копию (`lineage/<копия>.jsonl`) ⇒ одновременная запись двух копий
    не конфликтует: дописывают они **разные** файлы.
    """
    out: list[dict[str, Any]] = []
    directory = pool_dir() / LINEAGE_DIR
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                out.append(record)
    return out


def ratings() -> dict[str, dict[str, Any]]:
    """Рейтинг генов по всем копиям: сколько голосов и средняя оценка.

    ⚠️ **Один голос от копии на ген**: повторная оценка заменяет прежнюю
    (последняя — актуальная). Иначе одна копия могла бы «разогнать» рейтинг
    гена, а решение владельца о том, как считать при разном числе голосов,
    в промте отдано исполнителю — мы показываем **и** среднее, **и** число
    голосов, чтобы решение принималось по обоим.
    """
    votes: dict[str, dict[str, int]] = {}
    for record in pool_events():
        if str(record.get("event") or "") != "rated":
            continue
        gene = str(record.get("gene") or "")
        score = record.get("score")
        if not gene or not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        value = int(score)
        if value < RATING_MIN or value > RATING_MAX:
            continue
        instance = str(record.get("instance") or "copy")
        votes.setdefault(gene, {})[instance] = value
    me = str(lineage.instance().get("instance_id") or "")
    out: dict[str, dict[str, Any]] = {}
    for gene, by_copy in votes.items():
        values = list(by_copy.values())
        out[gene] = {
            "votes": len(values),
            "average": round(sum(values) / len(values), 2) if values else 0.0,
            "my_vote": int(by_copy.get(me, 0)),
            "voters": sorted(by_copy),
        }
    return out


def rate(gene: str, score: int) -> dict[str, Any]:
    """Поставить оценку гену (1–10 звёздочек).

    Оценку может поставить **любая** копия: голос уходит в летопись пула
    (файл на копию) ⇒ одновременная запись не конфликтует.
    """
    gene = str(gene or "").strip()
    if not gene:
        return {"error": "не указан ген"}
    try:
        value = int(score)
    except (TypeError, ValueError):
        return {"error": "оценка — число от %d до %d" % (RATING_MIN, RATING_MAX)}
    if value < RATING_MIN or value > RATING_MAX:
        return {"error": "оценка — число от %d до %d (звёздочек)"
                         % (RATING_MIN, RATING_MAX)}
    if not append_pool_event("rated", gene=gene, score=value):
        return {"error": "не записать оценку в летопись пула"}
    pushed = _push_pool_lineage("rating: %s — %d из %d" % (gene, value, RATING_MAX))
    data = ratings().get(gene) or _empty_rating()
    return {"ok": True, "gene": gene, "score": value,
            "votes": data.get("votes", 0), "average": data.get("average", 0.0),
            "my_vote": value, "pushed": pushed,
            "why": "оценка поставлена: %d из %d (голосов: %d)"
                   % (value, RATING_MAX, data.get("votes", 0))}


def _push_pool_lineage(message: str = "") -> bool:
    """Отправить летопись пула (оценки, откаты) в общий репозиторий.

    ⚠️ Наружу уходят только идентификатор копии и хеши коммитов (D-038):
    оценка гена — число, путей и имён машин в ней нет.
    """
    if not is_pool_git():
        return False
    add = _git(["add", "-A", LINEAGE_DIR], pool_dir())
    if not add.get("ok"):
        return False
    commit = _git(["commit", "-m", message or "lineage: оценки и откаты"],
                  pool_dir(), timeout=180)
    if not commit.get("ok"):
        return False
    if _local_mode():
        return False
    push = _git(["push", "origin", "HEAD:refs/heads/pool"], pool_dir(), timeout=300)
    return bool(push.get("ok"))


def _read_patch_bytes(gene: str) -> bytes:
    """Патч гена — байтами (как его записал git)."""
    try:
        return (_manifest(gene).parent / "patch.diff").read_bytes()
    except OSError:
        return b""


def read_gene(gene: str) -> dict[str, Any]:
    """Ген целиком: манифест, заметка и diff (diff — с обрезкой)."""
    directory = _manifest(gene).parent
    if not directory.is_dir():
        return {"error": "нет гена: %s" % gene}
    result: dict[str, Any] = {}
    try:
        result["manifest"] = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result["manifest"] = {}
    for name, key in (("notes.md", "notes"), ("patch.diff", "patch")):
        try:
            text = (directory / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        result[key] = text[:MAX_NOTES]
    return result


def _current_diff() -> tuple[bytes, list[str]]:
    """Diff текущей правки копии (worktree правки или текущая ветка клона).

    ⚠️ Diff берётся **сырыми байтами**: текстовый режим срезает финальный
    перевод строки, и git apply такой патч считает битым («corrupt patch»).
    """
    import minigen_clone
    worktree = code.current_worktree()
    if worktree is not None:
        return _git_bytes(["diff", "master"], worktree), code.changed_files(worktree)
    clone = minigen_clone.CLONE_DIR
    if (clone / ".git").is_dir():
        return _git_bytes(["diff", "HEAD"], clone), code.changed_files(clone)
    return b"", []


def _git_bytes(args: list[str], cwd: Path, timeout: int = 180) -> bytes:
    """Git без текстовой нормализации: байты вывода как есть."""
    import subprocess
    try:
        proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                              timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return b""
    return proc.stdout or b""


def publish(title: str, summary: str = "", notes: str = "",
            worktree: Path | None = None) -> dict[str, Any]:
    """Опубликовать свою наработку в пул (ветка + push в пул).

    Опасное действие: уходит в общий репозиторий ⇒ только после подтверждения
    владельца.
    """
    title = (title or "").strip()
    if not title:
        return {"error": "нужно название наработки"}
    ready, why = _pool_ready()
    if not ready:
        return {"error": why}
    diff, files = _current_diff()
    if not diff.strip():
        return {"error": "нет изменений — нечего публиковать (сначала code.branch "
                         "и code.patch)"}
    data = lineage.instance()
    gene = gene_id(str(data.get("instance_id") or ""))
    directory = _manifest(gene).parent
    directory.mkdir(parents=True, exist_ok=True)
    head = ""
    source = worktree or code.current_worktree()
    if source is not None:
        head = _git(["rev-parse", "--short", "HEAD"], source).get("out") or ""
    manifest = {
        "id": gene,
        "title": title,
        "summary": (summary or "").strip()[:600],
        "author": str(data.get("instance_id") or ""),
        "family": str(data.get("family") or "minigen"),
        "created_at": _now(),
        "base_commit": head,
        "files": files,
        "version": str(data.get("fork_of", {}).get("commit") or ""),
    }
    # ⚠️ Пометка «трогает внешний вид» — в манифесте, чтобы её увидели и
    # те копии, у которых нет списка файлов под рукой (промт КАРМАНА #187):
    # вид не должен меняться молча, а сюрприз после внедрения — неправило.
    look = appearance_of({"files": files})
    manifest["appearance"] = {"touches": look["touches"], "files": look["files"]}
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # патч — байтами: текстовая запись срезает/ломает переводы строк, и git
    # apply такой патч считает битым («corrupt patch» — живой случай 24.11)
    (directory / "patch.diff").write_bytes(diff)
    (directory / "notes.md").write_text(
        (notes or summary or title).strip()[:MAX_NOTES], encoding="utf-8")
    local = _local_mode()
    if is_pool_git():
        add = _git(["add", "-A", GENES_DIR], pool_dir())
        if not add.get("ok"):
            return {"error": "не добавить в индекс: %s" % (add.get("err") or add.get("out"))}
        commit = _git(["commit", "-m", "gene: %s (%s)" % (title, gene)], pool_dir(), timeout=180)
        if not commit.get("ok"):
            return {"error": "коммит не прошёл: %s" % (commit.get("err") or commit.get("out"))}
        if not local:
            push = _git(["push", "origin", "HEAD:refs/heads/pool"], pool_dir(), timeout=300)
            if not push.get("ok"):
                return {"error": "пуш в пул не прошёл: %s" % (push.get("err") or push.get("out"))}
    lineage.published(gene, commit=head, note=title)
    _append_pool_lineage(gene, "published")
    if local:
        # ⚠️ Локальный режим: сети нет, пул наружу не уходит. Говорим прямо и
        # показываем путь, которым наработка попадёт к соседней копии.
        return {"ok": True, "gene": gene, "manifest": manifest, "local_only": True,
                "why": "ген сохранён только на этой машине: локальный режим, "
                       "сети нет. Отнесите его соседней копии вручную "
                       "(genes.export)"}
    return {"ok": True, "gene": gene, "manifest": manifest,
            "why": "опубликовано в пул: %s" % gene}


def append_pool_event(event: str, gene: str = "", **fields: Any) -> bool:
    """Дописать событие в летопись пула (свой файл на копию).

    Общий журнал конфликтовал бы: копии пишут в пул одновременно.
    """
    data = lineage.instance()
    instance = str(data.get("instance_id") or "copy")
    record = {"ts": _now(), "event": event, "instance": instance}
    if gene:
        record["gene"] = gene
    for key, value in fields.items():
        if value not in (None, "", [], {}):
            record[key] = value
    try:
        directory = pool_dir() / LINEAGE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ("%s.jsonl" % instance)).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def record_birth(fork_commit: str = "", **fields: Any) -> bool:
    """Отметить в пуле рождение копии: «в какой точке родилась».

    Карта родословной общая для всех копий (заказ владельца 24.11): кто
    появился, от какой версии форк — и дальше по этой точке видно, кто у кого
    брал гены.
    """
    return append_pool_event("born", fork_commit=fork_commit, **fields)


def publish_birth(fork_commit: str = "", **fields: Any) -> dict[str, Any]:
    """Записать рождение копии в пул **и отправить в общий репозиторий**.

    Без отправки событие осталось бы на этой машине, и карта у соседей не
    увидела бы, что появилась новая копия.
    """
    ready, why = _pool_ready()
    if not ready:
        return {"ok": False, "error": why}
    data = lineage.instance()
    instance = str(data.get("instance_id") or "copy")
    if not record_birth(fork_commit, **fields):
        return {"ok": False, "error": "не записать в летопись пула"}
    add = _git(["add", "-A", LINEAGE_DIR], pool_dir())
    if not add.get("ok"):
        return {"ok": False, "error": "не добавить в индекс: %s"
                % (add.get("err") or add.get("out"))}
    commit = _git(["commit", "-m", "born: %s (карта родословной)" % instance],
                  pool_dir(), timeout=180)
    if not commit.get("ok"):
        return {"ok": False, "error": "коммит не прошёл: %s"
                % (commit.get("err") or commit.get("out"))}
    push = _git(["push", "origin", "HEAD:refs/heads/pool"], pool_dir(), timeout=300)
    if not push.get("ok"):
        return {"ok": False, "error": "пуш в пул не прошёл: %s"
                % (push.get("err") or push.get("out"))}
    return {"ok": True, "instance": instance, "fork_commit": fork_commit,
            "why": "рождение копии записано в пул"}


def _append_pool_lineage(gene: str, event: str) -> None:
    """Летопись пула: свой файл на копию (общий журнал конфликтовал бы)."""
    append_pool_event(event, gene=gene)


# ─────────────────────────────────────────── подсказки «подходящих» генов

def _area(path: str) -> str:
    """Часть программы, к которой относится файл (`tools/minigen-gui`)."""
    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    if not parts:
        return ""
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def my_files() -> list[str]:
    """Файлы, которые копия трогает сейчас: правка в работе или свежие коммиты.

    Это **признак совпадения** для подсказок: «подходящая наработка» — та,
    что трогает те же файлы или ту же часть программы. Критерия от владельца
    не было, поэтому он прозрачный и показывается вместе с причиной.
    """
    out: list[str] = []
    worktree = code.current_worktree()
    if worktree is not None:
        for name in code.changed_files(worktree):
            if name not in out:
                out.append(name)
        return out
    try:
        repo = code.REPO
    except Exception:  # noqa: BLE001
        repo = None
    if repo is None:
        return out
    res = _git(["log", "--name-only", "-n", "5", "--pretty=format:"], repo)
    for line in (res.get("out") or "").splitlines():
        name = line.strip()
        if name and name not in out:
            out.append(name)
    return out


def installed_genes() -> list[dict[str, Any]]:
    """Гены, которые эта копия внедрила — с коммитом, куда они встали.

    ⚠️ Именно этот список — то, что можно **откатить**: у гена есть свой
    коммит в этой копии (`local_commit`), и revert отменяет конкретный
    коммит, а не «что-то похожее».
    """
    data = lineage.instance()
    manifests = {str(item.get("id") or ""): item for item in list_genes()}
    rolled = {str(item.get("gene") or "") for item in lineage.rolled_back_genes()}
    votes = ratings()
    out: list[dict[str, Any]] = []
    for item in (data.get("genes") or []):
        gene = str(item.get("gene") or "")
        if not gene:
            continue
        manifest = manifests.get(gene, {})
        out.append({
            "id": gene,
            "gene": gene,
            "title": str(manifest.get("title") or ""),
            "author": str(item.get("from") or manifest.get("author") or ""),
            "commit": str(item.get("local_commit") or ""),
            "base_commit": str(item.get("commit") or ""),
            "branch": str(item.get("branch") or ""),
            "ts": int(item.get("ts") or 0),
            "rolled_back": gene in rolled,
            "rating": votes.get(gene) or _empty_rating(),
        })
    return out


def _pool_rolled_back() -> dict[str, list[str]]:
    """Какие гены откатывали копии: ген → список копий."""
    out: dict[str, list[str]] = {}
    for record in pool_events():
        if str(record.get("event") or "") != "rolled_back":
            continue
        gene = str(record.get("gene") or "")
        instance = str(record.get("instance") or "")
        if not gene:
            continue
        copies = out.setdefault(gene, [])
        if instance and instance not in copies:
            copies.append(instance)
    return out


def suggestions() -> dict[str, Any]:
    """Чужие наработки, которые стоит посмотреть: с причиной совпадения.

    Кто прислал — видно, совпадает ли работа — тоже. Чужой ген никогда не
    попадает в программу молча: только через ветку и PR (решение — за
    владельцем).

    ⚠️ **Промт КАРМАНА #187**: ген, который эта копия **внедряла и откатывала**
    (или пометила «не ставить»), в список чужих наработок **не попадает** —
    он виден отдельной строкой с пометкой «уже ставили и откатывали».
    Ген, откатанный **другой** копией, остаётся в списке, но с пометкой:
    соседу полезно знать, что его уже откатывали.
    """
    data = lineage.instance()
    me = str(data.get("instance_id") or "")
    taken = {str(item.get("gene") or "") for item in (data.get("genes") or [])}
    not_wanted = lineage.not_wanted_genes()
    rolled_back = {str(item.get("gene") or "")
                   for item in lineage.rolled_back_genes()}
    #: «не хотим» = и помеченные «не ставить», и откатанные нами.
    #: ⚠️ Внедрённые, но **не** откатанные гены сюда не входят: они стоят в
    #: программе, их видно в «Внедрённые гены» (и оттуда откатывают).
    refused = not_wanted | rolled_back
    rolled_elsewhere = _pool_rolled_back()
    votes = ratings()
    files = my_files()
    areas = {_area(name) for name in files if _area(name)}
    items: list[dict[str, Any]] = []
    refused_items: list[dict[str, Any]] = []
    for gene in list_genes():
        gene_id_ = str(gene.get("id") or "")
        author = str(gene.get("author") or "")
        if not gene_id_:
            continue
        rating = votes.get(gene_id_) or _empty_rating()
        elsewhere = [name for name in rolled_elsewhere.get(gene_id_, [])
                     if name != me]
        base_item = {
            "id": gene_id_,
            "gene": gene_id_,
            "title": str(gene.get("title") or ""),
            "summary": str(gene.get("summary") or ""),
            "author": author,
            "created_at": int(gene.get("created_at") or 0),
            "files": [str(name) for name in (gene.get("files") or [])],
            "rating": rating,
            "votes": rating.get("votes", 0),
            "average": rating.get("average", 0.0),
            "my_vote": rating.get("my_vote", 0),
            "touches_appearance": bool(gene.get("touches_appearance")),
            "appearance_files": [str(name)
                                 for name in (gene.get("appearance_files") or [])],
            "rolled_back_elsewhere": bool(elsewhere),
            "rolled_back_by": elsewhere,
        }
        if author and author == me:
            continue
        if gene_id_ in refused:
            # виден отдельной строкой: «уже ставили и откатывали» / «не ставить»
            if gene_id_ in rolled_back:
                why = "уже ставили и откатывали — ставить не хотим"
            elif gene_id_ in not_wanted:
                why = "помечен «не ставить»"
            else:
                why = "уже внедрён"
            refused_items.append(dict(base_item, why=why))
            continue
        if gene_id_ in taken:
            # уже внедрён этой копией — в чужие наработки не попадает
            # (он виден в «Внедрённые гены», оттуда его и откатывают)
            continue
        gene_files = [str(name) for name in (gene.get("files") or [])]
        same_files = sorted({name for name in gene_files if name in files})
        same_area = sorted({_area(name) for name in gene_files if _area(name)} & areas)
        if same_files:
            score = 2 * len(same_files)
            why = "те же файлы, что я сейчас правлю: %s" % ", ".join(same_files[:3])
        elif same_area:
            score = len(same_area)
            why = "та же часть программы: %s" % ", ".join(same_area[:3])
        else:
            score = 0
            why = "новая наработка другой копии (с моей работой не пересекается)"
        items.append(dict(base_item, score=score, why=why, fits=score > 0))
    items.sort(key=lambda item: (-int(item.get("score") or 0),
                                 -int(item.get("created_at") or 0)))
    refused_items.sort(key=lambda item: (-int(item.get("created_at") or 0)))
    try:
        import minigen_copy
        exchange = minigen_copy.exchange_state()
    except Exception as exc:  # noqa: BLE001
        exchange = {"available": False, "transport": "нет", "why": str(exc)}
    return {
        "suggestions": items,
        "fits": sum(1 for item in items if item["fits"]),
        "my_files": files,
        "transport": exchange.get("transport") or "нет",
        "available": bool(exchange.get("available")),
        "why": exchange.get("why") or "",
        # откатанные и помеченные «не ставить» — отдельной строкой
        "not_wanted": refused_items,
        "rolled_back": sum(1 for item in refused_items
                           if "откатывали" in str(item.get("why") or "")),
        # гены, которые эта копия внедрила (их можно откатить)
        "installed": installed_genes(),
    }


# ────────────────────────────────────────── ручной обмен (без сети)

def export_pool(path: str = "") -> dict[str, Any]:
    """Выгрузить наработки в папку, которую можно отнести соседней копии.

    Это промежуточный вариант для локального режима: сети нет, а наработки
    соседних копий всё же можно передать — вручную, на флешке.
    """
    source = pool_dir()
    if not source.is_dir():
        return {"error": "пула наработок нет — нечего выгружать"}
    target = Path(str(path).strip()) if str(path or "").strip() else None
    if target is None:
        try:
            import minigen_copy
            target = minigen_copy.EXPORT_DIR
        except Exception:  # noqa: BLE001
            target = Path("genepool-export")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"error": "не создать папку: %s" % exc}
    copied: list[str] = []
    for folder in (GENES_DIR, LINEAGE_DIR):
        base = source / folder
        if not base.is_dir():
            continue
        for item in base.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(source)
            dest = target / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
            except OSError as exc:
                return {"error": "не скопировать %s: %s" % (rel.name, exc)}
            copied.append(str(rel).replace("\\", "/"))
    data = lineage.instance()
    manifest = {
        "kind": "minigen-genepool-export",
        "version": 1,
        "created_at": _now(),
        "instance": str(data.get("instance_id") or ""),
        "files": len(copied),
    }
    try:
        (target / "export.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"error": "не записать описание выгрузки: %s" % exc}
    return {"ok": True, "path": str(target), "files": len(copied),
            "why": "наработки выгружены в папку — отнесите её соседней копии "
                   "(genes.import)"}


def import_pool(path: str) -> dict[str, Any]:
    """Внести наработки из выгруженной папки (обмен вручную)."""
    source = Path(str(path or "").strip())
    if not source.is_dir():
        return {"error": "нет такой папки: %s" % (source or "—")}
    added: list[str] = []
    skipped: list[str] = []
    genes_base = source / GENES_DIR
    if genes_base.is_dir():
        for manifest_path in sorted(genes_base.glob("*/*/manifest.json")):
            gene_dir = manifest_path.parent
            target = pool_dir() / GENES_DIR / gene_dir.parent.name / gene_dir.name
            if target.exists():
                skipped.append(gene_dir.name)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(gene_dir, target)
            except (OSError, shutil.Error) as exc:
                return {"error": "не внести ген %s: %s" % (gene_dir.name, exc)}
            added.append(gene_dir.name)
    journals = 0
    lineage_base = source / LINEAGE_DIR
    if lineage_base.is_dir():
        for journal in sorted(lineage_base.glob("*.jsonl")):
            target = pool_dir() / LINEAGE_DIR / journal.name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                existing = (target.read_text(encoding="utf-8")
                            if target.is_file() else "")
                lines = journal.read_text(encoding="utf-8", errors="replace").splitlines()
                with target.open("a", encoding="utf-8") as fh:
                    for line in lines:
                        line = line.strip()
                        if line and line not in existing:
                            fh.write(line + "\n")
                            journals += 1
            except OSError as exc:
                return {"error": "не внести летопись: %s" % exc}
    return {"ok": True, "added": added, "skipped": skipped, "events": journals,
            "why": "внесено наработок: %d (уже были: %d)"
                   % (len(added), len(skipped))}


def adopt(gene: str, title: str = "", allow_appearance: bool = False) -> dict[str, Any]:
    """Внедрить чужой ген: ветка в своём клоне → проверки → PR.

    Опасное действие: меняет программу ⇒ только после подтверждения владельца.
    Решение о слиянии — всё равно за человеком.

    ⚠️ **Ген, трогающий внешний вид, без явного решения владельца не
    внедряется** (промт КАРМАНА #187): по умолчанию установка гена вид
    программы не меняет, а если меняет — это видно заранее (пометка в
    списке и в подтверждении), а не сюрприз после внедрения.
    """
    ready, why = _pool_ready()
    if not ready:
        return {"error": why}
    payload = read_gene(gene)
    if payload.get("error"):
        return {"error": payload["error"]}
    patch = _read_patch_bytes(gene)
    if not patch.strip():
        return {"error": "ген без diff: %s" % gene}
    manifest = payload.get("manifest") or {}
    look = appearance_of(manifest)
    if look["touches"] and not allow_appearance:
        return {"ok": False,
                "error": "ген трогает внешний вид программы (%s) — без вашего "
                         "явного решения его не внедряю"
                         % ", ".join(look["files"][:5]),
                "needs_appearance_decision": True,
                "appearance_files": look["files"],
                "why": "ген меняет оформление — посмотрите diff и решите"}
    branch_title = title or ("внедрение гена %s: %s" % (gene, manifest.get("title") or gene))
    created = code.create_worktree(branch_title)
    if created.get("error"):
        return {"error": created["error"]}
    worktree = Path(created["worktree"])
    # патч — файлом (в git apply из stdin мы не умеем подсовывать данные)
    patch_file = worktree / ".gene.patch"
    patch_file.write_bytes(patch)
    applied = _git(["apply", "--3way", str(patch_file)], worktree, timeout=180)
    try:
        patch_file.unlink()
    except OSError:
        pass
    if not applied.get("ok"):
        code.discard_worktree()
        return {"error": "ген не применился к этой версии программы: %s"
                         % (applied.get("err") or applied.get("out"))}
    tests = code.run_tests(worktree=worktree)
    if not tests.get("ok"):
        return {"ok": False, "worktree": str(worktree),
                "error": "проверки красные — ген не идёт (worktree оставлен, "
                         "правку можно посмотреть)",
                "tests": tests}
    # ⚠️ Идентификатор гена — в сообщении коммита: по нему коммит гена
    # находится потом (в том числе для генов, взятых до появления отката).
    message = "feat(minigen): %s (ген %s)" % (manifest.get("title") or gene, gene)
    committed = code.commit(message)
    if committed.get("error"):
        return {"ok": False, "error": committed["error"], "worktree": str(worktree)}
    local_commit = str(committed.get("commit") or "")
    branch = str(committed.get("branch") or "")
    if _local_mode():
        # ⚠️ Локальный режим: PR без GitHub не открывается. Правка остаётся в
        # ветке локального репозитория — решение за владельцем, и он видит
        # всю правку в diff'е.
        lineage.adopt(gene, from_instance=str(manifest.get("author") or ""),
                      from_commit=str(manifest.get("base_commit") or ""),
                      note=str(manifest.get("title") or ""),
                      local_commit=local_commit, branch=branch)
        _append_pool_lineage(gene, "adopted")
        return {"ok": True, "gene": gene, "local_only": True,
                "branch": committed.get("branch") or "",
                "commit": local_commit,
                "worktree": str(worktree),
                "why": "ген применён в ветке «%s» локального репозитория: "
                       "локальный режим, PR не открывается — посмотрите правку "
                       "в diff'е и решите, что с ней делать"
                       % (committed.get("branch") or "—")}
    pr = code.open_pr("feat(minigen): %s" % (manifest.get("title") or gene),
                      "Ген %s от копии %s.\n\n%s"
                      % (gene, manifest.get("author") or "?", manifest.get("summary") or ""))
    if pr.get("error"):
        return {"ok": False, "error": pr["error"], "worktree": str(worktree)}
    lineage.adopt(gene, from_instance=str(manifest.get("author") or ""),
                  from_commit=str(manifest.get("base_commit") or ""),
                  note=str(manifest.get("title") or ""),
                  local_commit=local_commit, branch=branch)
    _append_pool_lineage(gene, "adopted")
    return {"ok": True, "gene": gene, "pr": pr.get("pr"), "branch": pr.get("branch"),
            "commit": local_commit,
            "why": "ген %s предложен PR'ом (решение за владельцем)" % gene}


# ─────────────────────────────────────────────── откат и пометка «не ставить»

def rollback(gene: str) -> dict[str, Any]:
    """Откатить внедрённый ген: вернуть состояние «как было».

    ⚠️ Откат — обычный `git revert` коммита гена в ветке, где ген стоит.
    После отката состояние программы совпадает с состоянием до внедрения
    (тот же список изменённых файлов и тот же diff) — критерий приёмки.

    Откат — тоже решение владельца: ген приносит чужой код, и «уже стоял —
    убрали» тоже должно быть его решением, а не тихой правкой.
    """
    gene = str(gene or "").strip()
    if not gene:
        return {"error": "не указан ген"}
    entry = lineage.gene_record(gene)
    if not entry:
        return {"ok": False,
                "error": "эта копия не внедряла ген %s — откатывать нечего" % gene}
    commit = str(entry.get("local_commit") or "")
    branch = str(entry.get("branch") or "")
    if not commit:
        # ген, взятый до появления отката: ищем коммит по слову в сообщении
        commit = code.find_commit_by_gene(gene)
    if not commit:
        return {"ok": False,
                "error": "не найден коммит гена %s в этой копии — откатывать нечем"
                         % gene}
    result = code.rollback_commit(commit, branch)
    if result.get("error"):
        return {"ok": False, "gene": gene, "error": result["error"]}
    revert = str(result.get("commit") or "")
    lineage.rolled_back(gene, commit=revert, branch=str(result.get("branch") or ""),
                        reason="откат по решению владельца")
    # ⚠️ Откатанный ген сразу помечается «не ставить» — чтобы он случайно
    # не предложился снова.
    lineage.dismiss(gene, reason="откатили — ставить не хотим")
    append_pool_event("rolled_back", gene=gene, commit=revert,
                      branch=str(result.get("branch") or ""))
    _push_pool_lineage("lineage: откат гена %s" % gene)
    return {"ok": True, "gene": gene, "commit": revert, "branch": result.get("branch"),
            "files": result.get("files") or [], "diff": result.get("diff") or "",
            "why": "ген %s откатан: состояние вернулось к тому, что было до "
                   "внедрения, и ген помечен «не ставить»" % gene}


def dismiss(gene: str, on: bool = True, reason: str = "") -> dict[str, Any]:
    """Пометить ген «не ставить» (или снять пометку).

    ⚠️ Это **личное** решение копии: в пул оно не уходит (в пул уходит
    «откатывали» — это полезно соседям, а «не хочу ставить» — нет).
    """
    gene = str(gene or "").strip()
    if not gene:
        return {"error": "не указан ген"}
    if on:
        lineage.dismiss(gene, reason=reason or "не хочу ставить")
    else:
        lineage.undismiss(gene)
    return {"ok": True, "gene": gene, "not_wanted": bool(on),
            "why": ("ген помечен «не ставить»" if on
                    else "пометка «не ставить» снята")}
