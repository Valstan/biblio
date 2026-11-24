"""Заводим агента «Идеи»: репозитории, конституция, скиллы, область.

Запуск: python tools/minimax-project-areas/make_ideas_agent.py
(одноразовая процедура по слову владельца; правило — тот же код программы).
"""
from __future__ import annotations

import json
import pathlib
import sys

BRIDGE = pathlib.Path(__file__).resolve().parent.parent / "minigen-gui" / "bridge"
sys.path.insert(0, str(BRIDGE))

import minigen_agents as agents   # noqa: E402
import minigen_bridge as bridge   # noqa: E402


class FakeCore:
    """Мини-ядро для `cmd_create`: нужен только BIN и log."""

    BIN = pathlib.Path(r"C:\Users\valstan\bin")

    def log(self, message: str) -> None:
        print("   [журнал] %s" % message)


def main() -> int:
    slug = "ideas"
    area = "p-ideas"
    print("1) агент: репозиторий, конституция, скиллы, манифест")
    created = agents.create_agent(
        slug,
        "Идеи",
        "обработка зачиток владельца в заявки для других нейросетей",
        repo_slug="Valstan/ideas",
        area=area,
    )
    if created.get("error"):
        print("   ОШИБКА: %s" % created["error"])
        return 1
    print(json.dumps({k: created.get(k) for k in
                      ("slug", "title", "repo", "repo_path", "files", "skills",
                       "committed", "registry_committed", "error")},
                     ensure_ascii=False, indent=1))

    print("2) рабочая область")
    fake = bridge.Bridge.__new__(bridge.Bridge)
    fake.core = FakeCore()
    area_dir = bridge.state.AREAS_ROOT / ("p-" + slug)
    made = fake.cmd_create({
        "slug": slug,
        "title": "Идеи",
        "folder": created.get("repo_path") or "",
        "karman": slug,
        # каталог мог остаться от неудачного копирования шаблона —
        # тогда область «доносится», а не заводится заново
        "resume": area_dir.exists(),
    })
    print("   " + json.dumps(made, ensure_ascii=False)[:700])
    if made.get("error"):
        print("   ОШИБКА области: %s" % made["error"])

    print("3) скиллы в область")
    installed = agents.install_skills(slug, pathlib.Path(created["repo_path"]))
    print("   " + json.dumps(installed, ensure_ascii=False))

    print("4) что получилось")
    print("   репозиторий агента: %s" % created.get("repo_path"))
    print("   область: %s" % area_dir)
    print("   манифест: %s" % created.get("manifest_path"))
    for item in agents.list_agents():
        print("   агент в реестре: %s — %s (%s)" % (
            item.get("slug"), item.get("title"), item.get("repo")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
