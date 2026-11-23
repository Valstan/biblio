# -*- coding: utf-8 -*-
"""Тест: перезапуск контура после смены прокси идёт к новому менеджеру.

Симптом (23.11, найдено при архивировании панели): сетевой сторож после
смены прокси звал `http://127.0.0.1:8098/restart-contour` — страницу
управления СТАРОЙ панели. Панель архивировали, супервизор остановили — и
каскад восстановления сети стал работать наполовину: прокси переключался,
а контур не перезапускался, поэтому лечащий и сторож сессий продолжали
работать со старыми прокси-переменными.

Проверяем на настоящем модуле:
  1) первым адресатом идёт программа «МиниГен» (:8097) — с токеном;
  2) старый супервизор (:8098) остаётся запасным путём;
  3) без файла с токеном программа не зовётся молча — только супервизор;
  4) `restart_contour()` пробует адресаты по порядку и возвращает успех.

Запуск: python3 tools/minimax-project-areas/test_minigen_contour_restart.py
"""
import importlib.util
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def load_netwatch():
    spec = importlib.util.spec_from_file_location(
        "nw_under_test", str(HERE / "minimax-netwatch.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    nw = load_netwatch()
    failures = []

    def check(cond, message):
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    tmp = tempfile.TemporaryDirectory()
    # токен лежит там же, где его кладёт программа:
    # %LOCALAPPDATA%\minigen-gui\bridge-token.txt
    token_dir = pathlib.Path(tmp.name) / "minigen-gui"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_file = token_dir / "bridge-token.txt"
    token_file.write_text("test-token", encoding="utf-8")
    os.environ["LOCALAPPDATA"] = tmp.name
    old_local = os.environ.get("LOCALAPPDATA")

    try:
        # ── 1–2) с токеном: первым программа, запасной — супервизор ──────
        targets = nw.contour_targets()
        urls = [t[0] for t in targets]
        headers = [t[1] for t in targets]
        check(any("8097" in u for u in urls),
              "программа «МиниГен» (:8097) не в списке адресатов: %s" % urls)
        check(any("8098" in u for u in urls),
              "старый супервизор (:8098) не остался запасным: %s" % urls)
        if any("8097" in u for u in urls):
            first = urls.index(next(u for u in urls if "8097" in u))
            check(first == 0, "программа должна быть ПЕРВОЙ: %s" % urls)
            check(headers[first].get("X-MiniGen-Token") == "test-token",
                  "токен не передан в заголовке: %s" % headers[first])

        # ── 4) порядок проб при перезапуске контура ──────────────────────
        tried = []

        class FakeResponse:
            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            tried.append(url)
            if "8097" in url:
                raise OSError("программа не отвечает")
            return FakeResponse()

        original = nw.urllib.request.urlopen
        nw.urllib.request.urlopen = fake_urlopen
        try:
            ok = nw.restart_contour()
        finally:
            nw.urllib.request.urlopen = original

        check(ok, "перезапуск контура должен считаться успешным через запасной путь")
        check(len(tried) >= 2, "адресаты должны проверяться по порядку: %s" % tried)
        if len(tried) >= 2:
            check("8097" in tried[0] and "8098" in tried[1],
                  "порядок проб нарушен: %s" % tried)

        # ── 3) без токена программа не зовётся ────────────────────────────
        token_file.unlink()
        targets = nw.contour_targets()
        urls = [t[0] for t in targets]
        check(not any("8097" in u for u in urls),
              "без токена программа звать нельзя: %s" % urls)
        check(any("8098" in u for u in urls),
              "супервизор должен остаться запасным: %s" % urls)
    finally:
        tmp.cleanup()
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local

    if failures:
        print("❌ приёмка перезапуска контура: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка перезапуска контура: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
