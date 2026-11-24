# -*- coding: utf-8 -*-
"""Приёмка: в модулях демонов **нет неопределённых имён**.

Живой случай 24.11: лечащий падал с `NameError: name 'core' is not defined` —
в двух функциях стояло `core.model_flags(...)`, а имя `core` там не было
определено (ядро грузится лениво, через `_core()`). Модуль при этом
**компилировался** — `py_compile` такой дефект не ловит, и проверок на него
не было.

Что проверяем: в каждой функции модулей демонов нет имени, которое
используется, но нигде не определено — ни в функции, ни выше (в модуле или в
объемлющей функции).

Запуск: python3 tools/minimax-project-areas/test_daemons_names_defined.py
"""
from __future__ import annotations

import ast
import builtins
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

# Модули, где демоны и ядро живут: там и был NameError.
FILES = ("minimax-autheal.py", "minimax-netwatch.py", "minimax-sessionwatch.py",
         "minimax-switch.py", "minimax-orphans.py", "minigen_core.py",
         "minigen_time.py")


def module_names(tree: ast.Module) -> set[str]:
    """Имена, определённые на уровне модуля (включая блоки try/if)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            names.update(node.names)

    def collect(body) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue          # тело функции — не уровень модуля
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    for sub in ast.walk(target):
                        if isinstance(sub, ast.Name):
                            names.add(sub.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            # блоки (try/if/for/with) — их тела тоже уровень модуля
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(node, field, None)
                if isinstance(sub, list):
                    collect(sub)
            for handler in getattr(node, "handlers", []) or []:
                collect(handler.body)

    collect(tree.body)
    names.update({"__file__", "__name__", "__doc__", "__package__", "__spec__"})
    return names


def own_names(node: ast.AST) -> set[str]:
    """Имена, которые функция (или класс) определяет у себя."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            names.add(sub.id)
        elif isinstance(sub, ast.arg):
            names.add(sub.arg)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(sub.name)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            names.add(sub.name)
    return names


def undefined_in(tree: ast.Module) -> list[tuple[str, int, str]]:
    """Список (функция, строка, имя) — имена, которые никто не определил."""
    problems: list[tuple[str, int, str]] = []
    module = module_names(tree)

    def check_function(func: ast.AST, visible: set[str]) -> None:
        own = own_names(func)
        used: dict[str, int] = {}
        for sub in ast.walk(func):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                used.setdefault(sub.id, sub.lineno)
        for name, line in used.items():
            if name in own or name in visible:
                continue
            if name in dir(builtins) or name in ("self", "cls"):
                continue
            problems.append((getattr(func, "name", "?"), line, name))

    def walk(body, visible: set[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                check_function(node, visible)
                walk(node.body, visible | own_names(node))
            elif isinstance(node, ast.ClassDef):
                check_function(node, visible)
                walk(node.body, visible | own_names(node))

    walk(tree.body, module)
    return problems


def main() -> int:
    failures: list[str] = []
    print("1) в модулях демонов нет неопределённых имён")
    for name in FILES:
        path = HERE / name
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for func, line, undefined in undefined_in(tree):
            message = "%s: в функции %s (строка %d) имя '%s' не определено" % (
                name, func, line, undefined)
            failures.append(message)
            print("   FAIL: " + message)

    print("2) лечащий зовёт ядро через _model_flags (а не через core напрямую)")
    tree = ast.parse((HERE / "minimax-autheal.py").read_text(encoding="utf-8-sig"))
    helper_lines: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_model_flags":
            helper_lines = (node.lineno, getattr(node, "end_lineno", node.lineno))
    direct = [node for node in ast.walk(tree)
              if isinstance(node, ast.Attribute) and node.attr == "model_flags"
              and isinstance(node.value, ast.Name) and node.value.id == "core"]
    for node in direct:
        inside = (helper_lines is not None
                  and helper_lines[0] <= node.lineno <= helper_lines[1])
        if not inside:
            failures.append("в лечащем есть прямая ссылка core.model_flags (строка %d)"
                            % node.lineno)
            print("   FAIL: в лечащем есть прямая ссылка core.model_flags")
    if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "_model_flags" for node in ast.walk(tree)):
        failures.append("лечащий не зовёт _model_flags")
        print("   FAIL: лечащий не зовёт _model_flags")

    if failures:
        print("❌ приёмка имён демонов: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка имён демонов: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
