# -*- coding: utf-8 -*-
r"""Приёмка: раскатка везёт конфиг областей и **не откатывает** живое состояние.

Зачем (живой случай 27.11): конфиг областей (`mcode-project-areas.json`) пишет
**сама программа** — владелец переключает «Автомат / Вручную» и выбирает
водителя проекта прямо в панели «МиниГен». Файл не был в списке раскатки ⇒
репозиторий (эталон) разошёлся с машиной молча: четыре области, заведённые на
машине, в репозиторий не попали, а правка поля «Раздел КАРМАНА» (G732) не
доехала.

Теперь файл в списке раскатки, и раскатка его **сливает**, а не копирует поверх:

- **структура** (какие области есть, названия, папки, разделы) — из репозитория:
  это эталон версии, и он едет на машину;
- **живое состояние** (`autoheal`, `driver`) — с машины: это выбор владельца,
  и раскатка не должна молча его отменять (припаркованный проект, включённый
  в автолечение, — лишние очки и лишние перезапуски);
- области, заведённые на машине и **не** в репозитории, — не удаляются.

Запуск: python3 tools/minimax-project-areas/test_rollout_areas_config_merge.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def load_rollout():
    spec = importlib.util.spec_from_file_location(
        "rollout_areas_merge_under_test", str(HERE / "minigen_rollout.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cfg(areas: list[dict]) -> bytes:
    return json.dumps({"note": "n", "projects": areas}, ensure_ascii=False,
                      indent=2).encode("utf-8")


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    rollout = load_rollout()

    print("1) структура — из репозитория, живое состояние — с машины")
    repo = cfg([{"slug": "a", "title": "А", "folder": "F", "karman": "A",
                  "autoheal": True, "driver": "none"}])
    live = cfg([{"slug": "a", "title": "А (старое)", "folder": "F",
                 "karman": "A", "autoheal": False, "driver": "lead"}])
    out, report = rollout.merge_areas_config(repo, live)
    areas = json.loads(out.decode("utf-8"))["projects"]
    check(len(areas) == 1, "область пропала: %s" % areas)
    check(areas[0].get("title") == "А",
          "название взято с машины, а не из репозитория: %s" % areas[0].get("title"))
    check(areas[0].get("autoheal") is False,
          "флаг «Автомат/Вручную» откачен — решение владельца затёрто")
    check(areas[0].get("driver") == "lead",
          "водитель проекта откачен — решение владельца затёрто")

    print("2) область, которой нет на машине, — добавляется из репозитория")
    repo = cfg([{"slug": "a", "karman": "A", "autoheal": True},
                {"slug": "new", "karman": "New", "autoheal": True}])
    live = cfg([{"slug": "a", "karman": "A", "autoheal": True}])
    out, report = rollout.merge_areas_config(repo, live)
    slugs = [a.get("slug") for a in json.loads(out.decode("utf-8"))["projects"]]
    check("new" in slugs, "новая область из репозитория не доехала: %s" % slugs)
    check(report.get("areas") == 2, "в отчёте не сказано, сколько областей: %s" % report)

    print("3) область, заведённая на машине, — не удаляется")
    repo = cfg([{"slug": "a", "karman": "A", "autoheal": True}])
    live = cfg([{"slug": "a", "karman": "A", "autoheal": True},
                {"slug": "live-only", "karman": "L", "autoheal": False}])
    out, report = rollout.merge_areas_config(repo, live)
    slugs = [a.get("slug") for a in json.loads(out.decode("utf-8"))["projects"]]
    check("live-only" in slugs, "область с машины удалена: %s" % slugs)
    check(report.get("live_only") == ["live-only"],
          "в отчёте не сказано про область, которой нет в репозитории: %s" % report)

    print("4) битый или пустой живой файл — раскатка берёт репозиторий и не падает")
    repo = cfg([{"slug": "a", "karman": "A", "autoheal": True}])
    for broken in (b"", "{не json".encode("utf-8"), b"[]"):
        out, report = rollout.merge_areas_config(repo, broken)
        areas = json.loads(out.decode("utf-8"))["projects"]
        check(len(areas) == 1,
              "битый живой файл (%r) оставил без областей" % broken[:12])

    print("5) битый файл из репозитория — раскатка его не трогает")
    broken_repo = "{не json".encode("utf-8")
    out, report = rollout.merge_areas_config(broken_repo, live)
    check(out == broken_repo, "испорченный конфиг из репозитория переписан")
    check(report.get("error"), "причина не сказана: %s" % report)

    print("6) переводы строк — как на машине (иначе файл «прыгает»)")
    repo = cfg([{"slug": "a", "karman": "A", "autoheal": True}])
    live_crlf = cfg([{"slug": "a", "karman": "A", "autoheal": False}
                     ]).replace(b"\n", b"\r\n")
    out, _report = rollout.merge_areas_config(repo, live_crlf)
    check(b"\r\n" in out, "переводы строк на машине (CRLF) не сохранены")
    check(b"\n" not in out.replace(b"\r\n", b""),
          "в файле остались голые LF — файл будет «прыгать» при каждой раскатке")
    out_lf, _report = rollout.merge_areas_config(repo, cfg([{"slug": "a"}]))
    check(b"\r\n" not in out_lf,
          "в файл с LF попали CRLF — на машине формат поменяется")

    print("7) структурные поля — по репозиторию, но о замене сказано")
    repo = cfg([{"slug": "a", "title": "А", "folder": "F2", "karman": "A",
                  "autoheal": True}])
    live = cfg([{"slug": "a", "title": "А", "folder": "F1", "karman": "A",
                 "autoheal": True}])
    out, report = rollout.merge_areas_config(repo, live)
    areas = json.loads(out.decode("utf-8"))["projects"]
    check(areas[0].get("folder") == "F2",
          "структурное поле взято с машины, а не из репозитория: %s" % areas[0])
    check(any("folder" in line for line in report.get("changed") or []),
          "о замене папки не сказано — молча откатили бы чужую работу: %s" % report)

    print("8) конфиг в списке раскатки, и он в репозитории есть")
    files = rollout.FILES
    check("mcode-project-areas.json" in files,
          "конфиг областей не в списке раскатки — правки не доедут")
    check((HERE / "mcode-project-areas.json").is_file(),
          "конфига областей нет в репозитории")
    src = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
    check("merge_areas_config" in src, "раскатка не сливает конфиг с машиной")
    check("AREAS_CONFIG" in src, "раскатка не знает, какой файл сливать")

    if failures:
        print("❌ приёмка слияния конфига областей: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка слияния конфига областей: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
