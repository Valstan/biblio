#!/usr/bin/env python3
r"""Приёмка панели «МиниГен»: скрипт страницы должен парситься.

Зачем эта проверка (18.11): панель дважды отдавала ПУСТУЮ страницу — шапка
жива, таблица пуста, кнопки мертвы. Сервер был здоров (`/api/state` отдавал
200 со всеми областями), а браузер получал
`SyntaxError: Invalid or unexpected token`: в шаблонной строке
`const HTML = `...` одиночный escape разорвал строковый литерал в отданном
скрипте.

Правило, которое отсюда следует: «страница грузится» != «скрипт работает».
Проверять надо ВЫЧИСЛЕННЫЙ шаблон, а не его исходный текст: сырой текст с
одиночным `\n` остаётся синтаксически валидным, ломается результат.

Три уровня:

1. ЛИНТ ИСХОДНИКА: внутри шаблонной строки любая обратная косая должна быть
   удвоена (`\\n`, а не `\n`). Одиночная — это всегда либо тихая порча
   (escape превратится в символ), либо обрыв строкового литерала. Ловит
   ошибку набора ДО выкатки.
2. ВЫЧИСЛЕННЫЙ ШАБЛОН: рендерим шаблон так же, как Node, вырезаем
   `<script>` и прогоняем `node --check`. Ловит любой синтаксический облом
   в отданном скрипте. Работает в CI — панель для этого не нужна.
3. ЖИВАЯ СТРАНИЦА: если панель поднята (127.0.0.1:8099) — вырезаем `<script>`
   из её ответа и проверяем его же: исходник может быть валиден, а
   отдаваемая страница — нет.

Выход: 0 — все проверки прошли (или живую пропустили: панель не поднята),
1 — хоть одна проверка не прошла.

Запуск:
    python tools/minimax-project-areas/check_panel.py              # всё
    python tools/minimax-project-areas/check_panel.py --source-only # без живой (CI)
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

# Печать значков (🟢/🔴) в консоли Windows ломается на кодовой странице
# консоли (charmap) — скрипт падал с UnicodeEncodeError. Принудительно UTF-8
# с заменой непечатаемого: приёмка должна работать отовсюду.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL_JS = os.path.join(HERE, "mcode-panel.js")
RENDER_JS = os.path.join(HERE, "render_panel_html.js")
PANEL_URL = "http://127.0.0.1:8099/"

TEMPLATE_OPEN = "const HTML = `"
TEMPLATE_TAIL = "</script></body></html>"


def fail(msg):
    print("🔴 " + msg)
    return 1


def ok(msg):
    print("🟢 " + msg)
    return 0


def template_slice(source):
    """Границы шаблонной строки в исходнике: (start, end) или None."""
    i = source.find(TEMPLATE_OPEN)
    if i < 0:
        return None
    start = i + len(TEMPLATE_OPEN)
    tail = source.find(TEMPLATE_TAIL, start)
    if tail < 0:
        return None
    end = source.find("`", tail)
    if end < 0:
        return None
    return start, end


def extract_script(html):
    """Вырезать содержимое <script>…</script>."""
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    return m.group(1) if m else None


def lint_escapes(source):
    """Одиночная обратная косая внутри шаблона — всегда ошибка набора."""
    sl = template_slice(source)
    if not sl:
        return fail("в исходнике не найдена шаблонная строка %s" % TEMPLATE_OPEN)
    start, end = sl
    body = source[start:end]
    bad = []
    for n, line in enumerate(body.split("\n"), 1):
        i = 0
        while i < len(line):
            if line[i] == "\\":
                if i + 1 < len(line) and line[i + 1] == "\\":
                    i += 2
                    continue
                bad.append((n, line.strip()[:110]))
                i += 1
            else:
                i += 1
    if bad:
        for n, text in bad[:6]:
            print("   строка %d шаблона: %s" % (n, text))
        return fail("в шаблоне %d одиночная обратная косая — удвойте её (\\\\n вместо \\n)"
                    % len(bad))
    return ok("линт экранирования шаблона: чисто")


def node_check(label, script):
    """Прогнать node --check по куску скрипта."""
    if not script or not script.strip():
        return fail("%s: скрипт не найден" % label)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        proc = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        if proc.returncode == 0:
            return ok("%s: node --check прошёл (%d байт скрипта)" % (label, len(script.encode("utf-8"))))
        err = (proc.stderr or "").strip().splitlines()
        detail = err[0] if err else "код %d" % proc.returncode
        return fail("%s: node --check ругнулся — %s" % (label, detail))
    except FileNotFoundError:
        print("⚠️ node не найден — проверку синтаксиса пропускаю")
        return 0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def render_source_html():
    """HTML, вычисленный Node'ом из шаблона исходника (None — не получилось)."""
    if not os.path.isfile(RENDER_JS):
        print("⚠️ нет %s — рендер пропускаю" % RENDER_JS)
        return None
    try:
        proc = subprocess.run(["node", RENDER_JS, PANEL_JS], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print("⚠️ node не найден — рендер пропускаю")
        return None
    if proc.returncode != 0:
        return fail("шаблон не вычисляется — %s" % ((proc.stderr or "").strip()[:160]))
    return proc.stdout


def check_source():
    if not os.path.isfile(PANEL_JS):
        return fail("исходник не найден: %s" % PANEL_JS)
    with open(PANEL_JS, encoding="utf-8") as fh:
        source = fh.read()
    rc = lint_escapes(source)
    if rc:
        return rc
    html = render_source_html()
    if html is None:
        return 1
    script = extract_script(html)
    if script is None:
        return fail("в вычисленном шаблоне нет тега <script>")
    return node_check("вычисленный шаблон панели", script)


def check_live():
    try:
        with urllib.request.urlopen(PANEL_URL, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        print("⚠️ панель не отвечает (%s) — живую проверку пропускаю" % exc)
        return 0
    script = extract_script(html)
    if script is None:
        return fail("живая страница: нет тега <script>")
    return node_check("живая страница (%s)" % PANEL_URL, script)


def main():
    ap = argparse.ArgumentParser(description="приёмка панели «МиниГен»")
    ap.add_argument("--source-only", action="store_true", help="только исходник (для CI)")
    args = ap.parse_args()

    rc = check_source()
    if rc == 0 and not args.source_only:
        rc = check_live() or rc
    print("✅ приёмка панели: PASS" if rc == 0 else "❌ приёмка панели: FAIL")
    return rc


if __name__ == "__main__":
    sys.exit(main())
