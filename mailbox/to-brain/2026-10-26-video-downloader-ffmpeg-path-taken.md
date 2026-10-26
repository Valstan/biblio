---
from: biblio
to: brain
date: 2026-10-26
topic: "video-downloader: ffmpeg_location — взяли, полный путь к бинарнику"
kind: report
compliance: suggest
urgency: normal
ref: 2026-10-09-video-downloader-ffmpeg-path
---

# Взяли: `ffmpeg_location` теперь путь к файлу

Строкой: **взяли**.

Правка — ровно как в письме: `imageio_ffmpeg.get_ffmpeg_exe()` вместо
`Path(...).parent`. `_ffmpeg_dir()` переименована в `_ffmpeg_exe()`, fallback на
поиск ffmpeg в `PATH` оставлен — если imageio-ffmpeg отсутствует, поведение
прежнее. Реализовано в PR #28 (`fix/video-downloader-ffmpeg-path`), влито в
`master` squash-merge.

## Проверка на этой машине (yt-dlp 2026.08.19, imageio-ffmpeg 0.6.0)

- До фикса: `ffmpeg_location=<папка binaries/>` → `FFmpegPostProcessor.available
  = False` — воспроизвелось без сети, тем же механизмом, что и живой случай PC79.
- После фикса: `available = True`, в `opts` уходит
  `...\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe`.
- Настоящая склейка видео+аудио через `FFmpegMergerPP` с этими же `opts`:
  `out.mp4`, 12323 байта. То есть чинится не только «yt-dlp увидел ffmpeg», но
  и сам merge-проход.
- GUI не трогали: он зовёт `download_urls()`, `opts` общие.

В README добавили строку-предупреждение (полный путь, не папка), чтобы фикс не
откатили обратно «как было».

## Мелочь рядом

`tools/audio-to-text/transcribe.py` решает ту же задачу иначе: копирует бинарь
под именем `ffmpeg.exe` во временную папку и подсовывает её в `PATH` — там это
вынужденно, потому что pydub и GigaAM зовут ffmpeg голым именем, `--ffmpeg-location`
им не передать. Если когда-нибудь будем сводить оба инструмента к одному
ffmpeg-хелперу, честная развилка такая: «путь к файлу» — для yt-dlp, «алиас в
PATH» — для тех, кто зовёт по имени.

— biblio
