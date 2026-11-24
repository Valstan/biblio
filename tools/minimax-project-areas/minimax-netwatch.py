"""МиниГен — сетевой сторож: видит, что интернет или доступ к модели отвалились,
и возвращает связь сам.

Задача (заказ владельца 21.11.2026): «добавь в Миниген, чтобы он умел
определять, что интернет сломался, что-то с ним не так, пакеты не идут,
десктопы минимакса остановились; и чтобы миниген мог сам менять сервер ВПН
в программе HAPP и проверять снова интернет, появился ли устойчивый доступ
к модели минимакса и что десктопы минимакса вновь начали работу».

Что делает демон (раз в `interval_sec`, по умолчанию 3 минуты):

1. **Проба путей.** Известные выходы: `happ` (прокси Happ, 10808) и `reserve`
   (свой резервный туннель, 11081). Проба — HTTP-запрос к панели MiniMax
   через конкретный прокси: ЛЮБОЙ код ответа (даже 400) означает «путь живой»,
   000 — «пакеты не идут».
2. **Проба вендора.** Авторизованный запрос к `agent.minimax.io` (панель
   входа) токеном живой области: отделяет «сеть упала» от «аккаунт/токен
   мёртв» (второе лечится входом, а не VPN).
3. **Гео выхода.** Через `ipinfo.io` смотрим страну и IP выхода. Это ловит
   самый неприятный случай: нода умерла, Happ ушёл «напрямую», трафик идёт
   с российского домашнего IP — связь формально есть, а модель отвечает
   гео-отказом («not available in your country»).
4. **Каскад восстановления** (шаг принимается только после успешной пробы):
   1. `reconnect` — `happ://disconnect` + `happ://connect` (схема Happ, без
      прав админа): переподключение на том же сервере;
   2. `switch_happ_server` — смена сервера **в Happ**: пишем в реестр
      `lastServer*` (Happ хранит выбранный сервер именно там) и переподключаем;
   3. `switch_path` — уход с пути Happ на резервный туннель (11081) или обратно;
   4. `rotate_reserve` — смена апстрима резервного туннеля между своими
      REALITY-серверами;
   5. `restart_core` — перезапуск ядра Happ, если порт не слушается.
5. **Проверка устойчивости.** «Доступ появился» — это не одна удачная проба,
   а `stable_probes` успешных проб подряд с интервалом `stable_gap_sec` плюс
   живая проба вендора. Только после этого пишем «доступ устойчив».
6. **Ответственный за сессии.** После восстановления сети поднимаем
   сессии, которые упали из-за сети: ставим метку времени восстановления в
   файл состояния, сторож сессий (`minimax-sessionwatch.py`) её видит и
   отправляет «Продолжай» именно тем сессиям, что падали по сети.

Границы (важно):
- **Ничего не ломаем «на всякий случай».** Каждый шаг каскада сначала
  проверяется пробой; если после шага стало хуже — откат на предыдущий
  рабочий путь.
- **Не трогаем то, что не сломано.** Смена сервера Happ идёт только если
  выбранный сервер действительно не отвечает.
- **Токены и аккаунты не трогаем.** Пул Кармана — забота лечащего
  (`minimax-autheal.py`); здесь только сеть.
- **Вход в Happ — только у того, кто уже вошёл.** Наш файловый кандидат
  (`happ-servers.json`) читается, если есть; подписка не нужна.

Запуск:
    python C:\\Users\\valstan\\bin\\minimax-netwatch.py --status
    python C:\\Users\\valstan\\bin\\minimax-netwatch.py --once --dry-run
    python C:\\Users\\valstan\\bin\\minimax-netwatch.py --recover
    python C:\\Users\\valstan\\bin\\minimax-netwatch.py          # демон
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minigen_core as core  # noqa: E402

try:
    import winreg
except ImportError:      # pragma: no cover — на Windows всегда есть
    winreg = None

HAPP_PREF_KEY = r"Software\Happ\OrganizationDefaults\Preferences"
HAPP_SERVERS_FILE = core.BIN / "happ-servers.json"       # кандидаты серверов (локальный файл)
SUBS_LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "Happ" / "logs" / "subscription_log.txt"

# ── НАШ СОБСТВЕННЫЙ ТОННЕЛЬ (резерв MiniMax) ─────────────────────────────
# Живёт в папке владельца `C:\Users\valstan\vpn`: своё ядро `xtunnel.exe`
# (переименовано, чтобы Happ его не убивал — плейбук
# `vpn-server-happ-opencode-tunnel.md` §7), входы 11080/11081.
# ⚠️ 23.11: порты в тоннеле были перееханы на 13090/13091, а и МиниГен, и
# скрипты владельца ждали 11080/11081 ⇒ «резерв» не поднимался НИКОГДА, и при
# смерти Happ все области MiniMax оставались без сети.
RESERVE_DIR = Path(r"C:\Users\valstan\vpn")
RESERVE_XRAY = RESERVE_DIR / "xray26327" / "xtunnel.exe"
RESERVE_CONFIG = RESERVE_DIR / "config.json"
RESERVE_UPSTREAMS = RESERVE_DIR / "upstreams"   # набор реальных апстримов (*.json)
RESERVE_SOCKS_PORT = 11080
RESERVE_HTTP_PORT = 11081

RECOVER_COOLDOWN = 600      # секунд между попытками восстановления
# Негодный сервер Happ возвращается в список через 6 ч (нода могла подняться).
BAD_SERVER_TTL_S = 6 * 3600
STEP_COOLDOWN = 120         # секунд между шагами каскада
VENDOR_EVERY = 10           # как часто проверять вендора «по расписанию» (иначе — по событию)


# ────────────────────────────────────────────────────────── HTTP-помощники

def http_get(url: str, proxy: str = "", timeout: int = 20, headers: dict = None):
    """GET через конкретный прокси (или напрямую, если proxy пустой).

    Возвращает (код, тело). Код 0 — ответа нет (порт закрыт, таймаут, DNS).
    """
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "minigen-netwatch/1.0",
        "Accept": "*/*",
    }, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


# ────────────────────────────────────────────────────────── пробы

def probe_path(path_cfg: dict, cfg: dict) -> dict:
    """Проба одного выхода: жив ли путь и откуда он выходит."""
    out = {"name": path_cfg.get("name"), "proxy": path_cfg.get("proxy"),
           "role": path_cfg.get("role"), "alive": False, "code": 0,
           "country": "", "ip": "", "note": ""}
    code, _ = http_get(cfg["probe_url"], out["proxy"], timeout=20)
    out["code"] = code
    out["alive"] = bool(code) and code != 0
    if not out["alive"]:
        out["note"] = "нет ответа (000) — пакеты не идут"
        return out
    gcode, body = http_get(cfg["geo_url"], out["proxy"], timeout=15)
    if gcode:
        try:
            geo = json.loads(body)
            out["country"] = str(geo.get("country") or "")
            out["ip"] = str(geo.get("ip") or "")
        except ValueError:
            pass
    expect = (path_cfg.get("expect_country") or "").upper()
    if out["country"] and expect and out["country"].upper() != expect:
        # ⚠️ Несовпадение страны САМО ПО СЕБЕ — не поломка (23.11). Вендор
        # Happ отдаёт разные серверы (NL/DE/AT/PL/CA…), и «ожидаемая страна»
        # в конфиге устаревает. Прежний вывод «нода упала, трафик ушёл напрямую»
        # приводил к каскаду восстановления (смена сервера → перезапуск ядра
        # Happ → прокси на 30 с умирал) ⇒ в десктопах «The model service is
        # unstable. Please try again.». Проверять надо не страну, а ЖИВ ЛИ ПУТЬ.
        out["note"] = ("страна выхода %s вместо ожидаемой %s — вендор по этому пути "
                       "отвечает (%s), значит нода жива, это просто другой сервер"
                       % (out["country"], expect, out["code"]))
        out["country_mismatch"] = True
    else:
        out["note"] = "путь живой"
    return out


_switch_mod = {}


def switch_module():
    """`minimax-switch.py` — единственная реализация механики входа (подключаем модулем)."""
    if "mod" not in _switch_mod:
        import importlib.util
        spec = importlib.util.spec_from_file_location("minimax_switch", str(core.BIN / "minimax-switch.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _switch_mod["mod"] = mod
    return _switch_mod["mod"]


def pick_token_area():
    """Область с самым свежим токеном — для пробы вендора (панели входа)."""
    best = None
    best_exp = 0
    mod = switch_module()
    for area in core.areas():
        if not area["dir"].is_dir():
            continue
        try:
            rec = (mod.read_store(area["dir"]).get("records") or {}).get(mod.store_key(area["dir"])) or {}
            exp = int(rec.get("expiresAtMs") or rec.get("expires_at_ms") or 0)
            token = rec.get("accessToken") or ""
            if token and exp > best_exp:
                best, best_exp = (area, token, mod), exp
        except Exception:  # noqa: BLE001
            continue
    return best


def probe_vendor() -> dict:
    """Живая проба вендора: панель входа MiniMax авторизованным токеном.

    Отвечает на вопрос «доступ к модели есть или нет» в смысле канала:
    сеть + токен. Отказ по очкам (50110) сюда не попадает — это лечащий.
    """
    out = {"ok": False, "code": 0, "note": "нет области с токеном"}
    found = pick_token_area()
    if not found:
        return out
    area, token, mod = found
    url = "%s/status?timezone_id=%s" % (mod.PANEL_BASE, mod.SIGNIN_TZ)
    # Пробуем через активный прокси процесса (тот, которым реально ходят
    # десктопы), и — если не вышло — напрямую (как это делает сам switch).
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    code, body = http_get(url, proxy, timeout=20,
                           headers={"Authorization": "Bearer " + token,
                                    "Accept": "application/json"})
    if not code:
        code, body = http_get(url, "", timeout=20,
                              headers={"Authorization": "Bearer " + token,
                                       "Accept": "application/json"})
    out["code"] = code
    out["area"] = area["slug"]
    if code == 200:
        out["ok"] = True
        out["note"] = "вендор отвечает"
    elif code in (401, 403):
        out["note"] = "вендор отвечает, но токен не принят (401/403) — это вход, не сеть"
    elif code:
        out["note"] = "вендер ответил кодом %s" % code
    else:
        out["note"] = "вендор не отвечает — сеть или вендор недоступны"
    return out


# ────────────────────────────────────────────────────────── шаги восстановления

def reg_read() -> dict:
    """Текущий выбранный сервер Happ (из реестра)."""
    if winreg is None:
        return {}
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HAPP_PREF_KEY) as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                out[name] = value
    except OSError:
        pass
    return out


def reg_write(values: dict) -> bool:
    if winreg is None:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, HAPP_PREF_KEY) as key:
            for name, value in values.items():
                if isinstance(value, int):
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
        return True
    except OSError as exc:
        core.log("реестр Happ: записать не вышло (%s)" % exc)
        return False


def happ_scheme(action: str) -> bool:
    """`happ://disconnect` / `happ://connect` — штатная схема Happ.

    Права админа не нужны: соединение и разрыв делает сам Happ.
    """
    try:
        os.startfile("happ://" + action)
        return True
    except Exception as exc:  # noqa: BLE001
        core.log("схема Happ (%s) не сработала: %s" % (action, exc))
        return False


def machine_hwid() -> str:
    """Windows MachineGuid — им Happ подписывает запрос (заголовок X-HWID).

    Провайдер подписки NextGenVPN отдаёт список серверов **только** с этим
    заголовком: без него — 404 (проверено 22.11: с `X-HWID: <MachineGuid>`
    приходит 200 и 8898 байт, без него — 404 и 0 байт).
    """
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except OSError:
        return ""


def fetch_happ_servers() -> list:
    r"""Список серверов Happ: тянем его подписку и разбираем.

    Кэш — `%LOCALAPPDATA%\mcode-web\happ-servers.json` (12 ч).
    """
    cache = core.LOCAL / "happ-servers.json"
    cached = core.read_state(cache)
    if cached.get("servers"):
        if core.now_ms() - int(cached.get("at") or 0) < 12 * 3600 * 1000:
            return cached["servers"]
    url = subscription_url()
    hwid = machine_hwid()
    if not url or not hwid:
        return (cached.get("servers") or [])
    try:
        req = urllib.request.Request(url, headers={"X-HWID": hwid,
                                                   "User-Agent": "Happ/1.6.16"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        core.log("СЕТЬ: подписку Happ не прочитать (%s) — беру кэш" % exc)
        return (cached.get("servers") or [])
    servers = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(vless|vmess|trojan|ss)://[^@]*@([^/?#]+)\?[^#]*#?(.*)$", line)
        if not m:
            continue
        scheme, hp, frag = m.groups()
        host, _, port = hp.rpartition(":")
        name = urllib.parse.unquote(frag) if frag else ""
        servers.append({"name": name, "host": host.strip("[]"), "port": port,
                        "scheme": scheme})
    if servers:
        core.write_state(cache, {"at": core.now_ms(), "servers": servers})
        core.log("СЕТЬ: подписка Happ прочитана — серверов: %d" % len(servers))
    return servers or (cached.get("servers") or [])


def reg_delete(names) -> None:
    """Убрать значения из реестра Happ."""
    if winreg is None:
        return
    for name in names:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HAPP_PREF_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        except OSError:
            pass


def port_listening(port: int) -> bool:
    """Слушает ли локальный порт.

    ⚠️ Раньше проверка жила в PowerShell (`Get-NetTCPConnection`) — и **каждая
    проверка мигала окном** на экране владельца (23.11, жалоба: «окна
    терминала постоянно появляются и закрываются»). Причина: демон запущен
    отсоединённо, консоли у него нет, а дочерний `powershell` без
    `CREATE_NO_WINDOW` получает **свою** консоль. За проход сторожа таких
    проверок десятки, и шли они пачками.

    Теперь проверка чистая, на сокетах: connect к локальному порту отвечает ⇔
    порт слушают. Никаких процессов, никаких окон, и вместо ~0,5 с на
    проверку — миллисекунды.

    Отличие от `Get-NetTCPConnection -State Listen`: connect спрашивает
    «примет ли кто-то соединение», а не «есть ли сокет в состоянии LISTEN».
    Для наших портов (прокси Happ и наш резервный тоннель — оба на
    127.0.0.1) это одно и то же.

    Таймаут 0,3 с: слушающий порт на loopback отвечает мгновенно (рукопожатие
    делает ядро), а вот на свободный порт Windows отвечает отказом либо
    (если правило файрвола глушит) таймаутом — и вот тут важно не ждать дольше
    нужного: за проход сторожа проверяется несколько портов.
    """
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            return True
    except (OSError, ValueError):
        return False


def happ_candidates() -> list:
    """Список серверов Happ для переключения.

    Формат элемента: {"name": ..., "host": ..., "port": ...} — ровно то, что
    Happ пишет в реестр (`lastServerName/Host/Port`).
    """
    return [s for s in (fetch_happ_servers() or []) if s.get("host")]


def subscription_url() -> str:
    """URL подписки из лога Happ (секрет в git не попадает — читаем локально)."""
    try:
        txt = SUBS_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    urls = re.findall(r"https?://[^\s\"]+ngvpnsub/[0-9a-f-]+", txt)
    return urls[-1] if urls else ""


def bad_servers(state: dict = None) -> set:
    """Серверы Happ, помеченные негодными (отметка ещё свежая).

    Пометка живёт TTL: нода могла подняться, и через пару часов сервер
    снова годится. Старые пометки в список не возвращают.
    """
    state = state if state is not None else core.read_state(core.NET_STATE)
    hosts = list(state.get("bad_servers") or [])
    at = state.get("bad_servers_at") or {}
    now = int(time.time())
    out = set()
    for h in hosts:
        marked = int(at.get(h) or 0)
        if marked and (now - marked) < BAD_SERVER_TTL_S:
            out.add(h)
    return out


def mark_bad_server(host: str, state: dict, why: str = "") -> None:
    """Пометить сервер Happ негодным — иначе выборщик снова возьмёт его первым.

    22.11: список `bad_servers` читался, но не заполнялся — потому негодный
    сервер снова оказывался первым кандидатом, и сеть зависала по кругу.
    Пометку пишем сразу в файл состояния: выборщик читает его из файла.
    """
    if not host:
        return
    hosts = [h for h in (state.get("bad_servers") or []) if h != host]
    hosts.append(host)
    state["bad_servers"] = hosts[-20:]
    at = dict(state.get("bad_servers_at") or {})
    at[host] = int(time.time())
    state["bad_servers_at"] = {k: v for k, v in at.items() if k in state["bad_servers"]}
    try:
        core.write_state(core.NET_STATE, state)
    except OSError:
        pass
    core.log("СЕТЬ: сервер Happ %s помечен негодным (%s) — следующим возьмём другой"
             % (host, why or "без причины"))


def pick_candidate(current_host: str) -> dict:
    r"""Кандидат на смену: первый из подписки Happ, кто не текущий и не в «плохом» списке.

    Источник — подписка самого Happ (`X-HWID`), кэш в
    `%LOCALAPPDATA%\mcode-web\happ-servers.json`; раньше список лежал в
    зашифрованной `subs.db` и был недоступен.
    """
    bad = bad_servers()
    cands = [s for s in happ_candidates()
             if s.get("host") != current_host and s.get("host") not in bad]
    if not cands and bad:
        # Все помечены — сбрасываем пометки: иначе переключаться больше не на что.
        state = core.read_state(core.NET_STATE)
        state["bad_servers"] = []
        state["bad_servers_at"] = {}
        try:
            core.write_state(core.NET_STATE, state)
        except OSError:
            pass
        core.log("СЕТЬ: все серверы помечены негодными — пометки сброшены, пробуем снова")
        cands = [s for s in happ_candidates() if s.get("host") != current_host]
    return cands[0] if cands else {}


def switch_happ_server() -> dict:
    """Смена сервера **в Happ** через реестр + переподключение.

    Happ хранит выбранный сервер в `HKCU\\Software\\Happ\\...\\Preferences`
    (`lastServer*`). Меняем эти значения на кандидата и переподключаем
    Happ штатной схемой (`happ://disconnect` → `happ://connect`).

    ⚠️ Проверка обязательна: если после смены выход не изменился или стал
    хуже — возвращаем прежний сервер и пишем это в журнал.
    """
    cur = reg_read()
    cur_host = str(cur.get("lastServerHost") or "")
    srv = pick_candidate(cur_host)
    if not srv:
        return {"ok": False, "why": "нет списка кандидатов (happ-servers.json) — сервер переключать нечем"}
    old = {k: cur.get(k) for k in
           ("lastServer", "lastServerName", "lastServerGuid", "lastServerHost", "lastServerPort")}
    new = {
        "lastServerName": srv.get("name") or cur.get("lastServerName"),
        "lastServerHost": srv.get("host"),
        "lastServerPort": srv.get("port") or cur.get("lastServerPort"),
    }
    if not reg_write(new):
        return {"ok": False, "why": "реестр Happ не пишется"}
    # `lastServerGuid`/`lastServer` — внутренние идентификаторы Happ; чужие
    # значения только мешают. Убираем ⇒ Happ ищет сервер по имени и хосту.
    reg_delete(("lastServerGuid", "lastServer"))
    happ_scheme("disconnect")
    time.sleep(3)
    happ_scheme("connect")
    time.sleep(8)
    return {"ok": True, "from": cur_host, "to": new["lastServerHost"], "old": old, "new": new}


def switch_happ_server_verified(cfg: dict, state: dict, tries: int = 10) -> dict:
    """Смена сервера Happ С ПРОВЕРКОЙ: негодные серверы уходят в «плохой» список.

    22.11 (находка владельца): сервер всегда выбирался первым из подписки, а
    список негодных не вёлся ⇒ после смены каскад снова брал тот же сервер,
    сеть зависала, и дальше серверы не менялись. Теперь каждый кандидат
    проверяется: не ожил — помечается негодным и пропускается, пока не
    найдётся живой.

    ⚠️ 23.11 (жалоба владельца: «он один раз поменяет сервер и затухает,
    дальше не меняет»): попыток было **3** при 23 серверах в подписке, из
    которых половина дохлая ⇒ каскад often исчерпывал попытки, не найдя
    живого. Теперь попыток 10. И главное: **перед пробой ждём готовности
    порта** — раньше проверка шла через фиксированные 11 с, и сервер, который
    поднимался чуть дольше, честно помечался «плохим» (ложное обвинение).
    """
    paths = {p.get("name"): p for p in (cfg.get("paths") or [])}
    tried = []
    for _ in range(max(1, tries)):
        res = switch_happ_server()
        if not res.get("ok"):
            res["tried"] = tried
            return res
        host = res.get("to") or ""
        tried.append(host)
        # Порт может подниматься дольше 11 с — ждём его, иначе живой сервер
        # будет помечен «плохим» без причины.
        _wait_port(proxy_port((paths.get("happ") or {}).get("proxy") or ""), 45)
        probe = probe_path(paths.get("happ") or {}, cfg)
        # ⚠️ 23.11: страна выхода **не** приговор (вендер Happ отдаёт серверы
        # разных стран: NL/DE/AT/PL/US/CA). Раньше после смены требовалась
        # именно ожидаемая страна ⇒ живой сервер из другой страны честно
        # помечался «плохим», список плохих рос, и переключаться было некуда
        # (жалоба владельца: «смена сервера не сработал, а он следующий не
        # пытается»). Теперь критерий один: **вендор отвечает по этому пути**.
        if probe.get("alive"):
            core.log("СЕТЬ: сервер Happ переключён на %s (выход %s)"
                     % (host, probe.get("country") or "?"))
            return {"ok": True, "from": res.get("from"), "to": host, "tried": tried}
        mark_bad_server(host, state, "после смены путь не ожил: %s"
                        % (probe.get("note") or probe.get("country") or "нет ответа"))
        core.log("СЕТЬ: сервер %s не ожил — помечен негодным, пробую следующий" % host)
    return {"ok": False, "why": "ни один сервер Happ не ожил (%d попыток: %s)"
            % (len(tried), ", ".join(tried)), "tried": tried}


def rollback_happ_server(old: dict) -> None:
    """Вернуть прежний сервер Happ (если смена оказалась хуже)."""
    if not old:
        return
    reg_write({k: v for k, v in old.items() if v is not None})
    happ_scheme("disconnect")
    time.sleep(3)
    happ_scheme("connect")


def set_proxy_env(http_proxy: str, socks_proxy: str) -> None:
    """Прописать прокси в окружение пользователя + системный прокси Windows.

    ⚠️ Старый процесс новых переменных не наследует — поэтому после смены
    пути контур «МиниГена» перезапускается (см. `restart_contour`).
    """
    if winreg is None:
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            winreg.SetValueEx(key, "HTTP_PROXY", 0, winreg.REG_SZ, http_proxy)
            winreg.SetValueEx(key, "HTTPS_PROXY", 0, winreg.REG_SZ, http_proxy)
            winreg.SetValueEx(key, "ALL_PROXY", 0, winreg.REG_SZ, socks_proxy)
            winreg.SetValueEx(key, "NO_PROXY", 0, winreg.REG_SZ, "localhost,127.0.0.1,::1")
    except OSError:
        pass
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, http_proxy)
    except OSError:
        pass
    # Свой процесс — чтобы следующие `mcode exec` (из этого же демона)
    # пошли по новому пути, а не по старому.
    os.environ["HTTP_PROXY"] = http_proxy
    os.environ["HTTPS_PROXY"] = http_proxy
    os.environ["ALL_PROXY"] = socks_proxy
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    # Файл-подсказка для супервизора: он запускает панель, лечащего и
    # сторожей, и они должны унаследовать новые значения, а не старые.
    try:
        core.LOCAL.mkdir(parents=True, exist_ok=True)
        (core.LOCAL / "proxy-env.json").write_text(json.dumps({
            "HTTP_PROXY": http_proxy, "HTTPS_PROXY": http_proxy,
            "ALL_PROXY": socks_proxy, "NO_PROXY": "localhost,127.0.0.1,::1",
            "at": int(time.time()),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def contour_targets() -> list:
    """К кому звать за перезапуском контура (по убыванию предпочтения).

    1. **Программа «МиниГен»** (локальный API :8097) — нынешний
       процесс-менеджер демонов. С 23.11 (Ф6) супервизор старой панели
       архивирован, и звать его больше некуда: каскад восстановления сети
       переключал прокси, но контур не перезапускался — лечащий и сторож
       сессий продолжали работать со СТАРЫМИ прокси-переменными и ходили по
       мёртвому пути.
    2. **Старый супервизор** (:8098) — запасной путь для машин, где старая
       панель ещё работает.

    Токен — из файла, который завела программа; без него запрос не
    принимается (случайная веб-страница не должна перезапускать демоны).
    """
    targets = []
    token = ""
    try:
        from pathlib import Path
        token_file = (Path(os.environ.get("LOCALAPPDATA", "")) / "minigen-gui"
                       / "bridge-token.txt")
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        targets.append(("http://127.0.0.1:8097/restart-contour",
                        {"X-MiniGen-Token": token}))
    targets.append(("http://127.0.0.1:8098/restart-contour", {}))
    return targets


def restart_contour() -> bool:
    """Перезапустить контур (лечащий и сторож сессий) — им нужен новый прокси.

    Процессы, запущенные до смены прокси, наследуют старые переменные
    окружения. Сам сетевой сторож не трогаем — он на середине восстановления.
    """
    last = ""
    for url, headers in contour_targets():
        try:
            # Именно POST: программа различает методы (/status — чтение,
            # /restart-contour — действие). Старый супервизор отвечал на
            # любой метод — на этом и подвох: GET на действие молча уходил
            # в 404, и цепочка падала, не досказав почему.
            req = urllib.request.Request(url, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=30).read()
            core.log("СЕТЬ: контур перезапущен (%s)" % url)
            return True
        except Exception as exc:  # noqa: BLE001
            last = "%s (%s)" % (url, exc)
    core.log("перезапуск контура не удался: %s" % last)
    return False


def switch_path(to: str, cfg: dict) -> dict:
    """Переключить рабочий путь: `happ` (10808) или `reserve` (11081).

    Правило выбора: для MiniMax нужен **иностранец** (российский egress →
    гео-отказ модели). Поэтому резерв (наш тоннель, RU) — запасной, а не
    первый.

    ⚠️ 23.11: переключаться на путь, который не отвечает, нельзя — тогда
    MiniMax теряет связь совсем (все области стоят). Поэтому ПЕРЕД переключением
    проверяем, что целевой путь живой, и поднимаем резерв, если он мёртв.
    """
    paths = {p.get("name"): p for p in cfg.get("paths") or []}
    target = paths.get(to)
    if not target:
        return {"ok": False, "why": "нет такого пути в конфиге"}
    proxy = target.get("proxy") or ""
    socks = proxy.replace("http://", "socks5://")
    if to == "reserve" and not port_listening(RESERVE_HTTP_PORT):
        res = ensure_reserve()
        if not res.get("ok"):
            return {"ok": False, "why": "резерв не поднялся: %s" % res.get("why")}
    code, _ = http_get(cfg_probe_url(), proxy, timeout=20)
    if not code:
        return {"ok": False, "why": "путь %s не отвечает — не переключаюсь" % to}
    set_proxy_env(proxy, socks)
    restart_contour()
    core.log("СЕТЬ: путь переключён на %s (%s) — вендор отвечает" % (to, proxy))
    return {"ok": True, "path": to, "proxy": proxy}


def rotate_reserve() -> dict:
    """Сменить апстрим НАШЕГО тоннеля (резерв MiniMax, порты 11080/11081).

    Набор апстримов — `C:\\Users\\valstan\\vpn\\upstreams\\*.json` (реальные
    конфиги). ⚠️ Раньше здесь перебирались `config*.json` из папки тоннеля —
    а это **бэкапы** (`config.json.bak-…`), то есть мусор: подставлялся
    «апстрим», которого нет, и резерв никогда не оживал.

    Плюс главное: наш тоннель должен быть **поднят всегда** (см.
    `ensure_reserve`), чтобы переключение на него было мгновенным — «время
    до воли» не должно зависеть от того, успели ли мы поднять резерв.
    """
    upstreams = sorted(RESERVE_UPSTREAMS.glob("*.json")) if RESERVE_UPSTREAMS.is_dir() else []
    if not upstreams:
        return {"ok": False, "why": "нет набора апстримов (%s)" % RESERVE_UPSTREAMS}
    if not RESERVE_XRAY.is_file():
        return {"ok": False, "why": "нет своего xray (%s)" % RESERVE_XRAY}
    for alt in upstreams:
        try:
            shutil.copy2(alt, RESERVE_CONFIG)
        except OSError as exc:
            return {"ok": False, "why": "подменить конфиг не вышло: %s" % exc}
        _restart_reserve_tunnel()
        if not _wait_port(RESERVE_HTTP_PORT, 30):
            core.log("СЕТЬ: апстрим %s — тоннель не поднялся" % alt.name)
            continue
        code, _ = http_get(cfg_probe_url(), "http://127.0.0.1:%d" % RESERVE_HTTP_PORT, timeout=20)
        if code:
            core.log("СЕТЬ: наш тоннель переведён на апстрим %s — вендор отвечает" % alt.name)
            return {"ok": True, "upstream": alt.name}
        core.log("СЕТЬ: апстрим %s не ожил — пробую следующий" % alt.name)
    return {"ok": False, "why": "ни один апстрим нашего тоннеля не ожил"}


def _restart_reserve_tunnel() -> None:
    """Перезапустить наш тоннель (xtunnel) с активным конфигом."""
    # ⚠️ CREATE_NO_WINDOW обязателен: демон идёт отсоединённо (консоли нет),
    # а дочерний powershell без этого флага получает свою консоль — на экране
    # мигает окно (23.11).
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process xtunnel -ErrorAction SilentlyContinue | "
                    "Stop-Process -Force -ErrorAction SilentlyContinue"],
                   capture_output=True, text=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(2)
    try:
        subprocess.Popen([str(RESERVE_XRAY), "-config", str(RESERVE_CONFIG)],
                         cwd=str(RESERVE_DIR), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        core.log("СЕТЬ: свой тоннель не запустился: %s" % exc)


def _wait_port(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_listening(port):
            return True
        time.sleep(2)
    return port_listening(port)


def ensure_reserve() -> dict:
    """Держать наш собственный тоннель (резерв MiniMax) поднятым.

    Зачем (23.11, заказ владельца: «сделать один прокси для MiniMax и не
    включать вообще ВПН… или прокси для MiniMax оставался рабочим»): когда
    Happ-путь умирает, MiniMax должен уйти на НАШ тоннель мгновенно, а не
    ждать, пока что-то поднимется. Значит, резерв поднят **заранее** и
    ждёт своего часа.

    ⚠️ Честно о роли резерва: наш тоннель выходит в **РФ**, а MiniMax для
    агента нужен иностранный egress (гео-отказ модели — плейбук
    `vpn-server-happ-opencode-tunnel.md` §9). То есть резерв — это «связь
    жива», а не «модель ответит»; основной путь остаётся Happ.
    """
    if port_listening(RESERVE_HTTP_PORT):
        return {"ok": True, "why": "резерв уже слушает %d" % RESERVE_HTTP_PORT}
    if not RESERVE_XRAY.is_file() or not RESERVE_CONFIG.is_file():
        return {"ok": False, "why": "нет своего тоннеля (%s)" % RESERVE_XRAY}
    core.log("СЕТЬ: резерв (%d) не слушает — поднимаю свой тоннель" % RESERVE_HTTP_PORT)
    _restart_reserve_tunnel()
    if _wait_port(RESERVE_HTTP_PORT, 30):
        core.log("СЕТЬ: свой тоннель поднят, резерв %d готов" % RESERVE_HTTP_PORT)
        return {"ok": True, "why": "тоннель поднят"}
    return {"ok": False, "why": "свой тоннель не поднялся"}


def cfg_probe_url() -> str:
    return core.net_config().get("probe_url",
                                  "https://agent.minimax.io/minimax-cloud/api/v1/signin/status")


# ────────────────────────────────────────────────────────── мёртвый прокси

def proxy_port(url: str) -> int:
    """Порт из URL прокси (0 — разобрать не удалось)."""
    try:
        tail = url.split("://", 1)[-1]
        hostport = tail.split("/")[0]
        return int(hostport.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return 0


def proxy_state() -> dict:
    """Состояние прокси-переменных: где указывают и живы ли эти порты.

    ⚠️ Смотрим **два** источника, потому что ломается именно расхождение:
    в реестре (`HKCU\\Environment`) прокси может быть верным, а процессы —
    держать старые значения, унаследованные от момента запуска. Тогда
    `mcode` падает с `fetch failed` / «Connection error», хотя сеть и вендор
    живы (замер 21.11: в реестре 10808, в процессах 10809 — порт не слушается).

    Это **самая частая** поломка на PC79, и снаружи она выглядит как
    «интернет сломался».
    """
    reg = core.user_proxy_env()
    proc = {k: os.environ.get(k, "") for k in
            ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")}
    out = {"registry": reg, "process": proc, "dead": [], "broken": False,
           "stale_process": False}
    for src, values in (("registry", reg), ("process", proc)):
        ports = {}
        for label, key in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY"),
                           ("socks", "ALL_PROXY")):
            url = values.get(key) or ""
            port = proxy_port(url)
            ports[label] = {"url": url, "port": port,
                            "listening": bool(port) and port_listening(port)}
        out[src + "_ports"] = ports
        for label, info in ports.items():
            if info["url"] and not info["listening"]:
                out["dead"].append("%s:%s" % (src, label))
    # Расхождение процесс ↔ реестр: процесс держит старые значения.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if proc.get(key) and reg.get(key) and proc[key] != reg[key]:
            out["stale_process"] = True
            out["dead"].append("process:%s" % key)
    # ⚠️ Важно (22.11): «сломаны переменные» и «сеть сломана» — РАЗНЫЕ вещи.
    # Сеть сломана, только если мёртв прокси В РЕЕСТРЕ (его читают новые
    # процессы). Расхождение «реестр ↔ процесс» — устаревшие переменные у уже
    # работающих процессов: их надо освежить, но сеть при этом ЖИВА, и лечащему
    # нельзя говорить «сеть не в порядке» (иначе он откладывает живые проверки
    # и видит ложное «подозрение» — так было в 00:18:55).
    reg_dead = [k for k, v in out["registry_ports"].items() if v["url"] and not v["listening"]]
    out["registry_dead"] = reg_dead
    out["broken"] = bool(reg_dead) or not reg.get("HTTP_PROXY")
    return out


def proxy_candidates(cfg: dict) -> list:
    """Кандидаты на замену: живые прокси по конфигу, сверху — Happ."""
    out = []
    for p in (cfg.get("paths") or []):
        proxy = p.get("proxy") or ""
        port = proxy_port(proxy)
        if not port:
            continue
        socks = proxy.replace("http://", "socks5://")
        out.append({"name": p.get("name"), "http": proxy, "socks": socks,
                    "port": port, "listening": port_listening(port)})
    return out


def repair_proxy(cfg: dict) -> dict:
    """Починить прокси-переменные и перезапустить контур.

    Два случая, по порядку:
    1. **Расхождение процесс ↔ реестр** (замер 21.11): в реестре прокси верный,
       а процессы держат старый мёртвый порт ⇒ берём значения из реестра,
       накладываем на процессы и перезапускаем контур;
    2. **В реестре тоже мёртвый порт** ⇒ выбираем живой путь из конфига,
       пишем его и перезапускаем контур.

    Контур перезапускается потому, что старый процесс новых переменных не
    наследует ([G]: `fetch failed` = мёртвый прокси).
    """
    state = proxy_state()
    if not state["broken"]:
        return {"ok": True, "why": "прокси-переменные в порядке", "changed": False}
    if state.get("stale_process") and not state.get("broken"):
        core.log("СЕТЬ: процессы держат устаревшие прокси-переменные (%s) — освежаю "
                 "(сеть при этом жива)" % ", ".join(state["dead"][:3]))
    else:
        core.log("СЕТЬ: прокси-переменные сломаны (%s) — чиню" %
                 ", ".join(state["dead"] or ["не заданы"]))

    # 1) Реестр живой, процесс отстал — забираем правильные значения.
    reg = state["registry"]
    reg_http = reg.get("HTTP_PROXY") or ""
    if reg_http:
        reg_port = proxy_port(reg_http)
        if reg_port and port_listening(reg_port):
            code, _ = http_get(cfg_probe_url(), reg_http, timeout=20)
            if code:
                set_proxy_env(reg_http, reg.get("ALL_PROXY") or reg_http.replace("http://", "socks5://"))
                core.log("СЕТЬ: процессы переведены на прокси из реестра (%s) — вендор отвечает" % reg_http)
                restart_contour()
                return {"ok": True, "changed": True, "path": "реестр",
                        "http": reg_http, "vendor_code": code}

    # 2) Ищем живой путь и переписываем прокси на него.
    for cand in proxy_candidates(cfg):
        if not cand["listening"]:
            continue
        code, _ = http_get(cfg_probe_url(), cand["http"], timeout=20)
        if not code:
            core.log("СЕТЬ: %s жив, но вендор через него не отвечает — беру следующий" % cand["name"])
            continue
        set_proxy_env(cand["http"], cand["socks"])
        core.log("СЕТЬ: прокси переставлен на %s (%s) — вендор отвечает" % (cand["name"], cand["http"]))
        restart_contour()
        return {"ok": True, "changed": True, "path": cand["name"],
                "http": cand["http"], "socks": cand["socks"], "vendor_code": code}
    return {"ok": False, "why": "нет живого прокси, через который отвечает вендор"}


# ────────────────────────────────────────────────────────── каскад

def recover(cfg: dict, state: dict, dry_run: bool = False) -> dict:
    """Каскад восстановления. Возвращает отчёт о том, что сделано."""
    steps = []
    paths = {p.get("name"): p for p in cfg.get("paths") or []}
    cur = reg_read()
    cur_host = str(cur.get("lastServerHost") or "")

    # 1) Переподключение Happ на том же сервере (дешевле всего, без риска).
    steps.append(("reconnect happ", lambda: happ_scheme("disconnect") or happ_scheme("connect")))
    # 2) Смена сервера в Happ (нужен список кандидатов).
    steps.append(("смена сервера Happ", lambda: switch_happ_server_verified(cfg, state)))
    # 3) Уход на резервный путь.
    steps.append(("переход на резерв 11081", lambda: switch_path("reserve", cfg)))
    # 4) Смена апстрима резерва.
    steps.append(("смена апстрима резерва", lambda: rotate_reserve()))
    # 5) Перезапуск ядра Happ, если порт мёртв.
    steps.append(("перезапуск ядра Happ", lambda: restart_happ_core()))

    plan = list(steps)          # план шагов — отдельно от журнала шагов
    done = []
    for name, fn in plan:
        if dry_run:
            core.log("СЕТЬ: сухой прогон — шаг «%s» не выполняем" % name)
            done.append({"step": name, "dry_run": True})
            continue
        core.log("СЕТЬ: шаг «%s»" % name)
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "why": str(exc)}
        done.append({"step": name, "result": res})
        time.sleep(STEP_COOLDOWN)
        # Проверяем результат шага прежде, чем идти дальше.
        probe = probe_path(paths.get("happ", {}), cfg)
        want = paths.get("happ", {}).get("expect_country")
        if probe.get("alive") and (not want or probe.get("country") == want):
            core.log("СЕТЬ: шаг «%s» вернул связь (выход %s)" % (name, probe.get("country")))
            break
        # Путь не ожил или страна не та — нынешний сервер Happ негоден.
        # Запоминаем: иначе выборщик снова возьмёт его первым (22.11).
        if name == "смена сервера Happ":
            mark_bad_server(str(reg_read().get("lastServerHost") or ""), state,
                            "путь: %s" % (probe.get("note") or probe.get("country") or "не отвечает"))
    return {"steps": done, "current_server": cur_host}


def restart_happ_core() -> bool:
    """Переподключить ядро Happ: `happ://disconnect` → `happ://connect`.

    Права админа не нужны. Службу `HappService` не трогаем: без прав её
    не остановить, а ломать рабочий туннель ради «перезапуска» нельзя.
    """
    happ_scheme("disconnect")
    time.sleep(4)
    happ_scheme("connect")
    for _ in range(12):
        if port_listening(10808):
            return True
        time.sleep(5)
    return False


def verify_stable(cfg: dict, need: int = 0) -> dict:
    """Устойчивость: несколько успешных проб подряд + живая проба вендора."""
    need = need or int(cfg.get("stable_probes") or 3)
    gap = int(cfg.get("stable_gap_sec") or 8)
    paths = cfg.get("paths") or []
    ok_streak = 0
    best = None
    for i in range(need):
        for p in paths:
            probe = probe_path(p, cfg)
            if probe.get("alive"):
                best = probe
                ok_streak += 1
                break
        else:
            ok_streak = 0
        if i < need - 1:
            time.sleep(gap)
    vendor = probe_vendor()
    proxy = proxy_state()
    stable = ok_streak >= need and vendor.get("ok") and not proxy.get("broken")
    return {"stable": stable, "streak": ok_streak, "need": need,
            "path": best, "vendor": vendor, "proxy": proxy}


# ────────────────────────────────────────────────────────── состояние и демон

def cycle(cfg: dict, state: dict, dry_run: bool = False) -> dict:
    """Один проход: прокси → пути → при необходимости восстановление → проверка."""
    paths = cfg.get("paths") or []
    proxy = proxy_state()
    probes = [probe_path(p, cfg) for p in paths]
    alive = [p for p in probes if p.get("alive")]
    report = {"at": core.now_ms(), "probes": probes, "action": "нет", "stable": False,
              "proxy": proxy}

    # 0-бис) Резерв (НАШ тоннель) держим поднятым ЗАРАНЕЕ (23.11, заказ
    # владельца: «сделай один прокси для MiniMax… чтобы прокси для MiniMax
    # оставался рабочим»). Когда Happ-путь умирает, переключение на резерв
    # должно быть мгновенным, а не «сначала поднять, потом переключить».
    if not dry_run and not port_listening(RESERVE_HTTP_PORT):
        rep = ensure_reserve()
        report["reserve"] = rep
        if not rep.get("ok"):
            core.log("СЕТЬ: резерв не поднялся (%s) — при смерти Happ переключаться будет некуда"
                     % rep.get("why"))

    # 0) Мёртвый прокси — самая частая поломка: починить ДО остального.
    if proxy.get("broken"):
        if dry_run:
            report["action"] = "прокси-переменные указывают на мёртвый порт (сухой прогон)"
            core.log("СЕТЬ: прокси-переменные указывают на мёртвый порт (%s) — сухой прогон"
                     % ", ".join(proxy.get("dead") or ["не заданы"]))
        else:
            rep = repair_proxy(cfg)
            report["proxy_repair"] = rep
            report["action"] = "починка прокси"
            if rep.get("ok") and rep.get("changed"):
                state["recovered_at"] = core.now_ms()
                probes = [probe_path(p, cfg) for p in paths]
                alive = [p for p in probes if p.get("alive")]
                report["probes"] = probes
                core.log("СЕТЬ: прокси починен (%s) — связь возвращена" % rep.get("path"))
                core.report("info", "network", "прокси починен (%s) — связь возвращена"
                            % rep.get("path"))
            else:
                core.log("СЕТЬ: прокси починить не удалось: %s" % rep.get("why"))
                core.report("error", "network", "прокси починить не удалось: %s"
                            % rep.get("why"))
        state["last_run"] = report
        core.write_state(core.NET_STATE, state)
        return report

    # 0a) Только расхождение «реестр ↔ процесс»: сеть жива, но процессы держат
    #     устаревшие переменные ⇒ освежаем их и идём дальше.
    if proxy.get("stale_process"):
        if dry_run:
            report["action"] = "освежение прокси-переменных у процессов (сухой прогон)"
        else:
            rep = repair_proxy(cfg)
            report["proxy_repair"] = rep
            report["action"] = "освежены прокси-переменные у процессов"
            if rep.get("ok"):
                report["probes"] = probes = [probe_path(p, cfg) for p in paths]
                alive = [p for p in probes if p.get("alive")]
                report["stable"] = bool(alive)
            core.log("СЕТЬ: прокси-переменные у процессов освежены (%s)" % rep.get("why"))

    # Плановый заход к вендору — не каждый проход (лишний трафик).
    state["cycles"] = int(state.get("cycles") or 0) + 1
    if int(state.get("cycles") or 0) % VENDOR_EVERY == 1:
        report["vendor_plan"] = probe_vendor()

    if not alive:
        # Ни один путь не отвечает ⇒ это и есть «интернет сломался».
        last = int(state.get("last_recover") or 0)
        if int(time.time()) - last < RECOVER_COOLDOWN:
            report["action"] = "ждём кулдаун восстановления"
        else:
            report["action"] = "каскад восстановления"
            core.log("СЕТЬ: ни один путь не отвечает — начинаю восстановление")
            core.report("error", "network", "ни один путь не отвечает — начинаю восстановление")
            state["last_recover"] = int(time.time())
            if not dry_run:
                report["recovery"] = recover(cfg, state, dry_run=False)
                stable = verify_stable(cfg)
                report["stable"] = stable.get("stable")
                report["verify"] = stable
                if stable.get("stable"):
                    state["recovered_at"] = core.now_ms()
                    core.log("СЕТЬ: доступ устойчив — %s" % json.dumps(stable.get("vendor") or {}, ensure_ascii=False))
                else:
                    core.log("СЕТЬ: устойчивого доступа нет — %s" % json.dumps(stable, ensure_ascii=False)[:300])
    else:
        # Путь есть. Страна выхода — НЕ повод для каскада: вендор по пути
        # отвечает ⇒ нода жива. Каскад запускаем только когда путь реально
        # не работает (вендор отдаёт 5xx или нет ответа).
        bad = [p for p in alive if p.get("note") and "вместо ожидаемой" in p["note"]]
        broken = [p for p in bad if int(p.get("code") or 0) >= 500]
        if bad and not broken:
            # Строка действия остаётся ровно «всё живо» — на неё смотрит
            # лечащий (`network_unhealthy`): любое другое значение он читает как
            # «сеть плохая» и ОТКЛАДЫВАЕТ лечение (так потеря входа РМЗ 2 и
            # не чинилась). Подробности — в отдельном поле.
            report["action"] = "всё живо"
            report["stable"] = True
            report["geo_note"] = bad[0].get("note")
            core.log("СЕТЬ: %s — каскад НЕ запускаю: путь живой, вендор отвечает" % bad[0].get("note"))
        elif broken and not dry_run:
            report["action"] = "путь жив, но вендор отдаёт 5xx — пробуем смену"
            core.log("СЕТЬ: %s" % broken[0]["note"])
            mark_bad_server(str(reg_read().get("lastServerHost") or ""), state,
                            "вендор 5xx: %s" % (broken[0].get("code") or "-"))
            state["last_recover"] = int(time.time())
            report["recovery"] = recover(cfg, state, dry_run=False)
            stable = verify_stable(cfg)
            report["stable"] = stable.get("stable")
            report["verify"] = stable
            if stable.get("stable"):
                state["recovered_at"] = core.now_ms()
        else:
            report["action"] = "всё живо"
            report["stable"] = True

    state["last_run"] = report
    core.write_state(core.NET_STATE, state)
    return report


def show_status() -> int:
    state = core.read_state(core.NET_STATE)
    last = state.get("last_run") or {}
    print("=== СЕТЬ (последний проход %s) ===" % core.fmt_ms(last.get("at") or 0))
    for p in last.get("probes") or []:
        print("  %-9s %-8s код=%-5s страна=%-3s ip=%-16s %s" % (
            p.get("name"), "живой" if p.get("alive") else "МЁРТВ", p.get("code"),
            p.get("country") or "-", p.get("ip") or "-", p.get("note") or ""))
    print("  действие: %s · устойчиво: %s" % (last.get("action") or "-", last.get("stable")))
    ver = (last.get("verify") or {}).get("vendor")
    if ver:
        print("  вендор: %s (%s)" % (ver.get("note"), ver.get("code")))
    px = proxy_state()
    print("  прокси-переменные:")
    for src in ("registry", "process"):
        ports = px.get(src + "_ports") or {}
        for label in ("http", "https", "socks"):
            p = ports.get(label) or {}
            if p.get("url"):
                print("    %-8s %-7s %-30s порт %-6s %s" % (
                    src, label, p["url"], p.get("port"),
                    "слушается" if p.get("listening") else "МЁРТВ"))
    if px.get("broken"):
        print("    ⚠ сломаны: %s" % ", ".join(px.get("dead") or ["не заданы"]))
    cur = reg_read()
    print("  сервер Happ: %s (%s:%s)" % (cur.get("lastServerName") or "-",
                                        cur.get("lastServerHost") or "-",
                                        cur.get("lastServerPort") or "-"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="МиниГен: сетевой сторож")
    ap.add_argument("--status", action="store_true", help="показать состояние сети")
    ap.add_argument("--once", action="store_true", help="один проход")
    ap.add_argument("--dry-run", action="store_true", help="ничего не менять, только показать")
    ap.add_argument("--recover", action="store_true", help="прогнать каскад восстановления сейчас")
    args = ap.parse_args()

    cfg = core.net_config()
    state = core.read_state(core.NET_STATE)

    if args.status:
        return show_status()
    if args.recover:
        core.log("СЕТЬ: ручной запуск восстановления")
        rep = recover(cfg, state, dry_run=args.dry_run)
        stable = verify_stable(cfg)
        rep["verify"] = stable
        print(json.dumps(rep, ensure_ascii=False, indent=2)[:4000])
        return 0 if stable.get("stable") else 1

    if args.once:
        rep = cycle(cfg, state, dry_run=args.dry_run)
        print(json.dumps(rep, ensure_ascii=False, indent=2)[:4000])
        return 0

    # демон
    core.log("СЕТЬ: сторож запущен")
    stamp = core.script_stamp(Path(__file__))
    while True:
        try:
            if core.script_stamp(Path(__file__)) != stamp:
                core.log("СЕТЬ: файл сторожа изменился — перезапускаюсь "
                         "(правка на диске не действует в живом процессе)")
                if core.supervisor_alive():
                    return 0              # супервизор поднимет свежий процесс
                core.respawn_self()
                return 0
            cycle(cfg, core.read_state(core.NET_STATE), dry_run=False)
        except Exception as exc:  # noqa: BLE001
            core.log("СЕТЬ: сбой прохода: %s" % exc)
        time.sleep(int(cfg.get("interval_sec") or 180))


if __name__ == "__main__":
    sys.exit(main())
