#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocvpn — локальный VPN-контроллер для OpenCode на Windows.

Один файл, только стандартная библиотека Python 3.11+.

Схема:
    OpenCode (serve) --HTTPS_PROXY--> 127.0.0.1:18080 (mixed inbound)
        --> ocvpn-core.exe (sing-box) --> выбранная нода (VLESS/Reality, Hysteria2, TUIC)

Ротация = переключение селектора "auto" через Clash API. Адрес прокси для OpenCode
не меняется, движок не перезапускается.

Команды:
    up            поднять всё (ядро + opencode serve + дашборд) и открыть браузер
    core          поднять только ядро (для отладки)
    refresh       обновить списки нод
    check         проверить ноды задержкой, оставить живые
    rotate        переключиться на следующую живую ноду
    status        напечатать состояние и выйти
    probe         один реальный запрос модели через прокси (стоит 1 сообщение)
    happ-off/on   остановить/запустить службу Happ (UAC)
    install       поставить автозапуск в Планировщик задач
    uninstall     снять автозапуск
    doctor        проверки окружения
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import urllib.error
import urllib.request

BASE = Path(__file__).resolve().parent
STATE = BASE / "state"
CORE_EXE = BASE / "core" / "ocvpn-core.exe"
CONFIG_PATH = BASE / "config.json"
NODES_PATH = STATE / "nodes.json"
CORE_CONFIG = STATE / "core.json"
LOG_PATH = STATE / "ocvpn.log"
DB_PATH = STATE / "state.json"

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

DEFAULT_CONFIG = {
    "ports": {
        "proxy": 18080,
        "api": 19090,
        "dashboard": 18081,
        "opencode": 18082,
    },
    "sources": {
        "epodonios": "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_Sub.txt",
        "jagger": "https://raw.githubusercontent.com/jagger235711/V2rayCollector/main/results/mixed.txt",
        "radikal": "https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/all/configs.txt",
        "solvpn": "https://raw.githubusercontent.com/SoliSpirit/SolVPN/main/all_configs.txt",
        "mahan": "https://raw.githubusercontent.com/MahanKenway/Freedom-V2Ray/main/configs/mix.txt",
        "iboxz": "https://raw.githubusercontent.com/iboxz/free-v2ray-collector/main/main/mix.txt",
        "bahemmat": "https://raw.githubusercontent.com/MohammadBahemmat/V2ray-Collector/main/all_servers.txt",
        "longlon": "https://raw.githubusercontent.com/longlon/v2ray-config/main/Splitted-By-Protocol/vless.txt",
        "wlunlocker": "https://raw.githubusercontent.com/wlunlocker/vpn-configs/main/blacklist_all.txt",
        "mhdi": "https://raw.githubusercontent.com/MhdiTaheri/V2rayCollector/main/sub/mix",
        "aliilapro": "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/sub.txt",
        "mahdibland": "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge.txt",
    },
    "node_limit": 400,
    "schemes": ["vless", "hysteria2", "vmess", "trojan", "ss", "tuic"],
    "probe_url": "https://api.ipify.org",
    "delay_url": "https://www.gstatic.com/generate_204",
    "delay_timeout_ms": 3000,
    "models": [
        "opencode/deepseek-v4.1-flash-free#high",
        "opencode/glm-5.3-flash-free#high",
        "opencode/flint-40b-free#max",
        "opencode/mimo-v2.6-flash-free#max",
        "opencode/nemotron-3-ultra-free#max",
    ],
    "probe_prompt": "Ответь одним словом: работаю",
    "probe_timeout_s": 180,
    "harvest_budget": 6,
    "burned_hours": 24,
    "rotate_after_limits": 3,
    "quota_cooldown_s": 20,
    "refresh_hours": 6,
    "happ_gate": "warn",
    "log_level": "info",
}


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except (OSError, ValueError) as exc:
            log(f"config.json не прочитан ({exc}) — беру значения по умолчанию")
    else:
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Ноды: разбор URI -> outbound sing-box
# --------------------------------------------------------------------------

@dataclass
class Node:
    tag: str
    scheme: str
    host: str
    port: int
    name: str
    outbound: dict
    latency: int | None = None
    dead_until: float = 0.0
    burned_until: float = 0.0  # квота сети уже выжжена — не пробовать до этого времени

    @property
    def alive(self) -> bool:
        return self.latency is not None and time.time() >= self.dead_until

    @property
    def fresh(self) -> bool:
        return self.alive and time.time() >= self.burned_until

    def to_json(self) -> dict:
        return {
            "tag": self.tag,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "outbound": self.outbound,
            "latency": self.latency,
            "dead_until": self.dead_until,
            "burned_until": self.burned_until,
        }

    @staticmethod
    def from_json(d: dict) -> "Node":
        fields = ("tag", "scheme", "host", "port", "name", "outbound", "latency", "dead_until")
        kwargs = {k: d.get(k, 0.0 if k == "dead_until" else None) for k in fields}
        kwargs["burned_until"] = d.get("burned_until", 0.0)
        return Node(**kwargs)


def _q(qs: dict, *names, default: str = "") -> str:
    for n in names:
        vals = qs.get(n)
        if vals:
            return vals[0]
    return default


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# sing-box знает только свой набор uTLS-отпечатков. Списки нод часто несут xray-значение
# `unsafe` — ядро падает на нём («unknown uTLS fingerprint: unsafe»), и такие ноды молча
# уходили в мусор. Подменяем неизвестное на chrome: это дефолт Xray для uTLS.
SINGBOX_FINGERPRINTS = {
    "chrome",
    "firefox",
    "safari",
    "ios",
    "android",
    "edge",
    "360",
    "qq",
    "random",
    "randomized",
}


def _fix_fingerprint(fp: str) -> str:
    fp = str(fp or "").strip().lower()
    return fp if fp in SINGBOX_FINGERPRINTS else "chrome"


def _tls_block(qs: dict, host: str, security: str, insecure: bool) -> dict | None:
    """TLS-блок для outbound. None — параметры неполные (ноду выкидываем)."""
    sni = _q(qs, "sni", "peer", "servername", "host") or host
    fp = _fix_fingerprint(_q(qs, "fp") or "chrome")
    if security == "reality":
        pbk = _q(qs, "pbk")
        if not pbk:
            return None
        tls: dict = {
            "enabled": True,
            "server_name": sni,
            "utls": {"enabled": True, "fingerprint": fp},
            "reality": {"enabled": True, "public_key": pbk},
        }
        sid = _q(qs, "sid")
        if sid:
            tls["reality"]["short_id"] = sid
        return tls
    if security in ("tls", "xtls"):
        tls = {"enabled": True, "server_name": sni, "utls": {"enabled": True, "fingerprint": fp}}
        if insecure:
            tls["insecure"] = True
        alpn = _q(qs, "alpn")
        if alpn:
            tls["alpn"] = [a for a in alpn.split(",") if a]
        return tls
    return {}


def _transport_block(net: str, qs: dict, sni: str) -> dict | None:
    """Транспорт sing-box; None — чистый TCP или неподдерживаемая экзотика."""
    net = (net or "tcp").lower()
    if net in ("tcp", "raw", "", "none"):
        return None
    if net in ("ws", "websocket"):
        return {
            "type": "ws",
            "path": _q(qs, "path") or "/",
            "headers": {"Host": _q(qs, "host") or sni},
        }
    if net == "grpc":
        return {"type": "grpc", "service_name": _q(qs, "serviceName", "servicename", "path")}
    if net == "httpupgrade":
        return {"type": "httpupgrade", "path": _q(qs, "path") or "/", "host": _q(qs, "host") or sni}
    return None


def _b64_json(blob: str) -> dict | None:
    try:
        compact = blob.strip()
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _parse_vmess(uri: str, tag: str) -> Node | None:
    data = _b64_json(uri.split("://", 1)[1])
    if not data:
        return None
    host = str(data.get("add") or "").strip()
    uid = str(data.get("id") or "").strip()
    try:
        port = int(str(data.get("port") or "0"))
        alter_id = int(str(data.get("aid") or "0"))
    except ValueError:
        return None
    if not host or not port or not uid:
        return None
    net = str(data.get("net") or "tcp")
    host_hdr = str(data.get("host") or "")
    path = str(data.get("path") or "")
    sni = str(data.get("sni") or host_hdr or host)
    qs = {"path": [path], "host": [host_hdr], "serviceName": [path], "sni": [sni]}
    alpn = str(data.get("alpn") or "")
    if alpn:
        qs["alpn"] = [alpn]
    security = "tls" if str(data.get("tls") or "").lower() in ("tls", "reality") else "none"
    ob: dict = {
        "type": "vmess",
        "tag": tag,
        "server": host,
        "server_port": port,
        "uuid": uid,
        "security": str(data.get("scy") or "auto"),
        "alter_id": alter_id,
    }
    if security != "none":
        tls = _tls_block(qs, host, security, False)
        if tls is None:
            return None
        ob["tls"] = tls
    transport = _transport_block(net, qs, sni)
    if transport:
        ob["transport"] = transport
    name = str(data.get("ps") or f"{host}:{port}")
    return Node(tag, "vmess", host, port, name, ob)


def _parse_ss(uri: str, tag: str) -> Node | None:
    body = uri.split("://", 1)[1]
    name = ""
    if "#" in body:
        body, frag = body.split("#", 1)
        name = unquote(frag).strip()
    if "?" in body:
        body, query = body.split("?", 1)
        if "plugin" in query:
            return None  # obfs/v2ray-плагины не поддерживаем
    if "@" in body:
        userinfo, hostpart = body.rsplit("@", 1)
        if ":" not in userinfo:
            try:
                compact = userinfo.strip()
                userinfo = base64.b64decode(
                    compact + "=" * (-len(compact) % 4)
                ).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return None
    else:
        try:
            compact = body.strip()
            decoded = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return None
        if "@" not in decoded:
            return None
        userinfo, hostpart = decoded.rsplit("@", 1)
    if ":" not in userinfo or ":" not in hostpart:
        return None
    method, password = userinfo.split(":", 1)
    host, port_s = hostpart.rsplit(":", 1)
    host = host.strip().strip("[]")
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not host or not port or not method:
        return None
    ob = {
        "type": "shadowsocks",
        "tag": tag,
        "server": host,
        "server_port": port,
        "method": method,
        "password": password,
    }
    return Node(tag, "ss", host, port, name or f"{host}:{port}", ob)


def parse_uri(uri: str, tag: str) -> Node | None:
    """URI подписки -> Node (или None, если формат не умеем)."""
    uri = uri.strip()
    scheme = uri.split("://", 1)[0].lower()
    try:
        if scheme == "vmess":
            return _parse_vmess(uri, tag)
        if scheme == "ss":
            return _parse_ss(uri, tag)
        if scheme not in ("vless", "hysteria2", "hy2", "tuic", "trojan"):
            return None

        u = urlparse(uri)
        host = u.hostname
        port = u.port
        if not host or not port:
            return None
        qs = parse_qs(u.query or "")
        name = unquote(u.fragment or "").strip() or f"{host}:{port}"
        sni = _q(qs, "sni", "peer", "host") or host
        insecure = _truthy(_q(qs, "insecure", "allowInsecure", "skip-cert-verify", default="0"))
        security = (_q(qs, "security") or "none").lower()
        net = _q(qs, "type", "net") or "tcp"

        if scheme == "vless":
            uid = unquote(u.username or "")
            if not uid:
                return None
            ob = {"type": "vless", "tag": tag, "server": host, "server_port": port, "uuid": uid}
            if security in ("tls", "reality", "xtls"):
                tls = _tls_block(qs, host, security, insecure)
                if tls is None:
                    return None
                ob["tls"] = tls
            transport = _transport_block(net, qs, sni)
            flow = _q(qs, "flow")
            if flow and transport is None:
                # sing-box умеет только xtls-rprx-vision; -udp443 — его алиас в xray
                ob["flow"] = "xtls-rprx-vision" if flow.startswith("xtls-rprx-vision") else ""
            if transport:
                ob["transport"] = transport
            return Node(tag, "vless", host, port, name, ob)

        if scheme == "trojan":
            password = unquote(u.username or "")
            if not password:
                return None
            ob = {"type": "trojan", "tag": tag, "server": host, "server_port": port, "password": password}
            tls = _tls_block(qs, host, security if security in ("tls", "reality") else "tls", True)
            if tls is None:
                return None
            ob["tls"] = tls
            transport = _transport_block(net, qs, sni)
            if transport:
                ob["transport"] = transport
            return Node(tag, "trojan", host, port, name, ob)

        if scheme in ("hysteria2", "hy2"):
            password = unquote(u.username or "") or _q(qs, "password", "auth")
            if not password:
                return None
            insecure_flag = _q(qs, "insecure", "allowInsecure")
            ob = {
                "type": "hysteria2",
                "tag": tag,
                "server": host,
                "server_port": port,
                "password": password,
                # у бесплатных нод почти всегда самоподписанный сертификат
                "tls": {
                    "enabled": True,
                    "server_name": sni,
                    "insecure": _truthy(insecure_flag) if insecure_flag else True,
                },
            }
            obfs = _q(qs, "obfs")
            obfs_password = _q(qs, "obfs-password", "obfs_password")
            if obfs and obfs_password:
                ob["obfs"] = {"type": obfs, "password": obfs_password}
            return Node(tag, "hysteria2", host, port, name, ob)

        # tuic
        uid = unquote(u.username or "")
        password = unquote(u.password or "") or _q(qs, "password")
        if not uid or not password:
            return None
        ob = {
            "type": "tuic",
            "tag": tag,
            "server": host,
            "server_port": port,
            "uuid": uid,
            "password": password,
            "congestion_control": _q(qs, "congestion_control") or "bbr",
            "udp_relay_mode": _q(qs, "udp_relay_mode") or "native",
            "tls": {"enabled": True, "server_name": sni, "insecure": True},
        }
        return Node(tag, "tuic", host, port, name, ob)
    except Exception:  # noqa: BLE001 — битая строка в чужом списке не должна валить импорт
        return None


def _decode_subscription(text: str) -> str:
    """Списки бывают base64-блобом целиком."""
    if "://" in text:
        return text
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return text
    try:
        padded = compact + "=" * (-len(compact) % 4)
        return base64.b64decode(padded).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return text


def fetch_source(name: str, url: str, timeout: int = 40) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "ocvpn/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    text = _decode_subscription(raw)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "://" in line:
            out.append(line)
    log(f"источник {name}: {len(out)} строк конфигов")
    return out


def fetch_nodes(cfg: dict) -> list[Node]:
    per_source: list[list[str]] = []
    for name, url in cfg["sources"].items():
        try:
            per_source.append(fetch_source(name, url))
        except Exception as exc:  # noqa: BLE001 — один упавший источник не должен ломать остальные
            log(f"источник {name} не скачался: {exc}")
    # чередуем источники, иначе первый большой список забирает весь лимит
    uris: list[str] = []
    for idx in range(max((len(s) for s in per_source), default=0)):
        for source in per_source:
            if idx < len(source):
                uris.append(source[idx])
    allowed = set(cfg["schemes"])
    seen: set[tuple] = set()
    nodes: list[Node] = []
    for uri in uris:
        scheme = uri.split("://", 1)[0].lower()
        if scheme not in allowed:
            continue
        node = parse_uri(uri, f"n{len(nodes) + 1}")
        if node is None:
            continue
        key = (
            node.scheme,
            node.host,
            node.port,
            node.outbound.get("uuid") or node.outbound.get("password"),
        )
        if key in seen:
            continue
        seen.add(key)
        nodes.append(node)
        if len(nodes) >= cfg["node_limit"]:
            break
    log(f"разобрано нод: {len(nodes)} (уникальных)")
    return nodes


def load_nodes() -> list[Node]:
    if not NODES_PATH.exists():
        return []
    try:
        data = json.loads(NODES_PATH.read_text(encoding="utf-8"))
        return [Node.from_json(d) for d in data]
    except (OSError, ValueError, KeyError):
        return []


def save_nodes(nodes: list[Node]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    NODES_PATH.write_text(json.dumps([n.to_json() for n in nodes], ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Ядро (sing-box) + Clash API
# --------------------------------------------------------------------------

def http_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 10) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body else {}


def http_get_text(url: str, proxy: str | None = None, timeout: int = 15) -> str:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "ocvpn/1.0"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


class Core:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.api_base = f"http://127.0.0.1:{cfg['ports']['api']}"
        self.proc: subprocess.Popen | None = None

    def write_config(self, nodes: list[Node]) -> Path:
        tags = [n.tag for n in nodes] or ["direct"]
        first = tags[0]
        outbounds: list[dict] = [
            {
                "type": "selector",
                "tag": "auto",
                "outbounds": tags,
                "default": first,
            }
        ]
        # пул мог быть сохранён до нормализации отпечатков — чиним и уже собранные ноды
        for node in nodes:
            utls = (node.outbound.get("tls") or {}).get("utls") or {}
            if utls.get("fingerprint"):
                utls["fingerprint"] = _fix_fingerprint(utls["fingerprint"])
        outbounds.extend(n.outbound for n in nodes)
        outbounds.append({"type": "direct", "tag": "direct"})
        config = {
            "log": {
                "level": self.cfg["log_level"],
                "output": str(STATE / "core.log"),
                "timestamp": True,
            },
            "inbounds": [
                {
                    "type": "mixed",
                    "tag": "mixed-in",
                    "listen": "127.0.0.1",
                    "listen_port": self.cfg["ports"]["proxy"],
                }
            ],
            "outbounds": outbounds,
            "route": {"final": "auto"},
            "experimental": {
                "clash_api": {
                    "external_controller": f"127.0.0.1:{self.cfg['ports']['api']}"
                }
            },
        }
        STATE.mkdir(parents=True, exist_ok=True)
        CORE_CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        return CORE_CONFIG

    def validate(self, path: Path) -> tuple[bool, str]:
        res = subprocess.run(
            [str(CORE_EXE), "check", "-c", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0:
            return True, ""
        return False, (res.stderr or res.stdout or "").strip()

    def start(self, nodes: list[Node]) -> None:
        if not nodes:
            raise RuntimeError("нет нод: сначала refresh")
        if not CORE_EXE.exists():
            raise RuntimeError(f"нет ядра: {CORE_EXE}")
        self.stop()
        nodes = list(nodes)
        path = self.write_config(nodes)
        for _ in range(15):
            ok, err = self.validate(path)
            if ok:
                break
            m = re.search(r"outbound\[(\d+)\]", err)
            node_index = int(m.group(1)) - 1 if m else -1
            if 0 <= node_index < len(nodes):
                bad = nodes.pop(node_index)
                log(f"ядро отвергло ноду {bad.name[:40]} ({bad.scheme}) — выкидываю: {err[:120]}")
                path = self.write_config(nodes)
                continue
            raise RuntimeError(f"конфиг ядра не принят: {err[:300]}")
        else:
            raise RuntimeError("конфиг ядра не принят после 15 выкинутых нод")
        if not nodes:
            raise RuntimeError("после проверки конфига не осталось ни одной ноды")
        self.proc = subprocess.Popen(
            [str(CORE_EXE), "run", "-c", str(path)],
            cwd=str(BASE),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("ядро упало при старте — смотри state/core.log")
            try:
                http_json(f"{self.api_base}/proxies", timeout=2)
                log(f"ядро поднято (pid {self.proc.pid}), прокси на 127.0.0.1:{self.cfg['ports']['proxy']}")
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        raise RuntimeError("ядро не ответило по Clash API за 15 с")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/IM", "ocvpn-core.exe", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )

    def running(self) -> bool:
        try:
            http_json(f"{self.api_base}/proxies", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delay(self, tag: str) -> int | None:
        url = self.cfg["delay_url"]
        q = (
            f"{self.api_base}/proxies/{tag}/delay"
            f"?timeout={self.cfg['delay_timeout_ms']}&url={urllib.parse.quote(url, safe='')}"
        )
        try:
            data = http_json(q, timeout=self.cfg["delay_timeout_ms"] / 1000 + 2)
            value = data.get("delay")
            return int(value) if value and int(value) > 0 else None
        except Exception:  # noqa: BLE001
            return None

    def select(self, tag: str) -> None:
        http_json(f"{self.api_base}/proxies/auto", "PUT", {"name": tag})

    def current(self) -> str:
        try:
            data = http_json(f"{self.api_base}/proxies/auto")
            return data.get("now") or ""
        except Exception:  # noqa: BLE001
            return ""


def measure_ip(proxy: str | None, url: str) -> str:
    text = http_get_text(url, proxy=proxy, timeout=15).strip()
    if text.startswith("{"):
        try:
            return json.loads(text).get("ip", text)
        except ValueError:
            return text
    return text


# --------------------------------------------------------------------------
# OpenCode: запуск + поиск бинаря
# --------------------------------------------------------------------------

def find_opencode() -> list[str] | None:
    """Возвращает команду запуска opencode (без подкоманды)."""
    cmd_path = shutil.which("opencode")
    if cmd_path:
        p = Path(cmd_path)
        if p.suffix.lower() in (".cmd", ".bat"):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'"([^"]*opencode[^"]*\.exe)"', text, re.I)
                if m and Path(m.group(1)).exists():
                    return [m.group(1)]
            except OSError:
                pass
            return ["cmd", "/c", str(p)]
        return [str(p)]
    for cand in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/@opencodedesktop/resources/opencode-cli.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/opencode-bin/opencode.exe",
    ):
        if cand.exists():
            return [str(cand)]
    return None


class OpenCode:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.cmd = find_opencode()

    def env(self) -> dict:
        env = os.environ.copy()
        proxy = f"http://127.0.0.1:{self.cfg['ports']['proxy']}"
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["https_proxy"] = proxy
        env["http_proxy"] = proxy
        env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        env["no_proxy"] = env["NO_PROXY"]
        env.pop("ALL_PROXY", None)
        env.pop("all_proxy", None)
        return env

    def start(self) -> None:
        if not self.cmd:
            raise RuntimeError("не нашёл opencode в PATH")
        self.stop()
        port = self.cfg["ports"]["opencode"]
        self.proc = subprocess.Popen(
            self.cmd + ["serve", "--port", str(port), "--log-level", "info"],
            cwd=str(BASE),
            env=self.env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        log(f"opencode serve запущен на 127.0.0.1:{port} (pid {self.proc.pid}), трафик через прокси")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def probe(self, model: str | None = None, use_server: bool = True) -> tuple[bool, str]:
        """Один реальный запрос модели. Стоит одно сообщение в квоте сети."""
        model = model or self.cfg["models"][0]
        if not self.cmd:
            return False, "opencode не найден"
        args = self.cmd + ["run", "--model", model, "--log-level", "error"]
        if use_server and self.running():
            args += ["--server", f"http://127.0.0.1:{self.cfg['ports']['opencode']}"]
        else:
            args.append("--standalone")
        args += ["--title", "ocvpn проба", self.cfg["probe_prompt"]]
        try:
            res = subprocess.run(
                args,
                cwd=str(BASE),
                env=self.env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.cfg["probe_timeout_s"],
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return False, "проба не ответила за отведённое время"
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if res.returncode == 0 and out:
            return True, out.splitlines()[-1][:200]
        detail = (err or out or "пустой ответ").splitlines()
        return False, (detail[-1][:200] if detail else "пустой ответ")


# --------------------------------------------------------------------------
# Сторож: хвост лога OpenCode (бесплатный сигнал о лимите)
# --------------------------------------------------------------------------

QUOTA_RE = re.compile(
    r"free usage limit reached|usage limit|rate.?limit|too many requests|\b429\b|quota",
    re.IGNORECASE,
)


def opencode_log_path() -> Path:
    share = os.environ.get("XDG_DATA_HOME")
    base = Path(share) if share else Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".local/share"
    return base / "opencode" / "log" / "opencode.log"


class LogWatcher(threading.Thread):
    """Читает только дописанное; на совпадение зовёт callback(text)."""

    def __init__(self, path: Path, pattern: re.Pattern, callback, poll_s: float = 1.0):
        super().__init__(daemon=True)
        self.path = path
        self.pattern = pattern
        self.callback = callback
        self.poll_s = poll_s
        # стартуем с конца: история лога не должна считаться новыми событиями
        try:
            self.offset = path.stat().st_size if path.exists() else 0
        except OSError:
            self.offset = 0
        self.stop_flag = threading.Event()

    def run(self) -> None:
        while not self.stop_flag.is_set():
            try:
                if not self.path.exists():
                    time.sleep(self.poll_s)
                    continue
                size = self.path.stat().st_size
                if size < self.offset:  # файл пересоздан
                    self.offset = 0
                if size > self.offset:
                    with open(self.path, "rb") as f:
                        f.seek(self.offset)
                        chunk = f.read(size - self.offset)
                        self.offset = size
                    text = chunk.decode("utf-8", "replace")
                    for line in text.splitlines():
                        if self.pattern.search(line):
                            try:
                                self.callback(line)
                            except Exception:  # noqa: BLE001
                                log("ошибка обработчика лога:\n" + traceback.format_exc())
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self.poll_s)

    def stop(self) -> None:
        self.stop_flag.set()


# --------------------------------------------------------------------------
# Контроллер: состояние, ротация, квоты, счётчики
# --------------------------------------------------------------------------

class Controller:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.core = Core(cfg)
        self.opencode = OpenCode(cfg)
        self.nodes: list[Node] = load_nodes()
        self.lock = threading.RLock()
        self.events: deque = deque(maxlen=200)
        self.status = "stopped"
        self.direct_ip = ""
        self.egress_ip = ""
        self.current_tag = ""
        self.limit_streak = 0
        self.last_quota_at = 0.0
        self.today = date.today().isoformat()
        self.counters = {"rotations": 0, "probes": 0, "limits": 0, "models": 0}
        self.model_index = 0
        self.watcher: LogWatcher | None = None
        self.happ_status = ""
        self._load_state()

    # --- состояние на диск -------------------------------------------------
    def _load_state(self) -> None:
        if not DB_PATH.exists():
            return
        try:
            d = json.loads(DB_PATH.read_text(encoding="utf-8"))
            if d.get("today") == self.today:
                self.counters.update(d.get("counters", {}))
            self.model_index = int(d.get("model_index", 0))
        except (OSError, ValueError):
            pass

    def _save_state(self) -> None:
        STATE.mkdir(parents=True, exist_ok=True)
        payload = {
            "today": self.today,
            "counters": self.counters,
            "model_index": self.model_index,
        }
        DB_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _roll_day(self) -> None:
        now = date.today().isoformat()
        if now != self.today:
            self.today = now
            self.counters = {"rotations": 0, "probes": 0, "limits": 0, "models": 0}
            self._save_state()

    def event(self, kind: str, text: str) -> None:
        with self.lock:
            self.events.appendleft(
                {"ts": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text}
            )
        log(f"[{kind}] {text}")

    # --- модель ------------------------------------------------------------
    @property
    def model(self) -> str:
        models = self.cfg["models"]
        return models[self.model_index % len(models)]

    def next_model(self) -> str:
        self.model_index = (self.model_index + 1) % len(self.cfg["models"])
        self.counters["models"] += 1
        self._save_state()
        self.event("модель", f"перехожу на {self.model}")
        return self.model

    # --- ноды --------------------------------------------------------------
    def refresh(self) -> int:
        self.status = "refresh"
        nodes = fetch_nodes(self.cfg)
        if not nodes:
            self.status = "down"
            raise RuntimeError("списки не дали ни одной ноды")
        with self.lock:
            self.nodes = nodes
        save_nodes(nodes)
        self.event("списки", f"обновлено нод: {len(nodes)}")
        self.status = "ok" if self.core.running() else "stopped"
        return len(nodes)

    def check(self) -> list[Node]:
        if not self.core.running():
            raise RuntimeError("ядро не запущено")
        with self.lock:
            nodes = list(self.nodes)
        if not nodes:
            raise RuntimeError("нет нод")
        tags = [n.tag for n in nodes]

        def probe(tag: str) -> int | None:
            return self.core.delay(tag)

        with ThreadPoolExecutor(max_workers=int(self.cfg.get("check_workers", 24))) as pool:
            results = list(pool.map(probe, tags))
        alive = 0
        for node, latency in zip(nodes, results):
            node.latency = latency
            node.dead_until = 0.0 if latency else time.time() + 900
            alive += 1 if latency else 0
        save_nodes(nodes)
        self.event("проверка", f"живых {alive} из {len(nodes)}")
        return nodes

    def alive_nodes(self, fresh_only: bool = False) -> list[Node]:
        with self.lock:
            pool = [n for n in self.nodes if (n.fresh if fresh_only else n.alive)]
        return sorted(pool, key=lambda n: n.latency or 9999)

    def mark_burned(self, tag: str, hours: float | None = None) -> None:
        until = time.time() + (hours or float(self.cfg.get("burned_hours", 24))) * 3600
        with self.lock:
            for n in self.nodes:
                if n.tag == tag:
                    n.burned_until = until
                    break
            save_nodes(self.nodes)

    def rotate_to_fresh(self, reason: str = "лимит", budget: int = 6) -> dict:
        """Ротация + проба: ищем сеть, у которой квота ещё жива."""
        for attempt in range(1, budget + 1):
            result = self.rotate(reason=f"{reason} {attempt}/{budget}")
            ok, detail = self.probe_model()
            result["probe_ok"] = ok
            result["probe_detail"] = detail
            if ok:
                self.event("квота", f"свежая сеть найдена с {attempt}-й ноды")
                return result
            self.mark_burned(self.current_tag)
            self.event("квота", f"сеть {self.egress_ip or '?'} квоты не даёт ({attempt}/{budget})")
        return {"exhausted": True, "detail": "свежих сетей в пуле не нашлось"}

    def rotate(self, reason: str = "вручную", require_probe: bool = False) -> dict:
        """Переключить селектор на следующую живую ноду + сверить внешний IP."""
        with self.lock:
            self.status = "rotating"
        try:
            if not self.core.running():
                raise RuntimeError("ядро не запущено")
            alive = self.alive_nodes(fresh_only=True) or self.alive_nodes()
            if not alive:
                self.event("ноды", "живых нет — проверяю заново")
                self.check()
                alive = self.alive_nodes(fresh_only=True) or self.alive_nodes()
            if not alive:
                raise RuntimeError("живых нод нет")
            candidates = [n for n in alive if n.tag != self.current_tag] or alive
            target = candidates[0]
            before = self.egress_ip
            self.core.select(target.tag)
            time.sleep(0.6)
            proxy = f"http://127.0.0.1:{self.cfg['ports']['proxy']}"
            try:
                self.egress_ip = measure_ip(proxy, self.cfg["probe_url"])
            except Exception as exc:  # noqa: BLE001
                self.event("ноды", f"внешний IP не определился: {exc}")
            self.current_tag = target.tag
            self.counters["rotations"] += 1
            self._save_state()
            self.event(
                "ротация",
                f"{reason}: {target.name[:40]} ({target.scheme}, {target.latency} мс), "
                f"IP {before or '?'} → {self.egress_ip or '?'}",
            )
            self.status = "ok"
            result = {
                "node": target.name,
                "scheme": target.scheme,
                "latency": target.latency,
                "egress_ip": self.egress_ip,
            }
            if require_probe:
                ok, detail = self.probe_model()
                result["probe_ok"] = ok
                result["probe_detail"] = detail
            return result
        finally:
            if self.status == "rotating":
                self.status = "ok"

    # --- квота -------------------------------------------------------------
    def on_quota(self, line: str) -> None:
        now = time.time()
        if now - self.last_quota_at < self.cfg["quota_cooldown_s"]:
            return
        self.last_quota_at = now
        self.counters["limits"] += 1
        self._save_state()
        short = re.sub(r"\s+", " ", line)[:150]
        self.event("лимит", f"free-тир ответил лимитом на текущей сети: {short}")
        threading.Thread(target=self._recover_safe, daemon=True).start()

    def _recover_safe(self) -> None:
        try:
            result = self.rotate_to_fresh(
                reason="лимит", budget=int(self.cfg.get("harvest_budget", 6))
            )
            if not result.get("exhausted"):
                return
            self.event("квота", "свежих сетей в пуле нет — пробую следующую модель")
            model = self.next_model()
            self.event("модель", f"перехожу на {model}")
            self.opencode.stop()
            self.opencode.start()
        except Exception as exc:  # noqa: BLE001
            self.status = "down"
            self.event("ошибка", f"восстановление не удалось: {exc}")

    def probe_model(self) -> tuple[bool, str]:
        ok, detail = self.opencode.probe(self.model)
        self.counters["probes"] += 1
        self._save_state()
        self.event("проба", ("ответила: " + detail) if ok else ("не прошла: " + detail))
        return ok, detail

    # --- Happ --------------------------------------------------------------
    def happ_check(self) -> str:
        ps = (
            "Get-NetAdapter | Where-Object { $_.Name -like 'happ*' } "
            "| Select-Object -First 1 -ExpandProperty Status"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=25,
                creationflags=CREATE_NO_WINDOW,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            out = ""
        self.happ_status = out or "не найден"
        return self.happ_status

    def happ_service(self, action: str) -> tuple[bool, str]:
        """action: stop|start — через UAC, потому что HappService это служба."""
        cmd = f"{action.title()}-Service HappService -Force"
        try:
            res = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Start-Process powershell -Verb RunAs -Wait -ArgumentList "
                    f"'-NoProfile','-Command','{cmd}'",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return False, "UAC не подтверждён за 120 с"
        if res.returncode != 0:
            return False, (res.stderr or "запрос прав отклонён").strip()[:200]
        time.sleep(2)
        status = self.happ_check()
        return True, status

    # --- жизненный цикл ----------------------------------------------------
    def start(self, open_opencode: bool = True) -> None:
        STATE.mkdir(parents=True, exist_ok=True)
        if not self.nodes:
            self.refresh()
        self.core.start(self.nodes)
        self.status = "ok"
        self.direct_ip = ""
        try:
            self.egress_ip = measure_ip(
                f"http://127.0.0.1:{self.cfg['ports']['proxy']}", self.cfg["probe_url"]
            )
            self.event("сеть", f"внешний IP через прокси: {self.egress_ip}")
        except Exception as exc:  # noqa: BLE001
            self.event("сеть", f"внешний IP не определился: {exc}")
        self.happ_check()
        if open_opencode:
            try:
                self.opencode.start()
            except Exception as exc:  # noqa: BLE001
                self.event("ошибка", f"OpenCode не запустился: {exc}")
        self.start_watchdog()

    def start_watchdog(self) -> None:
        if self.watcher:
            return
        path = opencode_log_path()
        self.watcher = LogWatcher(path, QUOTA_RE, self.on_quota)
        self.watcher.start()
        self.event("сторож", f"слежу за {path}")

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.opencode.stop()
        self.core.stop()
        self.status = "stopped"

    def snapshot(self) -> dict:
        self._roll_day()
        node = None
        with self.lock:
            for n in self.nodes:
                if n.tag == self.current_tag:
                    node = n
                    break
        return {
            "status": self.status,
            "core": self.core.running(),
            "opencode": self.opencode.running(),
            "node": {
                "tag": node.tag,
                "name": node.name[:80],
                "host": node.host,
                "scheme": node.scheme,
                "latency": node.latency,
            }
            if node
            else None,
            "nodes_total": len(self.nodes),
            "nodes_alive": len([n for n in self.nodes if n.alive]),
            "nodes_fresh": len([n for n in self.nodes if n.fresh]),
            "egress_ip": self.egress_ip,
            "direct_ip": self.direct_ip,
            "model": self.model,
            "happ": self.happ_status,
            "counters": dict(self.counters),
            "ports": self.cfg["ports"],
            "events": list(self.events)[:60],
        }


# --------------------------------------------------------------------------
# Дашборд
# --------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    controller: Controller | None = None

    def log_message(self, fmt: str, *args) -> None:  # тишина в консоли
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        ctrl = self.controller
        assert ctrl is not None
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            html = (BASE / "dashboard.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(ctrl.snapshot())
        else:
            self._json({"error": "не найдено"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        ctrl = self.controller
        assert ctrl is not None
        path = self.path.split("?")[0]
        try:
            if path == "/api/action":
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                action = payload.get("action", "")
                result = run_action(ctrl, action)
                self._json({"ok": True, "result": result})
            else:
                self._json({"error": "не найдено"}, 404)
        except Exception as exc:  # noqa: BLE001
            log("действие упало:\n" + traceback.format_exc())
            self._json({"ok": False, "error": str(exc)}, 500)


def run_action(ctrl: Controller, action: str) -> dict:
    if action == "rotate":
        return ctrl.rotate(reason="кнопка")
    if action == "harvest":
        return ctrl.rotate_to_fresh(reason="кнопка", budget=int(ctrl.cfg.get("harvest_budget", 6)))
    if action == "check":
        nodes = ctrl.check()
        return {"alive": len([n for n in nodes if n.alive]), "total": len(nodes)}
    if action == "refresh":
        return {"nodes": ctrl.refresh()}
    if action == "refresh_check":
        n = ctrl.refresh()
        nodes = ctrl.check()
        return {"nodes": n, "alive": len([x for x in nodes if x.alive])}
    if action == "probe":
        ok, detail = ctrl.probe_model()
        return {"ok": ok, "detail": detail}
    if action == "model":
        return {"model": ctrl.next_model()}
    if action == "restart_opencode":
        ctrl.opencode.stop()
        ctrl.opencode.start()
        return {"ok": True}
    if action == "happ_off":
        ok, detail = ctrl.happ_service("stop")
        return {"ok": ok, "happ": detail}
    if action == "happ_on":
        ok, detail = ctrl.happ_service("start")
        return {"ok": ok, "happ": detail}
    if action == "stop":
        ctrl.stop()
        return {"ok": True}
    if action == "start":
        ctrl.start()
        return {"ok": True}
    raise ValueError(f"неизвестное действие: {action}")


def serve_dashboard(ctrl: Controller) -> ThreadingHTTPServer:
    DashboardHandler.controller = ctrl
    port = ctrl.cfg["ports"]["dashboard"]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log(f"дашборд: http://127.0.0.1:{port}")
    return httpd


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_doctor(cfg: dict) -> None:
    print("окружение:")
    print(f"  python      {sys.version.split()[0]}")
    print(f"  ядро        {CORE_EXE} {'есть' if CORE_EXE.exists() else 'НЕТ'}")
    if CORE_EXE.exists():
        out = subprocess.run([str(CORE_EXE), "version"], capture_output=True, text=True).stdout
        print(f"  версия      {out.splitlines()[0] if out else '?'}")
    print(f"  opencode    {' '.join(find_opencode() or ['НЕ НАЙДЕН'])}")
    print(f"  лог движка  {opencode_log_path()} {'есть' if opencode_log_path().exists() else 'НЕТ'}")
    ctrl = Controller(cfg)
    print(f"  Happ        {ctrl.happ_check()}")
    print(f"  порты       proxy={cfg['ports']['proxy']} api={cfg['ports']['api']} "
          f"dashboard={cfg['ports']['dashboard']} opencode={cfg['ports']['opencode']}")
    try:
        direct = measure_ip(None, cfg["probe_url"])
        print(f"  прямой IP   {direct} (без прокси)")
    except Exception as exc:  # noqa: BLE001
        print(f"  прямой IP   не определился: {exc}")


def cmd_up(cfg: dict, no_browser: bool, no_opencode: bool) -> int:
    ctrl = Controller(cfg)
    serve_dashboard(ctrl)
    try:
        ctrl.start(open_opencode=not no_opencode)
    except Exception as exc:  # noqa: BLE001
        ctrl.event("ошибка", f"старт не удался: {exc}")
        log("останов: " + traceback.format_exc())
        return 1
    url = f"http://127.0.0.1:{cfg['ports']['dashboard']}"
    if not no_browser:
        try:
            os.startfile(url)  # noqa: S606 — windows shell
        except Exception:  # noqa: BLE001
            pass
    log("работаю. Ctrl+C — остановить.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("останавливаюсь по Ctrl+C")
        ctrl.stop()
        return 0


def cmd_install() -> int:
    py = sys.executable
    script = Path(__file__).resolve()
    task = "ocvpn"
    cmd = f'"{py}" "{script}" up --no-browser'
    res = subprocess.run(
        ["schtasks", "/Create", "/TN", task, "/SC", "ONLOGON", "/RL", "LIMITED",
         "/TR", cmd, "/F"],
        capture_output=True,
        text=True,
    )
    print(res.stdout.strip() or res.stderr.strip())
    return res.returncode


def cmd_uninstall() -> int:
    res = subprocess.run(["schtasks", "/Delete", "/TN", "ocvpn", "/F"], capture_output=True, text=True)
    print(res.stdout.strip() or res.stderr.strip())
    return res.returncode


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # noqa: PL1101 — windows-консоль по умолчанию cp866
        except Exception:  # noqa: BLE001
            pass
    parser = argparse.ArgumentParser(prog="ocvpn", description="локальный VPN-контроллер для OpenCode")
    parser.add_argument("command", nargs="?", default="up")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-opencode", action="store_true")
    args = parser.parse_args(argv)
    STATE.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    cmd = args.command

    if cmd == "doctor":
        cmd_doctor(cfg)
        return 0
    if cmd in ("install", "uninstall"):
        return cmd_install() if cmd == "install" else cmd_uninstall()
    if cmd == "up":
        return cmd_up(cfg, args.no_browser, args.no_opencode)

    ctrl = Controller(cfg)
    if cmd == "core":
        if not ctrl.nodes:
            ctrl.refresh()
        ctrl.core.start(ctrl.nodes)
        print(f"ядро на 127.0.0.1:{cfg['ports']['proxy']}, API {cfg['ports']['api']}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ctrl.core.stop()
        return 0
    if cmd == "refresh":
        print(f"нод: {ctrl.refresh()}")
        return 0
    if cmd == "check":
        ctrl.core.start(ctrl.nodes)
        nodes = ctrl.check()
        alive = [n for n in nodes if n.alive]
        for n in sorted(alive, key=lambda x: x.latency or 9999)[:10]:
            print(f"  {n.latency:>5} мс  {n.scheme:<9} {n.name[:50]}")
        print(f"живых {len(alive)} из {len(nodes)}")
        return 0
    if cmd == "rotate":
        result = ctrl.rotate(reason="cli")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if cmd == "status":
        print(json.dumps(ctrl.snapshot(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "probe":
        ok, detail = ctrl.probe_model()
        print(("✅ " if ok else "❌ ") + detail)
        return 0 if ok else 1
    if cmd in ("happ-off", "happ-on"):
        ok, detail = ctrl.happ_service("stop" if cmd == "happ-off" else "start")
        print(f"{'готово' if ok else 'не вышло'}: {detail}")
        return 0 if ok else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
