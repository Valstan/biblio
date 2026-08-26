# -*- coding: utf-8 -*-
"""video-downloader: CLI-ядро «страница → все видео на диск».

Движок — yt-dlp (1800+ экстракторов + generic-экстрактор, который находит
встроенные плееры/HLS на произвольных страницах). ffmpeg берётся из
imageio-ffmpeg (ставится pip'ом, системная установка не нужна) — нужен для
склейки видео+аудио и HLS-фрагментов.

Использование из командной строки:
    python download.py <url> [<url> ...] [-o <папка>]

Из кода/GUI:
    from download import download_urls
    download_urls(["https://..."], out_dir, log=print)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def default_download_dir() -> Path:
    """Папка «Загрузки» пользователя (fallback — домашняя)."""
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.home()


def _ffmpeg_dir() -> str | None:
    try:
        import imageio_ffmpeg
        return str(Path(imageio_ffmpeg.get_ffmpeg_exe()).parent)
    except Exception:
        return None  # yt-dlp поищет ffmpeg в PATH


def download_urls(urls, out_dir, log=print, progress=None, audio_only=False,
                  cookies_browser=None):
    """Скачать все видео по каждому URL (страница = все найденные на ней видео).

    log(str)              — строки статуса для GUI/консоли.
    progress(float 0..1)  — прогресс текущего файла (может дёргаться между файлами).
    Возвращает (ok_count, err_count).
    """
    import yt_dlp

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"ok": 0, "err": 0}

    def hook(d):
        if d["status"] == "downloading" and progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(d.get("downloaded_bytes", 0) / total)
        elif d["status"] == "finished":
            if progress:
                progress(1.0)
            log(f"  ✔ {Path(d.get('filename', '?')).name}")
            stats["ok"] += 1

    class _Logger:
        # yt-dlp многословен; наружу отдаём только предупреждения и ошибки.
        def debug(self, msg):
            if msg.startswith("[download] Destination:"):
                log("  ⇣ " + msg.split("Destination:", 1)[1].strip())
        def info(self, msg): pass
        def warning(self, msg): log("  ⚠ " + msg)
        def error(self, msg):
            log("  ✖ " + msg)
            stats["err"] += 1

    opts = {
        "outtmpl": str(out_dir / "%(title).120B [%(id)s].%(ext)s"),
        "format": "bv*+ba/b" if not audio_only else "ba/b",
        "merge_output_format": "mp4",
        # Страница с несколькими плеерами = playlist у generic-экстрактора:
        # качаем всё, ошибки одного видео не роняют остальные.
        "ignoreerrors": True,
        "noplaylist": False,
        "concurrent_fragment_downloads": 4,
        "retries": 5,
        "fragment_retries": 5,
        "progress_hooks": [hook],
        "logger": _Logger(),
        "windowsfilenames": True,
    }
    if cookies_browser:
        # Сайты за логином: берём cookies из браузера владельца (локально).
        opts["cookiesfrombrowser"] = (cookies_browser,)
    ffdir = _ffmpeg_dir()
    if ffdir:
        opts["ffmpeg_location"] = ffdir

    with yt_dlp.YoutubeDL(opts) as ydl:
        for url in urls:
            url = url.strip()
            if not url:
                continue
            log(f"Страница: {url}")
            try:
                ydl.download([url])
            except Exception as e:  # ignoreerrors ловит почти всё, это страховка
                log(f"  ✖ не удалось: {e}")
                stats["err"] += 1
    return stats["ok"], stats["err"]


def main():
    # Windows-консоль часто в cp866/cp1251 — не даём Unicode-значкам ронять CLI.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Скачать все видео со страниц(ы)")
    p.add_argument("urls", nargs="+", help="URL страниц или видео")
    p.add_argument("-o", "--out", default=str(default_download_dir()),
                   help="папка сохранения (по умолчанию — Загрузки)")
    p.add_argument("--audio", action="store_true", help="только аудио-дорожка")
    p.add_argument("--cookies-browser", default=None, metavar="BROWSER",
                   help="взять cookies из браузера для сайтов за логином "
                        "(chrome/edge/firefox)")
    a = p.parse_args()
    ok, err = download_urls(a.urls, a.out, audio_only=a.audio,
                            cookies_browser=a.cookies_browser)
    print(f"Готово: скачано {ok}, ошибок {err}. Папка: {a.out}")
    sys.exit(1 if (err and not ok) else 0)


if __name__ == "__main__":
    main()
