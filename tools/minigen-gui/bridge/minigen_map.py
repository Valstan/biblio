# -*- coding: utf-8 -*-
"""Карта родословной: **паутина копий и генов** (заказ владельца 24.11).

Заказ: «карта родословной, прямо в программе отдельная вкладка "Гены", где
будет рисоваться вся карта всех версий Минигена, которые существуют в
интернете: кто связан друг с другом по цепочке, в какой точке родилась эта
копия, сколько и откуда она приняла генов, сколько её наработки позаимствовали,
кто у неё брал те же гены или переработанные».

**Откуда данные.** Всё уже копится в **пуле наработок** (общий репозиторий
клонов генов):

- `lineage/<копия>.jsonl` — летопись копии: `published` (создала ген),
  `retracted` (отозвала ген), `adopted` (внедрила чужой ген), `born` (родилась);
- `genes/<копия>/<ген>/manifest.json` — кто автор, от какой версии (`base_commit`),
  что за наработка.

Плюс локальная родословная (`instance.json`): от какой версии форк, кто
родитель по гену. Поэтому карта **общая**: каждая копия дописывает в пул свои
связи (`born` при заведении копии, `published`/`adopted` — при работе с генами),
а окно собирает карту из пула.

**Чего карта не знает** (и не выдумывает): ген, принятый копией, — это связь
«взяла», а не «переродился». Перерождение (`gene-…-001` отозван, `gene-…-002`
взамен) читается из записи `retracted`, где в поле `why` сказано, чем он
заменён.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"
POOL_DIR = LOCAL / "genepool"
GENES_DIR = "genes"
LINEAGE_DIR = "lineage"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pool_events() -> list[dict[str, Any]]:
    """Все события пула (из летописи всех копий), новые — первыми."""
    out: list[dict[str, Any]] = []
    directory = POOL_DIR / LINEAGE_DIR
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
            if isinstance(record, dict) and record.get("event"):
                record["source_file"] = path.name
                out.append(record)
    out.sort(key=lambda item: int(item.get("ts") or 0))
    return out


def pool_manifests() -> dict[str, dict[str, Any]]:
    """Манифесты генов пула: ген → манифест."""
    out: dict[str, dict[str, Any]] = {}
    base = POOL_DIR / GENES_DIR
    if not base.is_dir():
        return out
    for path in base.glob("*/*/manifest.json"):
        data = _read_json(path)
        gene = str(data.get("id") or path.parent.name)
        if gene:
            out[gene] = data
    return out


def _local_lineage() -> dict[str, Any]:
    """Родословная этой копии (из instance.json)."""
    data = _read_json(LOCAL / "instance.json")
    return {
        "instance": data.get("instance_id") or "",
        "family": data.get("family") or "minigen",
        "generation": int(data.get("generation") or 1),
        "born_at": int(data.get("birth") or 0),
        "fork_commit": str((data.get("fork_of") or {}).get("commit") or ""),
        "fork_installed_at": int((data.get("fork_of") or {}).get("installed_at") or 0),
        "parent": data.get("parent") or {},
        "genes": list(data.get("genes") or []),
        "repo": data.get("copy_repo") or "",
        "upstream": data.get("upstream") or "",
    }


def _author_from_id(gene_id: str) -> str:
    """Автор гена по его идентификатору: `gene-<копия>-<номер>` → `<копия>`.

    ⚠️ У отозванного гена манифеста может не остаться (публикация не
    доехала) — но по имени гена копию-автора всё равно видно.
    """
    text = str(gene_id or "")
    if not text.startswith("gene-"):
        return ""
    tail = text[len("gene-"):]
    match = re.match(r"^(.+)-\d+$", tail)
    return match.group(1) if match else tail


def build() -> dict[str, Any]:
    """Собрать карту: копии, гены, связи между ними."""
    events = pool_events()
    manifests = pool_manifests()
    self_lineage = _local_lineage()

    # ── копии ────────────────────────────────────────────────────────────────
    copies: dict[str, dict[str, Any]] = {}

    def copy_of(instance: str) -> dict[str, Any]:
        ident = instance or "unknown"
        if ident not in copies:
            copies[ident] = {
                "instance": ident,
                "generation": 0,
                "born_at": 0,
                "fork_commit": "",
                "parent": {},
                "genes_made": [],
                "genes_taken": [],
                "taken_by": [],
                "repo": "",
                "upstream": "",
                "is_self": False,
            }
        return copies[ident]

    # 1) из летописи пула
    for record in events:
        instance = str(record.get("instance") or "")
        if not instance:
            continue
        entry = copy_of(instance)
        event = str(record.get("event") or "")
        gene = str(record.get("gene") or "")
        if event == "published" and gene:
            if gene not in entry["genes_made"]:
                entry["genes_made"].append(gene)
        elif event == "adopted" and gene:
            if gene not in entry["genes_taken"]:
                entry["genes_taken"].append(gene)
            author = str(manifests.get(gene, {}).get("author") or "")
            if author and author != instance:
                author_entry = copy_of(author)
                if instance not in author_entry["taken_by"]:
                    author_entry["taken_by"].append(instance)
        elif event == "born":
            entry["born_at"] = int(record.get("ts") or entry["born_at"])
            entry["fork_commit"] = str(record.get("fork_commit") or entry["fork_commit"])

    # 2) локальная родословная — дополняет (свою копию знаем точно)
    if self_lineage["instance"]:
        entry = copy_of(self_lineage["instance"])
        entry["is_self"] = True
        entry["generation"] = self_lineage["generation"]
        entry["born_at"] = self_lineage["born_at"] or entry["born_at"]
        entry["fork_commit"] = self_lineage["fork_commit"] or entry["fork_commit"]
        entry["parent"] = self_lineage["parent"]
        entry["repo"] = self_lineage["repo"]
        entry["upstream"] = self_lineage["upstream"]
        for item in self_lineage["genes"]:
            gene = str(item.get("gene") or "")
            if gene and gene not in entry["genes_taken"]:
                entry["genes_taken"].append(gene)
            source = str(item.get("from") or "")
            if source and source != entry["instance"]:
                source_entry = copy_of(source)
                if entry["instance"] not in source_entry["taken_by"]:
                    source_entry["taken_by"].append(entry["instance"])

    # поколение копии, у которой нет события «born», — считаем по цепочке
    for entry in copies.values():
        if not entry["generation"]:
            entry["generation"] = 1

    # ── гены ─────────────────────────────────────────────────────────────────
    genes: dict[str, dict[str, Any]] = {}

    def gene_of(gene_id: str) -> dict[str, Any]:
        ident = gene_id or "unknown"
        if ident not in genes:
            manifest = manifests.get(ident, {})
            genes[ident] = {
                "id": ident,
                "author": str(manifest.get("author") or _author_from_id(ident)),
                "title": str(manifest.get("title") or ""),
                "summary": str(manifest.get("summary") or ""),
                "base_commit": str(manifest.get("base_commit") or ""),
                "created_at": int(manifest.get("created_at") or 0),
                "state": "published",
                "taken_by": [],
                "replaced_by": "",
                "why_retracted": "",
            }
        return genes[ident]

    for gene_id, manifest in manifests.items():
        entry = gene_of(gene_id)
        entry["title"] = entry["title"] or str(manifest.get("title") or "")
        entry["summary"] = entry["summary"] or str(manifest.get("summary") or "")
        entry["author"] = entry["author"] or str(manifest.get("author") or "")
        entry["base_commit"] = entry["base_commit"] or str(manifest.get("base_commit") or "")
        entry["created_at"] = entry["created_at"] or int(manifest.get("created_at") or 0)

    for record in events:
        gene = str(record.get("gene") or "")
        if not gene:
            continue
        entry = gene_of(gene)
        event = str(record.get("event") or "")
        if event == "published":
            entry["state"] = "published"
        elif event == "retracted":
            entry["state"] = "retracted"
            entry["why_retracted"] = str(record.get("why") or "")
            replaced = str(record.get("replaced_by") or "")
            if replaced:
                entry["replaced_by"] = replaced
                gene_of(replaced)["state"] = "published"
        elif event == "adopted":
            entry["state"] = "adopted"
            instance = str(record.get("instance") or "")
            if instance and instance not in entry["taken_by"]:
                entry["taken_by"].append(instance)

    # ── связи ────────────────────────────────────────────────────────────────
    edges: list[dict[str, Any]] = []

    def edge(source: str, target: str, kind: str) -> None:
        if not source or not target:
            return
        item = {"from": source, "to": target, "kind": kind}
        if item not in edges:
            edges.append(item)

    for entry in copies.values():
        for gene in entry["genes_made"]:
            edge(entry["instance"], gene, "made")
        for gene in entry["genes_taken"]:
            edge(gene, entry["instance"], "took")
        parent = entry.get("parent") or {}
        parent_instance = str(parent.get("instance") or "")
        if parent_instance and parent_instance != entry["instance"]:
            # копия родилась от другой копии
            edge(parent_instance, entry["instance"], "born")
        elif entry["fork_commit"]:
            # копия родилась от версии программы (корень паутины)
            edge(entry["fork_commit"], entry["instance"], "born")
    for entry in genes.values():
        if entry["replaced_by"]:
            edge(entry["id"], entry["replaced_by"], "replaced")

    copies_list = sorted(copies.values(),
                         key=lambda item: (item["generation"], item["born_at"], item["instance"]))
    genes_list = sorted(genes.values(),
                        key=lambda item: (item["created_at"], item["id"]))

    return {
        "self": {
            "instance": self_lineage["instance"],
            "generation": self_lineage["generation"],
            "born_at": self_lineage["born_at"],
            "fork_commit": self_lineage["fork_commit"],
            "parent": self_lineage["parent"],
            "repo": self_lineage["repo"],
            "upstream": self_lineage["upstream"],
        },
        "copies": copies_list,
        "genes": genes_list,
        "edges": edges,
        "stats": {
            "copies": len(copies_list),
            "genes": len(genes_list),
            "links": len(edges),
            "events": len(events),
            "made": sum(len(item["genes_made"]) for item in copies_list),
            "took": sum(len(item["genes_taken"]) for item in copies_list),
        },
        "pool": str(POOL_DIR),
        "pool_ready": (POOL_DIR / ".git").is_dir(),
        "note": ("карта собрана из пула наработок: каждая копия дописывает в пул свои "
                 "связи (рождение, публикация гена, внедрение чужого гена)"),
    }
