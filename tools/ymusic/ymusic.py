#!/usr/bin/env python3
"""ymusic — скачивание треков плейлиста Яндекс.Музыки локально.

Подкоманды:
  list     <playlist>            показать треклист (без авторизации)
  auth                            получить OAuth-токен (подтверждение по ссылке)
  download <playlist> [-o DIR]    скачать все треки + теги + обложки
  upgrade  <playlist> [-o DIR]    перебрать варианты качества и перекачать лучшим
  tag      <DIR>                  перетегировать файлы в папке (теги + обложки)

Плейлист: https://music.yandex.ru/playlists/<uuid> или просто <uuid>.
Токен: ~/.ymusic/token.txt или переменная окружения YMUSIC_TOKEN.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# --- ключи подписи и клиент, которые Яндекс.Музыка использует в своих приложениях ---
# (см. плейбук brain_matrica/docs/playbooks/yandex-music-download.md)
KEYS = {
    "desktop": ("kzqU4XhfCaY6B6JTHODeq5", "YandexMusicDesktopAppWindows/5.95.0"),
    "web": ("7tvSmFbyf5hJnIHhCimDDD", "YandexMusicWebNext"),
}
OAUTH_CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
OAUTH_CLIENT_SECRET = "53bc75238f0c4d08a118e51fe9203300"
OAUTH = "https://oauth.yandex.ru"
API = "https://api.music.yandex.net"

QUALITIES = ["lossless", "hq", "nq"]
CODECS = ["flac", "flac-mp4", "aac-mp4", "he-aac-mp4", "aac", "he-aac", "mp3"]
TRANSPORTS = ["raw", "encraw"]
CODEC_EXT = {"flac": "flac", "flac-mp4": "m4a", "aac-mp4": "m4a", "he-aac-mp4": "m4a",
             "aac": "m4a", "he-aac": "m4a", "mp3": "mp3"}
CODEC_RANK = {"flac": 3, "flac-mp4": 3, "mp3": 2, "aac": 2, "he-aac": 2,
              "aac-mp4": 2, "he-aac-mp4": 2}
TOKEN_DIR = os.path.join(os.path.expanduser("~"), ".ymusic")


# ─────────────────────────────── утилиты ───────────────────────────────
def token_path() -> str:
    return os.path.join(TOKEN_DIR, "token.txt")


def load_token() -> str | None:
    env = os.environ.get("YMUSIC_TOKEN")
    if env:
        return env.strip()
    path = token_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    return None


def save_token(token: str) -> None:
    os.makedirs(TOKEN_DIR, exist_ok=True)
    with open(token_path(), "w", encoding="utf-8") as fh:
        fh.write(token)


def safe_name(name: str) -> str:
    name = name.replace(":", "-")
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return re.sub(r"\s+", " ", name).strip()


def http(url: str, headers: dict | None = None, method: str = "GET",
         data: bytes | None = None, timeout: int = 60) -> tuple[int, bytes]:
    hdrs = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def sign(secret: str, ts: int, tid: str, quality: str, codecs: str,
         transport: str) -> str:
    message = f"{ts}{tid}{quality}{codecs}{transport}"
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()[:-1]


def playlist_uuid(source: str) -> str:
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                      source)
    if not match:
        raise SystemExit(f"Не понял плейлист: {source!r} (нужен UUID или ссылка)")
    return match.group(1)


# ─────────────────────────────── плейлист ───────────────────────────────
def fetch_playlist(source: str) -> list[dict]:
    """Список треков плейлиста. Работает без авторизации."""
    uuid = playlist_uuid(source)
    status, body = http(f"{API}/playlist/{uuid}",
                         headers={"Accept": "application/json",
                                  "Referer": "https://music.yandex.ru/",
                                  "X-Retpath-Y": "https://music.yandex.ru/"})
    if status != 200:
        raise SystemExit(f"Плейлист не отдан: HTTP {status}")
    result = json.loads(body.decode("utf-8", "replace")).get("result", {})
    tracks = []
    for item in result.get("tracks") or []:
        track = item.get("track") or {}
        albums = track.get("albums") or []
        album = albums[0] if albums else {}
        tracks.append({
            "index": item.get("originalIndex", len(tracks)),
            "track_id": track.get("id"),
            "album_id": album.get("id"),
            "title": track.get("title"),
            "artists": ", ".join(a["name"] for a in track.get("artists", [])),
            "album": album.get("title"),
            "year": album.get("year"),
            "duration_ms": track.get("durationMs"),
            "cover_uri": album.get("coverUri") or "",
        })
    return tracks


# ─────────────────────────────── авторизация ───────────────────────────────
def authorize() -> str | None:
    """OAuth device-flow: код подтверждения в браузере, без пароля."""
    payload = urllib.parse.urlencode({
        "client_id": OAUTH_CLIENT_ID, "device_id": "ymusic-cli",
        "device_name": "ymusic"}).encode()
    status, body = http(f"{OAUTH}/device/code", method="POST", data=payload,
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "Accept": "application/json"})
    if status != 200:
        print(f"Не удалось получить код: HTTP {status}", file=sys.stderr)
        return None
    code = json.loads(body.decode("utf-8", "replace"))
    print(f"\n  Откройте {code['verification_url']} и введите код: {code['user_code']}")
    print(f"  (код живёт {int(code.get('expires_in', 300)) // 60} минут)\n")
    deadline = time.time() + int(code.get("expires_in", 300))
    interval = int(code.get("interval", 5))
    while time.time() < deadline:
        payload = urllib.parse.urlencode({
            "grant_type": "device_code", "code": code["device_code"],
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET}).encode()
        status, body = http(f"{OAUTH}/token", method="POST", data=payload,
                            headers={"Content-Type": "application/x-www-form-urlencoded",
                                     "Accept": "application/json"})
        try:
            result = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            result = {}
        if status == 200 and result.get("access_token"):
            save_token(result["access_token"])
            print("  Готово: токен сохранён.\n")
            return result["access_token"]
        error = result.get("error")
        if error and error not in ("authorization_pending", "slow_down"):
            print(f"  Ошибка авторизации: {result}", file=sys.stderr)
            return None
        time.sleep(interval)
    print("  Время кода истекло.", file=sys.stderr)
    return None


# ─────────────────────────────── файл трека ───────────────────────────────
def file_info(token: str, tid: str, quality: str, codecs: list[str], transport: str,
              key_name: str) -> dict | None:
    """Информация о файле трека. codecs — список, сервер сам выбирает лучший."""
    secret, client = KEYS[key_name]
    ts = int(time.time())
    url = f"{API}/get-file-info?" + urllib.parse.urlencode({
        "ts": ts, "trackId": tid, "quality": quality, "codecs": ",".join(codecs),
        "transports": transport,
        "sign": sign(secret, ts, tid, quality, "".join(codecs), transport)})
    status, body = http(url, headers={
        "Accept": "application/json", "Referer": "https://music.yandex.ru/",
        "X-Retpath-Y": "https://music.yandex.ru/",
        "Authorization": f"OAuth {token}", "X-Yandex-Music-Client": client})
    if status != 200:
        return None
    result = json.loads(body.decode("utf-8", "replace")).get("result", {})
    infos = result.get("downloadInfos") or (
        [result["downloadInfo"]] if "downloadInfo" in result else [])
    infos = [i for i in infos if i]
    return infos[0] if infos else None


def best_info(token: str, tid: str, deep: bool = False) -> dict | None:
    """Лучшая доступная рендиция: максимальный битрейт, при равенстве — lossless.

    deep=False — один запрос, сервер сам отдаёт лучшее доступное (быстро).
    deep=True — полный перебор качеств × кодеков × транспортов (медленно, но
    находит варианты, которые сервер не отдаёт в одном запросе).
    """
    if not deep:
        info = file_info(token, tid, "lossless", CODECS, "raw", "desktop")
        if info is None:
            info = file_info(token, tid, "lossless", CODECS, "raw", "web")
        return info

    found = []
    for key_name in KEYS:
        for quality in QUALITIES:
            for codec in CODECS:
                for transport in TRANSPORTS:
                    info = file_info(token, tid, quality, [codec], transport, key_name)
                    if info:
                        found.append((info.get("bitrate") or 0,
                                      CODEC_RANK.get(info.get("codec"), 1), info))
    if not found:
        return None
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return found[0][2]


# ─────────────────────────────── скачивание ───────────────────────────────
def fetch_cover(uri: str) -> bytes | None:
    if not uri:
        return None
    for suffix in ("300x300", "200x200", "orig"):
        status, body = http("https://" + uri.replace("%%", suffix))
        if status == 200 and 0 < len(body) < 300000:
            return body
    return None


def download_bytes(info: dict) -> bytes:
    last = None
    for url in info.get("urls", []):
        try:
            status, body = http(url, timeout=300)
            if status == 200:
                return body
            last = f"HTTP {status}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"не скачался ни один URL ({last})")


def decrypt(data: bytes, key: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    decryptor = Cipher(algorithms.AES(bytes.fromhex(key)),
                       modes.CTR(bytes(16))).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def write_tags(path: str, item: dict, cover: bytes | None) -> str:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".m4a":
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(path)
            if "covr" in audio:
                del audio["covr"]
            audio["\xa9nam"] = [item["title"] or ""]
            audio["\xa9ART"] = [item["artists"] or ""]
            audio["\xa9alb"] = [item["album"] or ""]
            if item.get("year"):
                audio["\xa9day"] = [str(item["year"])]
            audio["trkn"] = [(int(item["index"]) + 1, 0)]
            if cover:
                audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        elif ext == ".mp3":
            from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TRCK
            from mutagen.mp3 import MP3
            audio = MP3(path)
            if audio.tags.getall("APIC"):
                audio.tags.delall("APIC")
            audio["TIT2"] = TIT2(encoding=3, text=item["title"] or "")
            audio["TPE1"] = TPE1(encoding=3, text=item["artists"] or "")
            audio["TALB"] = TALB(encoding=3, text=item["album"] or "")
            if item.get("year"):
                audio["TDRC"] = TDRC(encoding=3, text=str(item["year"]))
            audio["TRCK"] = TRCK(encoding=3, text=str(int(item["index"]) + 1))
            audio.save()
            if cover:
                audio = MP3(path)
                audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3,
                                    desc="Cover", data=cover))
                audio.save()
        else:
            return f"неизвестный формат {ext} — теги пропущены"
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def save_track(item: dict, info: dict, out_dir: str, do_tag: bool = True) -> dict:
    data = download_bytes(info)
    if info.get("transport") == "encraw" and info.get("key"):
        data = decrypt(data, info["key"])
    ext = CODEC_EXT.get(info.get("codec"), "mp3")
    name = safe_name(f'{int(item["index"]) + 1:02d}. {item["artists"]} - {item["title"]}')
    path = os.path.join(out_dir, f"{name}.{ext}")
    with open(path, "wb") as fh:
        fh.write(data)
    note = "ok"
    if do_tag:
        note = write_tags(path, item, fetch_cover(item.get("cover_uri") or ""))
    return {"index": item["index"], "ok": True, "file": os.path.basename(path),
            "bytes": len(data), "quality": info.get("quality"),
            "codec": info.get("codec"), "bitrate": info.get("bitrate"),
            "transport": info.get("transport"), "tags": note}


def download_playlist(source: str, out_dir: str, deep: bool = False,
                      workers: int = 4) -> list[dict]:
    token = load_token()
    if not token:
        print("Токена нет — запрашиваю подтверждение.\n")
        token = authorize()
        if not token:
            raise SystemExit("Без токена файлы недоступны (API отдаёт 403 not-allowed).")
    tracks = fetch_playlist(source)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Треков в плейлисте: {len(tracks)} → {out_dir}\n")
    results: list[dict] = []

    def work(item: dict) -> dict:
        for attempt in range(3):
            try:
                found = best_info(token, item["track_id"], deep=deep)
                if not found:
                    raise RuntimeError("нет file-info")
                info = found[0]
                return save_track(item, info, out_dir)
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    return {"index": item["index"], "ok": False,
                            "title": item["title"], "error": f"{type(exc).__name__}: {exc}"}
                time.sleep(2 * (attempt + 1))
        return {"index": item["index"], "ok": False, "error": "unknown"}

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(work, tracks):
            results.append(result)
            if result.get("ok"):
                print(f'  [OK ] {result["file"]}  {result["bitrate"]} кбит/с '
                      f'{result["codec"]}  {result["bytes"] // 1024} КБ', flush=True)
            else:
                print(f'  [ERR] {result.get("title")}: {result.get("error")}', flush=True)

    results.sort(key=lambda r: r["index"])
    with open(os.path.join(out_dir, "ymusic-report.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    ok = [r for r in results if r.get("ok")]
    print(f"\nГотово: {len(ok)}/{len(results)}, "
          f"{sum(r['bytes'] for r in ok) / 1048576:.1f} МБ")
    return results


def retag_folder(out_dir: str) -> None:
    """Перетегировать файлы в папке по плейлисту (по порядковому номеру в имени)."""
    token = load_token()
    if not token:
        raise SystemExit("Нужен токен: команда auth.")
    tracks = fetch_playlist(input("Ссылка или UUID плейлиста: ").strip())
    by_index = {int(t["index"]): t for t in tracks}
    for name in sorted(os.listdir(out_dir)):
        match = re.match(r"^(\d+)\.", name)
        if not match:
            continue
        item = by_index.get(int(match.group(1)) - 1)
        if not item:
            continue
        path = os.path.join(out_dir, name)
        note = write_tags(path, item, fetch_cover(item.get("cover_uri") or ""))
        print(f"  {name} → {note}")


def cmd_list(source: str, as_json: str | None) -> None:
    tracks = fetch_playlist(source)
    for item in tracks:
        minutes = (item["duration_ms"] or 0) // 60000
        seconds = (((item["duration_ms"] or 0) % 60000) // 1000) // 60
        print(f'{int(item["index"]) + 1:>2}. {item["artists"]} — {item["title"]} '
              f'[{item["album"]}, {item["year"]}] {minutes}:{seconds:02d}')
    print(f"\nВсего: {len(tracks)}")
    if as_json:
        with open(as_json, "w", encoding="utf-8") as fh:
            json.dump(tracks, fh, ensure_ascii=False, indent=1)
        print(f"Сохранено: {as_json}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ymusic", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="показать треклист (без авторизации)")
    p_list.add_argument("playlist")
    p_list.add_argument("--json", help="сохранить треклист в JSON")

    sub.add_parser("auth", help="получить OAuth-токен")

    p_dl = sub.add_parser("download", help="скачать все треки")
    p_dl.add_argument("playlist")
    p_dl.add_argument("-o", "--out", default=os.path.join(os.path.expanduser("~"),
                                                          "Music", "ymusic"))
    p_dl.add_argument("--deep", action="store_true",
                      help="полный перебор качеств/кодеков/транспортов (медленнее)")
    p_dl.add_argument("--workers", type=int, default=4)

    p_up = sub.add_parser("upgrade", help="перекачать в лучшем доступном качестве")
    p_up.add_argument("playlist")
    p_up.add_argument("-o", "--out", required=True)
    p_up.add_argument("--workers", type=int, default=4)

    p_tag = sub.add_parser("tag", help="перетегировать файлы в папке")
    p_tag.add_argument("folder")

    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list(args.playlist, args.json)
    elif args.cmd == "auth":
        return 0 if authorize() else 1
    elif args.cmd == "download":
        download_playlist(args.playlist, args.out, deep=args.deep,
                          workers=args.workers)
    elif args.cmd == "upgrade":
        download_playlist(args.playlist, args.out, deep=True, workers=args.workers)
    elif args.cmd == "tag":
        retag_folder(args.folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
