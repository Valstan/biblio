# -*- coding: utf-8 -*-
"""Тест переключения серверов Happ: негодный сервер не берётся снова.

Симптом (22.11, жалоба владельца): «серверы VPN в HAPP странно меняются,
всё время один и тот же сервер первый выбирается, потом всё зависает, а
дальше серверы не меняются».

Проверяем на настоящем модуле (подменяем реестр Happ, пробы и состояние):
  1) помеченный негодным сервер выборщик пропускает;
  2) если помечены все — пометки сбрасываются (иначе переключаться не на что);
  3) старая пометка (TTL истёк) сервер не исключает;
  4) перебор кандидатов: негодный кандидат помечается и берётся следующий.

Запуск: python3 tools/minimax-project-areas/test_minimax_netwatch_switch.py
"""
import importlib.util
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
BIN = HERE  # модуль лежит рядом


def load_netwatch():
    spec = importlib.util.spec_from_file_location(
        "nw_under_test", str(HERE / "minimax-netwatch.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    nw = load_netwatch()
    # Состояние — во временный файл: тест не трогает боевое.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    nw.core.NET_STATE = pathlib.Path(tmp.name)

    def fake_candidates():
        return [
            {"name": "DE-1", "host": "de1.test", "port": 8443},
            {"name": "NL-1", "host": "nl1.test", "port": 8443},
            {"name": "PL-1", "host": "pl1.test", "port": 8443},
        ]

    nw.happ_candidates = fake_candidates

    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print("%s %s: %r (ждали %r)" % ("[ок]" if good else "[!!]", name, got, want))

    # 1) Текущий сервер — nl1.test; первый кандидат de1.test.
    check("кандидат по умолчанию", nw.pick_candidate("nl1.test").get("host"), "de1.test")

    # 2) Помечаем de1.test негодным → выборщик идёт дальше.
    state = nw.core.read_state(nw.core.NET_STATE)
    nw.mark_bad_server("de1.test", state, "тест")
    check("негодный пропущен", nw.pick_candidate("nl1.test").get("host"), "pl1.test")

    # 3) Помечаем все → пометки сбрасываются, берётся первый.
    state = nw.core.read_state(nw.core.NET_STATE)
    nw.mark_bad_server("pl1.test", state, "тест")
    check("все помечены → сброс и первый снова",
          nw.pick_candidate("nl1.test").get("host"), "de1.test")

    # 4) Старая пометка (TTL истёк) сервер не исключает.
    state = nw.core.read_state(nw.core.NET_STATE)
    state["bad_servers"] = ["de1.test"]
    state["bad_servers_at"] = {"de1.test": int(time.time()) - 7 * 3600}
    nw.core.write_state(nw.core.NET_STATE, state)
    check("старая пометка не мешает", nw.pick_candidate("nl1.test").get("host"), "de1.test")

    # 5) Перебор кандидатов: негодный кандидат помечается, берётся следующий.
    state = nw.core.read_state(nw.core.NET_STATE)
    nw.core.write_state(nw.core.NET_STATE, state)
    reg = {"lastServerHost": "nl1.test", "lastServerName": "NL-1", "lastServerPort": 8443}
    nw.reg_read = lambda: dict(reg)
    nw.reg_write = lambda new: (reg.update(new), True)[1]
    nw.reg_delete = lambda keys: True
    nw.happ_scheme = lambda what: True
    nw.rollback_happ_server = lambda old: None
    probes = {"de1.test": {"alive": False, "country": "-", "note": "нет ответа"},
              "pl1.test": {"alive": True, "country": "NL", "note": "путь живой"}}
    nw.probe_path = lambda p, cfg: probes.get(
        reg.get("lastServerHost"), {"alive": False, "note": "нет ответа"})
    cfg = {"paths": [{"name": "happ", "proxy": "http://127.0.0.1:10808",
                      "expect_country": "NL"}]}
    res = nw.switch_happ_server_verified(cfg, state, tries=3)
    check("перебор дошёл до живого", res.get("to"), "pl1.test")
    check("негодный помечен", "de1.test" in nw.bad_servers(), True)
    check("последняя попытка — живой сервер", (res.get("tried") or [])[-1], "pl1.test")
    check("попыток больше одной", len(res.get("tried") or []) >= 2, True)

    print("\nИТОГ: %s" % ("правка ведёт себя как задумано" if ok else "ЕСТЬ РАСХОЖДЕНИЯ"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
