"""Инструкция по установке «МиниГена» на другой компьютер.

Заказ владельца (26.11): «сделай в интерфейсе МиниГена ссылку на инструкцию
установки минигена… А то я сейчас порылся и не могу понять что мне отправить
знакомому чтобы он у себя установил миниген тоже».

Что здесь: программа **сама** находит файл инструкции и отдаёт окну его
текст, путь и путь к установщику. Владельцу не нужно искать по диску, что
отправить: текст инструкции виден в окне, его можно скопировать в буфер, а
файл — открыть.

⚠️ Честность: если файла нет — окно говорит об этом прямо и говорит, **что
делать** (положить `INSTALL.md` в папку программы или указать путь к
исходникам в настройке `codeRepo`), а не показывает пустую страницу.

⚠️ Пути и адреса — **на машине владельца**: в репозиторий ничего из этого не
попадает (D-038).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import minigen_state as state

HERE = Path(__file__).resolve().parent
#: Папка программы: `bridge/` → `../`. В установленной программе это `app/`,
#: в репозитории — `tools/minigen-gui/`.
PROGRAM_DIR = HERE.parent
#: Имя файла инструкции.
GUIDE_NAME = "INSTALL.md"
#: Имя установщика (рядом с папкой `docs`).
INSTALLER_NAME = "install.ps1"
#: Предел на размер инструкции (файл маленький, но пусть будет предел).
MAX_GUIDE_CHARS = 60_000
#: Публичный адрес программы — как в инструкции. Не секрет: он и в самой
#: инструкции написан.
PUBLIC_REPO = "https://github.com/Valstan/biblio"


def _sources_root() -> Path | None:
    """Корень исходников программы (настройка `codeRepo`)."""
    raw = str(state.read_settings().get("codeRepo") or "").strip()
    return Path(raw) if raw else None


def _program_dir_of(root: Path) -> Path:
    """Папка программы внутри репозитория (`tools/minigen-gui`).

    ⚠️ `codeRepo` указывает на корень репозитория, но инструкция лежит в
    папке программы внутри него. Если путь уже указывает на папку программы —
    берём её как есть.
    """
    if (root / "tools" / "minigen-gui").is_dir():
        return root / "tools" / "minigen-gui"
    if (root / "docs").is_dir() and (root / "bridge").is_dir():
        return root
    return root / "tools" / "minigen-gui"


def candidates() -> list[tuple[Path, str]]:
    """Где искать инструкцию: сперва исходники, потом папка программы."""
    out: list[tuple[Path, str]] = []
    root = _sources_root()
    if root is not None:
        out.append((_program_dir_of(root) / "docs" / GUIDE_NAME, "исходники"))
    out.append((PROGRAM_DIR / "docs" / GUIDE_NAME, "папка программы"))
    return out


def _git_remote(root: Path | None) -> str:
    """Адрес репозитория программы — из его git-remote (если он есть)."""
    if root is None:
        return ""
    try:
        res = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    if res.returncode != 0:
        return ""
    url = (res.stdout or "").strip()
    if url.startswith("git@") and ":" in url:
        url = "https://" + url.split(":", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    return url


def read_guide() -> dict[str, Any]:
    """Инструкция по установке — текст, путь и установщик.

    Возвращает `{"ok": True, ...}` либо `{"ok": False, "error": ..., "why": ...}`
    — с честным объяснением, что делать, если файла нет.
    """
    root = _sources_root()
    for path, source in candidates():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        program_dir = path.parent.parent
        installer = program_dir / INSTALLER_NAME
        try:
            installer_path = str(installer) if installer.is_file() else ""
        except OSError:
            installer_path = ""
        title = ""
        for line in text.splitlines():
            if line.strip():
                title = line.strip().lstrip("# ").strip()
                break
        return {
            "ok": True,
            "title": title or "Установка «МиниГена» на новый компьютер",
            "text": text[:MAX_GUIDE_CHARS],
            "path": str(path),
            "source": source,
            "installer": installer_path,
            "installer_dir": str(program_dir) if installer_path else "",
            "repo": _git_remote(root) or PUBLIC_REPO,
        }
    return {
        "ok": False,
        "error": "инструкция по установке не найдена",
        "why": ("Файл `%s` не лежит ни в исходниках (настройка `codeRepo`), ни в "
                "папке программы. Что делать: положить `%s` в папку программы "
                "(рядом с `docs`) или указать путь к исходникам в настройках — "
                "тогда инструкция появится здесь."
                % (GUIDE_NAME, GUIDE_NAME)),
        "path": "",
        "installer": "",
        "installer_dir": "",
        "repo": _git_remote(root) or PUBLIC_REPO,
    }
