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
  коммит), а в пуле — свой файл `lineage/<копия>.jsonl` (по файлу на копию:
  общий журнал конфликтовал бы при слиянии);
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
    """Все гены пула (новые — первыми)."""
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
    return out


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


def suggestions() -> dict[str, Any]:
    """Чужие наработки, которые стоит посмотреть: с причиной совпадения.

    Кто прислал — видно, совпадает ли работа — тоже. Чужой ген никогда не
    попадает в программу молча: только через ветку и PR (решение — за
    владельцем).
    """
    data = lineage.instance()
    me = str(data.get("instance_id") or "")
    taken = {str(item.get("gene") or "") for item in (data.get("genes") or [])}
    files = my_files()
    areas = {_area(name) for name in files if _area(name)}
    items: list[dict[str, Any]] = []
    for gene in list_genes():
        gene_id_ = str(gene.get("id") or "")
        author = str(gene.get("author") or "")
        if not gene_id_ or gene_id_ in taken:
            continue
        if author and author == me:
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
        items.append({
            "id": gene_id_,
            "gene": gene_id_,
            "title": str(gene.get("title") or ""),
            "summary": str(gene.get("summary") or ""),
            "author": author,
            "created_at": int(gene.get("created_at") or 0),
            "files": gene_files,
            "score": score,
            "why": why,
            "fits": score > 0,
        })
    items.sort(key=lambda item: (-int(item.get("score") or 0),
                                 -int(item.get("created_at") or 0)))
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


def adopt(gene: str, title: str = "") -> dict[str, Any]:
    """Внедрить чужой ген: ветка в своём клоне → проверки → PR.

    Опасное действие: меняет программу ⇒ только после подтверждения владельца.
    Решение о слиянии — всё равно за человеком.
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
    message = "feat(minigen): %s" % (manifest.get("title") or gene)
    committed = code.commit(message)
    if committed.get("error"):
        return {"ok": False, "error": committed["error"], "worktree": str(worktree)}
    if _local_mode():
        # ⚠️ Локальный режим: PR без GitHub не открывается. Правка остаётся в
        # ветке локального репозитория — решение за владельцем, и он видит
        # всю правку в diff'е.
        lineage.adopt(gene, from_instance=str(manifest.get("author") or ""),
                      from_commit=str(manifest.get("base_commit") or ""),
                      note=str(manifest.get("title") or ""))
        _append_pool_lineage(gene, "adopted")
        return {"ok": True, "gene": gene, "local_only": True,
                "branch": committed.get("branch") or "",
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
                  note=str(manifest.get("title") or ""))
    _append_pool_lineage(gene, "adopted")
    return {"ok": True, "gene": gene, "pr": pr.get("pr"), "branch": pr.get("branch"),
            "why": "ген %s предложен PR'ом (решение за владельцем)" % gene}
