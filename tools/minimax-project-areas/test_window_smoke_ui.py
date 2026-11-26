# -*- coding: utf-8 -*-
"""Приёмка окна «МиниГен» глазами: вкладки, их текст и снимки экрана (24.11).

Заказ владельца: «карту родословной… отдельная вкладка "Гены"». Вкладку
сделали и покрыли тестами **логики** (раскладка графа, строки родословной,
московское время) — но **само окно** никто не видел: вёрстка, живые данные
пула и карточка «Родословная копии» остались без проверки.

Теперь окно умеет **само себя показывать** (штатный дымовой режим
`MINIGEN_SMOKE=1`, расширенный снимками вкладок):

- `MINIGEN_SMOKE_TABS` — метки вкладок через запятую;
- `MINIGEN_SMOKE_TEXT` — файл с JSON `{метка: видимый текст вкладки}`;
- `MINIGEN_SMOKE_SHOTS` — папка с PNG-снимками вкладок.

Эта приёмка запускает окно в дымовом режиме и проверяет, что на вкладках
есть ожидаемое: «Гены» — карта родословной с живыми данными пула,
«Настройки» — строка «Москва, Россия (UTC+3)» и карточка «Родословная
копии», «Агенты» — состояние канала КАРМАНА и живой агент «Идеи» с
репозиторием и областью (заявка владельца от 24.11).

Часть 1 (всегда, работает и в гейте на Linux) — проверка, что окно вообще
умеет отдавать снимки вкладок. Часть 2 (живая) — только там, где есть
Windows и сама программа; в гейте она **пропускается** с объяснением, а не
падает (живой случай 24.11: приёмка окна падала сама в среде без программы).

Запуск: python tools/minimax-project-areas/test_window_smoke_ui.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
GUI = REPO / "tools" / "minigen-gui"

# Что должно быть на вкладках. Метки — как в окне (это подписи кнопок).
TAB_MARKERS: dict[str, list[str]] = {
    "Области": [
        "Рабочие области",   # список областей
        "Область",            # шапки столбцов
        "Работает ▼",         # сортировка по умолчанию — по статусу «Работает»
        "Сессии",
    ],
    "Гены": [
        "Паутина разработки",   # заголовок карты
        "Я в этой паутине",     # карточка «кто я»
        "Копии",                # таблица копий
        "Гены",                 # таблица генов
        "Связи",                # список связей
        "мост запущен",         # шапка окна не врёт о мосте
    ],
    "Черенки": [
        "Черенкование",             # заголовок вкладки
        "Что отправить знакомому",  # главный вопрос владельца (26.11)
        "Скопировать инструкцию",   # кнопка: инструкция в буфер обмена
        "Передать наработки",       # передача генов
        "Что важно помнить",        # правила второй копии
    ],
    "Настройки": [
        "Время",                        # строка часового пояса
        "Москва, Россия (UTC+3)",       # сам пояс — по заказу владельца
        "Родословная копии",            # карточка родословной
        "коммит",                       # версия программы — прочитана (не «неизвестна»)
        "Поколение",                    # родословная — прочитана (не «не прочитана»)
        "Родитель по гену",
        "демоны 3/3",                   # демоны видны: шапка не врёт
        "Совет специалистов",           # совет: включён/выключен и сколько ролей
    ],
    "Агенты": [
        "Канал КАРМАНА",         # состояние канала промтов
        "Реестр агентов",        # карточка со списком агентов
        "Идеи",                  # живой агент «Идеи» из реестра
        "Valstan/ideas",         # его репозиторий
        "p-ideas",               # его область
        "Восстановить",          # действие восстановления есть
    ],
    "Промты": [
        "Хранилище промтов",      # заголовок вкладки
        "Место хранения",         # карточка места хранения (база или внешний ресурс)
        "Промты и планы",         # список промтов и планов
        "Новый промт",            # форма добавления промта
        "Переключается в «Настройках»",   # где выбирается место хранения
    ],
}


def source_checks(failures: list[str]) -> None:
    """Часть 1: окно умеет отдавать снимки вкладок (работает везде)."""
    print("1) окно умеет снимать вкладки")
    index = GUI / "src" / "main" / "index.ts"
    helper = GUI / "src" / "main" / "smoke.ts"
    for path in (index, helper):
        if not path.is_file():
            failures.append("нет файла: %s" % path)
            print("   FAIL: нет файла: %s" % path)
            continue
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        for marker in ("MINIGEN_SMOKE_TABS", "MINIGEN_SMOKE_TEXT", "MINIGEN_SMOKE_SHOTS"):
            if marker not in text:
                failures.append("окно не читает %s" % marker)
                print("   FAIL: окно не читает %s" % marker)
        if "minigen-gui-smoke" not in text:
            failures.append("дымовой режим не отделён профилем — проверка мешала бы окну владельца")
            print("   FAIL: дымовой режим не отделён профилем")
    if helper.is_file():
        text = helper.read_text(encoding="utf-8")
        for marker in ("clickTabScript", "parseSmokeTabs", "shotName"):
            if marker not in text:
                failures.append("в помощнике окна нет %s" % marker)
                print("   FAIL: в помощнике окна нет %s" % marker)
    print("   окно: снимки вкладок %s" % ("есть" if not failures else "НЕ готовы"))


def find_target() -> tuple[Path, Path] | None:
    """Где запускать окно: своя сборка в репозитории или установленная копия."""
    repo_electron = GUI / "node_modules" / "electron" / "dist" / "electron.exe"
    if repo_electron.is_file() and (GUI / "out" / "main" / "index.js").is_file():
        return repo_electron, GUI
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        root = Path(local) / "Programs" / "МиниГен"
        installed = root / "electron" / "dist" / "electron.exe"
        if installed.is_file() and (root / "app").is_dir():
            return installed, root / "app"
    return None


def live_check(failures: list[str], keep_dir: Path | None = None) -> None:
    """Часть 2: живой запуск окна в дымовом режиме и проверка вкладок."""
    print("2) живая проверка вкладок")
    if os.name != "nt":
        print("   пропущено (нужна Windows: окно — это приложение, не скрипт)")
        return
    target = find_target()
    if not target:
        print("   пропущено (нет ни собранной программы, ни установленной копии)")
        return
    electron, app_dir = target
    print("   окно: %s" % electron.parent.parent.parent)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text_path = root / "tabs.json"
        shots_dir = root / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["MINIGEN_SMOKE"] = "1"
        env["MINIGEN_SMOKE_TABS"] = ",".join(TAB_MARKERS.keys())
        env["MINIGEN_SMOKE_TEXT"] = str(text_path)
        env["MINIGEN_SMOKE_SHOTS"] = str(shots_dir)
        try:
            # ⚠️ Таймаут 600 с, а не 180: дымовой режим ждёт **данных** вкладки
            # (мост отвечает по очереди, а снимок состояния идёт 4–23 с), и под
            # нагрузкой машины окно закрывается заметно позже (живой случай
            # 26.11: на 180 с окно не успевало закрыться само).
            proc = subprocess.run(
                [str(electron), str(app_dir)],
                env=env, timeout=600,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            failures.append("окно не закрылось само — дымовой режим не сработал")
            print("   FAIL: окно не закрылось само (таймаут)")
            return
        except OSError as exc:
            failures.append("не удалось запустить окно: %s" % exc)
            print("   FAIL: не удалось запустить окно: %s" % exc)
            return
        if proc.returncode != 0:
            print("   ⚠ код возврата окна: %s (не критично — снимки главное)" % proc.returncode)
        if not text_path.is_file():
            failures.append("окно не отдало текст вкладок")
            print("   FAIL: окно не отдало текст вкладок")
            return
        try:
            texts = json.loads(text_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            failures.append("текст вкладок не прочитан: %s" % exc)
            print("   FAIL: текст вкладок не прочитан: %s" % exc)
            return
        for label, markers in TAB_MARKERS.items():
            entry = texts.get(label) or {}
            visible = entry.get("text", "") if isinstance(entry, dict) else str(entry)
            if not visible:
                failures.append("вкладка «%s» не снята" % label)
                print("   FAIL: вкладка «%s» не снята" % label)
                continue
            missing = [marker for marker in markers if marker not in visible]
            if missing:
                failures.append("вкладка «%s»: нет %s" % (label, ", ".join(missing)))
                print("   FAIL: вкладка «%s»: нет %s" % (label, ", ".join(missing)))
            else:
                print("   ок: «%s» — все маркеры на месте (%d символов)" % (label, len(visible)))
            # карта не должна быть обрезана (живой случай 24.11: ширина
            # считалась по числу колонок, правый край уезжал за полотно)
            geometry = entry.get("map") if isinstance(entry, dict) else None
            if geometry:
                if geometry.get("ok"):
                    print("   ок: карта не обрезана (полотно %sx%s, узлы до %sx%s)"
                          % (geometry.get("width"), geometry.get("height"),
                             geometry.get("max_x"), geometry.get("max_y")))
                else:
                    failures.append("карта обрезана: полотно %sx%s, узлы до %sx%s"
                                    % (geometry.get("width"), geometry.get("height"),
                                       geometry.get("max_x"), geometry.get("max_y")))
                    print("   FAIL: карта обрезана — полотно %sx%s, узлы до %sx%s"
                          % (geometry.get("width"), geometry.get("height"),
                             geometry.get("max_x"), geometry.get("max_y")))
        shots = sorted(shots_dir.glob("*.png"))
        if len(shots) < len(TAB_MARKERS):
            failures.append("снимков экрана %d, а вкладок %d" % (len(shots), len(TAB_MARKERS)))
            print("   FAIL: снимков экрана %d, а вкладок %d" % (len(shots), len(TAB_MARKERS)))
        else:
            for shot in shots:
                size = shot.stat().st_size
                if size < 1024:
                    failures.append("снимок %s пустой (%d байт)" % (shot.name, size))
                    print("   FAIL: снимок %s пустой (%d байт)" % (shot.name, size))
                else:
                    print("   ок: снимок %s (%d КБ)" % (shot.name, size // 1024))
        if keep_dir is not None:
            # Снимки оставить владельцу: это и есть «приёмка глазами» —
            # посмотреть можно своими глазами.
            import shutil
            keep_dir.mkdir(parents=True, exist_ok=True)
            kept = keep_dir / "shots"
            if kept.exists():
                shutil.rmtree(kept, ignore_errors=True)
            shutil.copytree(shots_dir, kept)
            shutil.copy2(text_path, keep_dir / "tabs.json")
            print("   снимки оставлены: %s" % keep_dir)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Приёмка окна «МиниГен» глазами")
    parser.add_argument("--out", default="",
                        help="папка, куда оставить снимки вкладок и их текст")
    args = parser.parse_args()
    failures: list[str] = []
    source_checks(failures)
    live_check(failures, Path(args.out).expanduser() if args.out else None)
    if failures:
        print("❌ приёмка окна: FAIL (%d)" % len(failures))
        for item in failures:
            print("   - " + item)
        return 1
    print("✅ приёмка окна: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
