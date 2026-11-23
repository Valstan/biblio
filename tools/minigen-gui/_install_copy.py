"""Разовая установка программы «МиниГен» в своё место (обход гейта PowerShell).

Копирует: рантайм Electron, собранное окно, мост и базу знаний — в
%LOCALAPPDATA%\\Programs\\МиниГен. Ярлыки и автозапуск создаются отдельно.
"""
import json
import os
import shutil
import sys
from pathlib import Path

SRC = Path(r"D:\valstan\REPO\biblio-minigen\tools\minigen-gui")
ROOT = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "МиниГен"
APP = ROOT / "app"
ELECTRON = ROOT / "electron"

LAUNCHER = """@echo off
rem MiniGen launcher (installed copy, ASCII only).
setlocal
cd /d "%~dp0"
if not exist "%~dp0electron\\dist\\electron.exe" (
  echo Electron runtime not found - re-run install.ps1 in the repository.
  pause
  exit /b 1
)
start "" "%~dp0electron\\dist\\electron.exe" "%~dp0app"
"""

AUTOSTART = """@echo off
rem MiniGen background mode: the daemons (healer, network watcher, session
rem watcher) stay alive without a window. ASCII only.
setlocal
cd /d "%~dp0"
set "PYTHONW=%LOCALAPPDATA%\\Programs\\Python\\Python312\\pythonw.exe"
if not exist "%PYTHONW%" set "PYTHONW=pythonw.exe"
start "" "%PYTHONW%" "%~dp0app\\bridge\\minigen_bridge.py" --daemon
"""


def copy_tree(src: Path, dst: Path, skip=()) -> int:
    if not src.is_dir():
        return 0
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*skip) if skip else None)
    return sum(1 for _ in dst.rglob("*") if _.is_file())


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    APP.mkdir(parents=True, exist_ok=True)
    ELECTRON.mkdir(parents=True, exist_ok=True)

    n = copy_tree(SRC / "node_modules" / "electron" / "dist", ELECTRON / "dist")
    print("electron/dist: %d файлов" % n)
    for name in ("package.json", "path.txt"):
        src = SRC / "node_modules" / "electron" / name
        if src.is_file():
            shutil.copy2(src, ELECTRON / name)

    copy_tree(SRC / "out", APP / "out")
    copy_tree(SRC / "bridge", APP / "bridge", skip=("__pycache__", "*.pyc"))
    (APP / "docs").mkdir(parents=True, exist_ok=True)
    src = SRC / "docs" / "knowledge-pack.md"
    if src.is_file():
        shutil.copy2(src, APP / "docs" / "knowledge-pack.md")
    shutil.copy2(SRC / "package.json", APP / "package.json")

    (ROOT / "Запустить.cmd").write_bytes(LAUNCHER.encode("ascii"))
    (ROOT / "Автозапуск.cmd").write_bytes(AUTOSTART.encode("ascii"))

    # настройки: где исходники (для правок и PR)
    settings_path = Path(os.environ["LOCALAPPDATA"]) / "minigen-gui" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    if not data.get("codeRepo"):
        data["codeRepo"] = str(Path(r"D:\valstan\REPO\biblio-minigen"))
        settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        print("настройки: codeRepo =", data["codeRepo"])
    else:
        print("настройки: codeRepo уже есть —", data["codeRepo"])

    print("установлено в:", ROOT)
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and path.suffix in (".cmd", ".json", ".md", ".js"):
            print("   ", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
