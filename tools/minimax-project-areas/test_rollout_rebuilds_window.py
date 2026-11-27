# -*- coding: utf-8 -*-
"""Приёмка: раскатка не должна ставить окно старше раскатываемого кода.

Живой случай (27.11): в master залили правки, окно **не пересобрали**, и раскатка
прошла «успешно» — а окно осталось на старой сборке (метка `0d6094f` при master
`f8d07b3`). Владелец видел «свежую» программу, а в окне не было новой кнопки.
Перезапуск окна не помогал: перезапускался тот же старый бандл.

Теперь правило **в самой раскатке**: бандл окна должен быть собран из кода, в
котором есть раскатываемый коммит. Не содержит ⇒ пересобрать; пересобрать не
из чего ⇒ остановиться, **не раскатывая** (молчаливый старый бандл — ровно тот
дефект, который чиним).

Проверяем на **настоящем** временном репозитории (предок, а не равенство):

1. окно собрано из коммита, где master есть в истории → **готово**, сборка не нужна;
2. окно собрано из старого коммита (master'а в его истории нет) → **пересборка**;
3. пересборка не помогла (метка всё та же) → **отказ**, раскатка не идёт;
4. в исходниках есть незакоммиченные правки → **отказ**, чужую работу не затираем;
5. `--no-program` — проверка окна пропускается (катай только демонов);
6. в самой раскатке проверка стоит **до** установки программы.

Запуск: python3 tools/minimax-project-areas/test_rollout_rebuilds_window.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load_rollout():
    spec = importlib.util.spec_from_file_location(
        "rollout_fresh_bundle_under_test", str(HERE / "minigen_rollout.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo: pathlib.Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t.local",
         "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(args),
        capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), res.stderr))
    return (res.stdout or "").strip()


def make_repo(root: pathlib.Path) -> dict[str, str]:
    """Репозиторий с историей: base ← master, плюс «старый» и «новый» коммиты.

    * `base`   — предок master'а (окно, собранное из него, **старше** master'а);
    * `master` — то, что раскатывается;
    * `tip`    — потомок master'а (окно, собранное из него, **свежее** master'а).
    """
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")

    hashes: dict[str, str] = {}

    def commit(name: str) -> None:
        # ⚠️ `git commit -q` **не печатает** хеш ⇒ берём его отдельным rev-parse
        git(repo, "commit", "-q", "--allow-empty", "-m", name)
        hashes[name] = git(repo, "rev-parse", "HEAD")

    commit("base")
    commit("master")
    commit("tip")
    return hashes


def write_label(gui: pathlib.Path, commit: str) -> None:
    """Метка сборки рядом с бандлом — как её пишет electron-vite."""
    out = gui / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "build-info.json").write_text(
        json.dumps({"commit": commit[:7], "branch": "master",
                    "built_at": "2026-11-27T00:00:00.000Z"}), encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    rollout = load_rollout()

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        commits = make_repo(root)
        gui = root / "gui"
        gui.mkdir()
        (gui / "node_modules").mkdir()          # сборке нужны зависимости

        # раскатка едет из origin/master — указываем на наш master
        rollout.REPO = root / "repo"
        rollout.PROGRAM_SRC = gui
        rollout.target_hash = lambda ref="origin/master": commits["master"]
        # метка сборки — короткий хеш ⇒ разворачиваем в полный
        by_short = {full[:7]: full for full in commits.values()}
        rollout.resolve_hash = lambda rev: by_short.get((rev or "")[:7], "")
        rollout.head_hash = lambda: commits["tip"]
        rollout.tracked_dirty = lambda: False
        rollout.build_window = lambda: (write_label(gui, commits["tip"]), (True, ""))[1]

        print("1) окно из кода, где master есть в истории — сборка не нужна")
        write_label(gui, commits["tip"])
        built = []
        rollout.build_window = lambda: (built.append(1), (True, ""))[1]
        ready, why = rollout.ensure_fresh_bundle()
        check(ready, "окно из свежего кода отвергнуто: %s" % why)
        check(not built, "окно свежее, а пересборка всё равно пошла")

        print("2) окно из старого кода (master'а в истории нет) — нужна пересборка")
        write_label(gui, commits["base"])
        built = []
        rollout.build_window = lambda: (built.append(1), write_label(gui, commits["tip"]),
                                        (True, ""))[2]
        ready, why = rollout.ensure_fresh_bundle()
        check(ready, "после пересборки окно всё ещё не годное: %s" % why)
        check(len(built) == 1, "пересборка не случилась (звонов: %d)" % len(built))

        print("3) пересборка не помогла — раскатка обязана остановиться")
        write_label(gui, commits["base"])
        rollout.build_window = lambda: (True, "")
        ready, why = rollout.ensure_fresh_bundle()
        check(not ready, "окно осталось старым, а раскатка сказала «готово»")
        check("стар" in why.lower(), "причина не сказана прямо: %s" % why)

        print("4) в исходниках есть незакоммиченные правки — чужую работу не затираем")
        write_label(gui, commits["base"])
        rollout.tracked_dirty = lambda: True
        built = []
        rollout.build_window = lambda: (built.append(1), (True, ""))[1]
        ready, why = rollout.ensure_fresh_bundle()
        check(not ready, "при незакоммиченных правках раскатка пошла дальше")
        check(not built, "сборка запущена при незакоммиченных правках")
        check("незакоммиченные" in why, "про правки не сказано прямо: %s" % why)
        rollout.tracked_dirty = lambda: False

        print("5) в дереве нет раскатываемого кода — окно собрать не из чего")
        write_label(gui, commits["base"])
        rollout.head_hash = lambda: commits["base"]
        ready, why = rollout.ensure_fresh_bundle()
        check(not ready, "дерево без раскатываемого кода, а раскатка пошла дальше")
        rollout.head_hash = lambda: commits["tip"]

        print("6) --no-program: окно не трогаем — проверка пропускается")
        src = (HERE / "minigen_rollout.py").read_text(encoding="utf-8")
        check('if "--no-program" not in sys.argv:' in src,
              "проверка окна не связана с --no-program")
        body = src[src.find("def main("):]
        check("ensure_fresh_bundle(ref)" in body,
              "раскатка не зовёт проверку свежести сборки")
        # проверка обязана стоять ДО установки программы
        fresh_at = body.find("ensure_fresh_bundle(ref)")
        install_at = body.find("install_program()")
        check(fresh_at > 0 and install_at > 0 and fresh_at < install_at,
              "проверка окна идёт после установки программы (а должна до)")

        print("7) проверка идёт до «--- файлы ---»: машину не трогаем зря")
        files_at = body.find('print("--- файлы ---")')
        check(fresh_at < files_at,
              "раскатка успела что-то записать до проверки окна")

    if failures:
        print("❌ приёмка пересборки окна перед раскаткой: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка пересборки окна перед раскаткой: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
