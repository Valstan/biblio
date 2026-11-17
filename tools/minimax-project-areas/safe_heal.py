"""Разовое лечение области по команде (обёртка над minimax-autheal.py).

Одна реализация механики — в minimax-autheal.py; здесь только CLI:
    python C:\\Users\\valstan\\bin\\safe_heal.py vmalmyzhe [область ...]
Лечит область: отказ по очкам или потерянный вход → новый аккаунт у Кармана →
claim бонуса → перезапуск области → «Продолжай». Токен проверяется ДО установки:
протухший или не принятый вендором — такой не оставляем (17.11).
"""
import importlib.util
import sys
from pathlib import Path

BIN = Path(r"C:\Users\valstan\bin")
spec = importlib.util.spec_from_file_location("autheal", str(BIN / "minimax-autheal.py"))
ah = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ah)


def main(argv):
    if not argv:
        print("использование: python safe_heal.py <область> [<область> ...]")
        return 2
    switch = ah.load_switch()
    ah.load_cooldowns()
    ok = True
    for slug in argv:
        area = next((a for a in ah.all_areas() if a["slug"] == slug), None)
        if area is None:
            print("%s: нет такой области в конфиге" % slug)
            ok = False
            continue
        print("=== %s (%s) ===" % (area["title"], slug))
        diag = ah.diagnose(area)
        print("вердикт: %s — %s" % (diag["verdict"], diag["reason"]))
        if diag["verdict"] in ("ОТКАЗ ПО ОЧКАМ", "ПОТЕРЯН ВХОД"):
            ok = ah.heal(area, switch, force=True) and ok
        else:
            print("лечить нечего")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
