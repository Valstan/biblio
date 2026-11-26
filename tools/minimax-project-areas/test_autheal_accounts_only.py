# -*- coding: utf-8 -*-
"""Приёмка: автомат аккаунтов не пишет в сессии ни одного сообщения.

Промт КАРМАНА #174 (зачитка владельца 26.11, 19:00): «автомат» был одним
большим механизмом — он и следил за аккаунтами (перелогин, свежие очки), и
вмешивался в сессии (продлевал, завершал, архивировал, поднимал новые).
Из-за этого чужую сессию «оживляли» посреди работы.

После разделения:
- **автомат аккаунтов** (`minimax-autheal.py`) — перелогинивает агента, даёт
  свежий аккаунт MiniMax со свежими очками, следит за «очки кончились»;
  **в сессии не пишет ни одного сообщения** и не создаёт, не закрывает, не
  архивирует сессии;
- **автомат управления сессиями** (`minimax-sessionwatch.py`) — только он
  умеет продлевать, завершать, архивировать и поднимать новые сессии.

Проверяем:

1. **смена аккаунта — ноль сообщений в сессии**: зовём `renew()` с подменёнными
   шагами и ловим **любую** попытку запустить что-либо в области (сообщение в
   сессию — это запуск `mcode` с текстом);
2. **решение о жизни сессии в автомате аккаунтов не принимается**: в коде
   лечащего нет ни отправки в сессию, ни команд закрытия, ни «Продолжай»;
3. **аккаунт при этом меняется**: состояние лечащего помнит смену аккаунта —
   по нему сторож сессий и понимает, что аккаунт обновлён (и продолжает нитку
   сам, ровно один раз на смену аккаунта).

Запуск: python tools/minimax-project-areas/test_autheal_accounts_only.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load_healer():
    spec = importlib.util.spec_from_file_location(
        "minimax_autheal_174_under_test", str(HERE / "minimax-autheal.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeProc:
    """Процесс, который «идёт»."""

    pid = 4242

    def poll(self):
        return None


def renew_without_session_writes(mod, area_dir: pathlib.Path) -> tuple:
    """Смена аккаунта с подменёнными шагами; ловим всё, что уходит в область.

    Возвращает (результат renew, список запущенных команд).
    """
    captured: list[list] = []

    def fake_popen(cmd, **kwargs):
        captured.append(list(cmd))
        return FakeProc()

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return FakeProc()

    class FakeSubprocess:
        DEVNULL = -3
        CREATE_NO_WINDOW = 0x08000000
        Popen = staticmethod(fake_popen)
        run = staticmethod(fake_run)

    old_subprocess = mod.subprocess
    old_pull = mod.pull_account
    old_bonus = mod.claim_bonus
    old_login_lost = mod.login_lost
    old_generation = mod.store_generation
    old_running = mod.desktop_running
    old_log = mod.log
    old_state_file = mod.STATE_FILE
    mod.subprocess = FakeSubprocess
    mod.pull_account = lambda switch, area: (0, "аккаунт получен (тест)")
    mod.claim_bonus = lambda switch, adir: "бонус активирован"
    mod.login_lost = lambda adir: ""
    mod.store_generation = lambda adir: 7
    mod.desktop_running = lambda slug: False
    mod.log = lambda msg: None
    # Файл состояния — во временный каталог (настоящий `save_cooldowns` пишет
    # туда, куда указывает `STATE_FILE`).
    mod.STATE_FILE = area_dir / "autheal-state.json"
    try:
        area = {"slug": "test-area", "title": "Тестовая область",
                "dir": area_dir, "folder": str(area_dir)}
        result = mod.renew(object(), area)
        state = json.loads((area_dir / "autheal-state.json").read_text(encoding="utf-8"))
    finally:
        mod.subprocess = old_subprocess
        mod.pull_account = old_pull
        mod.claim_bonus = old_bonus
        mod.login_lost = old_login_lost
        mod.store_generation = old_generation
        mod.desktop_running = old_running
        mod.log = old_log
        mod.STATE_FILE = old_state_file
    return result, captured, state


#: Куски кода, которыми автомат аккаунтов пишет в сессию. Ни один из них не
#: имеет права остаться в лечащем: жизнью сессии занимается сторож сессий.
SESSION_WRITE_MARKERS = (
    "send_to_session",
    "say_continue",
    "_continue_once",
    "rotate_session",
    "reset_session",
    "archive_session",
    "new_session",
    "wait_closed",
)

#: Строки-команды, которые пишут в сессию.
SESSION_WRITE_TEXTS = ("--continue", "Продолжай нитку", "Завершай работу в этой сессии")


def code_calls_and_texts(source: str) -> tuple[list[str], list[str]]:
    """Имена вызываемых функций и строковые литералы — **без** комментариев и
    документации.

    Разбор по дереву, а не по тексту: иначе упоминание «send_to_session» в
    докстринге выдавало бы за ошибку (или, наоборот, пропускало бы вызов,
    спрятанный за алиасом).
    """
    tree = ast.parse(source.lstrip("﻿"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    calls: list[str] = []
    texts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.append(func.id)
            elif isinstance(func, ast.Attribute):
                calls.append(func.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docs:
                continue
            texts.append(node.value)
    return calls, texts


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("1) смена аккаунта — ноль сообщений в сессии")
    mod = load_healer()
    with tempfile.TemporaryDirectory() as tmp:
        area_dir = pathlib.Path(tmp) / "p-test"
        area_dir.mkdir(parents=True, exist_ok=True)
        result, commands, state = renew_without_session_writes(mod, area_dir)
    ok, why = result
    check(bool(ok), "смена аккаунта не удалась: %s" % why)
    check(not commands,
          "автомат аккаунтов что-то запускал в области: %s" % commands)
    for cmd in commands:
        joined = " ".join(str(part) for part in cmd)
        check("Продолжай" not in joined,
              "в область ушло сообщение «Продолжай»: %s" % joined)

    print("2) решение о жизни сессии в автомате аккаунтов не принимается")
    source = (HERE / "minimax-autheal.py").read_text(encoding="utf-8")
    calls, texts = code_calls_and_texts(source)
    for marker in SESSION_WRITE_MARKERS:
        check(marker not in calls,
              "в лечащем остался вызов, который пишет в сессию: %s" % marker)
    for needle in SESSION_WRITE_TEXTS:
        hits = [text for text in texts if needle in text]
        check(not hits,
              "в лечащем осталась команда в сессию «%s»: %s" % (needle, hits[:2]))

    print("3) аккаунт обновлён — и это видно сторожу сессий")
    with tempfile.TemporaryDirectory() as tmp:
        area_dir = pathlib.Path(tmp) / "p-test"
        area_dir.mkdir(parents=True, exist_ok=True)
        _result, _cmds, state = renew_without_session_writes(mod, area_dir)
    last_heal = (state.get("last_heal") or {}).get("test-area")
    check(bool(last_heal),
          "состояние лечащего не помнит смену аккаунта: %s" % state.get("last_heal"))
    # Сторож сессий читает это состояние по пути `core.AUTHEAL_STATE`.
    state_path = re.search(r"^STATE_FILE\s*=.*?([\"'])(.+?)\1\s*$",
                            (HERE / "minimax-autheal.py").read_text(encoding="utf-8"),
                            re.M | re.S)
    check(state_path is not None, "не нашли путь файла состояния лечащего")
    if state_path:
        check("autheal-state.json" in state_path.group(2),
              "файл состояния лечащего не тот: %s" % state_path.group(2))

    print("4) в лечащем нет решения о жизни сессии (только аккаунты)")
    check("probe_exhausted" in source,
          "в лечащем нет живой проверки аккаунта (проба)")

    if failures:
        print("❌ приёмка «автомат аккаунтов — только аккаунты»: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка «автомат аккаунтов — только аккаунты»: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
