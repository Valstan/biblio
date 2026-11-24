# -*- coding: utf-8 -*-
"""Приёмка: «версия на месте» описывает установленный бандл, а не ветку.

Живой случай 24.11 (жалоба владельца): «окно старое, на диске новая версия,
перезапусти окно» — я перезапускал, и оно **снова** писало то же самое.

Причина была в раскатке: `version.json` записывался из коммита **рабочего
дерева** (`git rev-parse HEAD`), а окно несло метку **сборки**, которая лежит
на диске. Стоило закатить одни документы (без пересборки) — «версия на месте»
уезжала вперёд, а бандл оставался прежним ⇒ перезапуск перезапускал тот же
бандл, и расхождение не исчезало никогда.

Теперь: сборка кладёт свою метку рядом с бандлом (`out/build-info.json`), а
раскатка берёт версию **из бандла**. Расхождение невозможно по построению.

Запуск: python3 tools/minimax-project-areas/test_rollout_version_from_build.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load_rollout():
    spec = importlib.util.spec_from_file_location(
        "rollout_version_under_test", str(HERE / "minigen_rollout.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    rollout = load_rollout()

    print("1) версия берётся из метки сборки")
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "gui"
        (src / "out").mkdir(parents=True)
        (src / "out" / "build-info.json").write_text(
            json.dumps({"commit": "abc1234", "branch": "master"}), encoding="utf-8")
        check(rollout.built_commit(src) == "abc1234",
              "метка сборки не прочитана: %s" % rollout.built_commit(src))

    print("2) без метки — пусто, а не выдумка")
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "gui"
        (src / "out").mkdir(parents=True)
        check(rollout.built_commit(src) == "",
              "без метки сборки взято что-то: %s" % rollout.built_commit(src))

    print("3) битая метка не роняет раскатку")
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "gui"
        (src / "out").mkdir(parents=True)
        (src / "out" / "build-info.json").write_text("{не json", encoding="utf-8")
        check(rollout.built_commit(src) == "",
              "битая метка не распознана: %s" % rollout.built_commit(src))

    print("4) раскатка пишет версию из бандла, а не из HEAD")
    src = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
    check("built_commit()" in src, "раскатка не зовёт built_commit()")
    body = src[src.find("def install_program"):]
    check("built_commit()" in body,
          "версия программы по-прежнему берётся не из бандла")
    check("rev-parse" in body, "запасной путь (коммит рабочего дерева) пропал")

    print("5) сборка кладёт метку рядом с бандлом")
    config = (HERE.parent / "minigen-gui" / "electron.vite.config.ts").read_text(
        encoding="utf-8")
    check("build-info.json" in config,
          "сборка не пишет метку сборки (out/build-info.json)")
    check("closeBundle" in config, "в сборке нет шага, который пишет метку")

    if failures:
        print("❌ приёмка версии по бандлу: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка версии по бандлу: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
