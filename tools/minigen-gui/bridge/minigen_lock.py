"""Эксклюзивная блокировка надзирателя демонов.

Зачем этот модуль. Демоны должен вести **один** процесс: две копии лечащего
брали аккаунты у КАРМАНА дважды (урок 22.11). Проверка «жив ли другой
экземпляр» по ответу API (PR #83) работает, но у неё два слепых места:

- **гонка**: два экземпляра стартуют одновременно (автозапуск при входе в
  Windows + запуск руками), оба спрашивают API, оба видят «никого» — и оба
  становятся надзирателями;
- **окно**: окно программы — тоже отдельный процесс моста, и оно тоже следит
  за демонами (`Bridge.start_daemon_supervision`). Открытое окно плюс
  фоновая копия = снова два надзирателя.

Живой пример (23.11, найдено при разборе хвоста Ф6): на машине жили **два**
фоновых менеджера из дерева репозитория, и демоны были перезапущены дважды
за две минуты (19:52 и 19:54) — каждый надзиратель считал, что демоны упали.

Поэтому надзирательство защищаем **системной блокировкой файла**: `msvcrt`
на Windows, `flock` на POSIX. Она атомарна (две одновременные попытки — одна
выигрывает) и снимается ОС, когда процесс умирает. Файл-флаг для этого не
годится: он остаётся от упавшего процесса, и тогда демоны останутся без
присмотра — ровно та дыра, из-за которой сторож сессий однажды остался без
надзора.

Кто надзиратель — пишем отдельным файлом (`supervisor.json`): в нём pid и
путь, откуда запущен. Так окно может показать «надзиратель: pid …, из
<путь>», и сразу видно, если программа запущена из дерева репозитория
(такого быть не должно — программа живёт в своём месте, PR #79).

Файлы:

- `%LOCALAPPDATA%\\minigen-gui\\supervisor.lock` — сама блокировка;
- `%LOCALAPPDATA%\\minigen-gui\\supervisor.json` — кто держит (может
  протухнуть, если владелец умер; тогда ориентируемся на блокировку, а не на
  файл).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

LOCK_NAME = "supervisor.lock"
INFO_NAME = "supervisor.json"


def _base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "minigen-gui"


def lock_path() -> Path:
    return _base() / LOCK_NAME


def info_path() -> Path:
    return _base() / INFO_NAME


def pid_alive(pid: int) -> bool:
    """Жив ли процесс с таким pid.

    ⚠️ На Windows `os.kill(pid, 0)` **убивает процесс** (CPython отправляет
    `TerminateProcess`), поэтому проверка живости здесь своя: `OpenProcess` с
    `PROCESS_QUERY_LIMITED_INFORMATION`. Отказ в доступе (5) — процесс есть,
    просто он привилегированный.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return ctypes.windll.kernel32.GetLastError() == 5
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_one_byte(fd: int) -> None:
    """Захватить один байт файла эксклюзивно (без ожидания)."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_one_byte(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


class SupervisorLock:
    """Блокировка надзирателя: «я — единственный, кто следит за демонами»."""

    def __init__(self, path: Path | None = None, info: Path | None = None) -> None:
        self.path = Path(path) if path is not None else lock_path()
        self.info = Path(info) if info is not None else info_path()
        self._fd: int | None = None
        self._owned = False

    # ─────────────────────────────────────────────────────────── захват
    def acquire(self) -> bool:
        """Взять блокировку. `False` — её уже держит другой живой процесс."""
        if self._owned:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            # На Windows блокировать нечего, если файл пуст — байт нужен.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            _lock_one_byte(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        self._owned = True
        self._write_info()
        return True

    def release(self) -> None:
        """Отпустить блокировку (файл удаляем: он и есть признак «свободно»)."""
        if not self._owned or self._fd is None:
            return
        try:
            _unlock_one_byte(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        self._owned = False
        try:
            if self.info.is_file():
                self.info.unlink()
        except OSError:
            pass

    @property
    def owned(self) -> bool:
        return self._owned

    # ─────────────────────────────────────────────────────────── кто
    def _write_info(self) -> None:
        data = {
            "pid": os.getpid(),
            "path": _script_path(),
            "since": time.time(),
        }
        try:
            self.info.parent.mkdir(parents=True, exist_ok=True)
            self.info.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def owner(self) -> dict[str, Any] | None:
        """Кто держит блокировку (по файлу-подсказке). `None` — неизвестно."""
        return read_owner(self.info)


def _script_path() -> str:
    try:
        return str(Path(sys.argv[0]).resolve())
    except Exception:  # noqa: BLE001
        return sys.argv[0] if sys.argv else ""


def read_owner(info: Path | None = None) -> dict[str, Any] | None:
    """Прочитать подсказку «кто надзиратель» (может быть протухшей)."""
    path = Path(info) if info is not None else info_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = int(data.get("pid") or 0)
    return {"pid": pid, "path": str(data.get("path") or ""),
            "since": data.get("since"), "alive": pid_alive(pid)}


_LOCK: SupervisorLock | None = None


def get_lock() -> SupervisorLock:
    """Блокировка процесса (одна на процесс).

    ⚠️ Именно одна на процесс, а не на объект: демон-режим берёт блокировку
    сам, а потом создаёт `Bridge`, который тоже начинает следить за демонами.
    Если бы это были два разных объекта, второй захват **в том же процессе**
    конфликтовал бы с первым (Windows считает конфликт и внутри процесса) —
    и демоны остались бы без надзирателя.
    """
    global _LOCK
    if _LOCK is None:
        _LOCK = SupervisorLock()
    return _LOCK
