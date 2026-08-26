#!/usr/bin/env python3
"""audio-to-text — локальное распознавание речи (GigaAM v2) + пунктуация (Silero). CLI-ядро.

Один файл аудио (ogg/mp3/wav/m4a/…) → распознанный русский текст в stdout.
Всё локально: ничего не уходит наружу. ffmpeg берётся из пакета imageio-ffmpeg.

Пайплайн:
  1. ffmpeg декодирует вход → 16 кГц моно WAV.
  2. Длинные файлы режутся по тишине (GigaAM transcribe берёт ≤25с за раз).
  3. Каждый фрагмент распознаётся GigaAM (сырой текст без пунктуации).
  4. Опц.: Silero `silero_te` ставит пунктуацию и заглавные; фрагменты
     склеиваются как абзацы (по границам тишины).

Без pyannote/HF_TOKEN и без облака — полностью оффлайн после разовой загрузки моделей.

Использование:
    python transcribe.py <audio> [--model v2_rnnt] [--no-punct] [--out текст.txt]

Модели GigaAM (пакет gigaam 0.1.0): v2_rnnt (точная, по умолч.), v2_ctc (быстрее),
v1_rnnt / v1_ctc (старее). v3/e2e в pip-пакете пока нет — пунктуацию даёт Silero.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

# В GUI (pythonw) каждый вызов ffmpeg-подпроцесса на доли секунды мигает окном
# консоли (мешает работе). Гасим централизованно: патчим subprocess.Popen, чтобы
# ВСЕ дочерние процессы — наш ffmpeg И внутренний ffmpeg внутри GigaAM — стартовали
# без окна. CREATE_NO_WINDOW = 0x08000000 (Windows).
if os.name == "nt":
    _CREATE_NO_WINDOW = 0x08000000
    _popen_init = subprocess.Popen.__init__

    def _silent_popen_init(self, *a, **kw):  # noqa: ANN001
        kw["creationflags"] = kw.get("creationflags", 0) | _CREATE_NO_WINDOW
        _popen_init(self, *a, **kw)

    subprocess.Popen.__init__ = _silent_popen_init  # type: ignore[method-assign]

# В pythonw.exe (GUI без консоли) sys.stdout/stderr = None → torch.hub при загрузке
# модели печатает в поток и падает AttributeError, из-за чего пунктуация молча
# отключалась. Подменяем None на devnull. Плюс Windows-консоль по умолчанию cp1251:
# кириллица/«→» в перенаправлённом stdout падают UnicodeEncodeError → ставим UTF-8.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# GigaAM transcribe рассчитан на ≤25с; держим запас на джиттер нарезки.
CHUNK_SEC = 24.0
SILENCE_DB = "-35dB"
MIN_SILENCE = 0.35
DEFAULT_MODEL = os.environ.get("A2T_MODEL", "v2_rnnt")

# Лимиты Telegram-бота SaluteSpeech (@smartspeech_sber_bot): ≤5 мин, ≤20 МБ, МОНО,
# форматы MP3/WAV/FLAC/OggOpus. Режем по тишине с запасом под 5 мин; моно-mp3 в
# 4:40 ≈ 2–3 МБ — до 20 МБ далеко, так что связывает именно длительность.
BOT_MAX_SEC = 280.0


def _log(msg: str, progress=None) -> None:
    """Сообщение о ходе: в коллбэк GUI, иначе в stderr (stdout — для текста)."""
    if progress is not None:
        progress(msg)
    else:
        print(msg, file=sys.stderr, flush=True)


# ---- ffmpeg ------------------------------------------------------------------

def _ffmpeg_exe() -> str:
    """ffmpeg из imageio-ffmpeg, видимый как бинарь `ffmpeg` в PATH.

    GigaAM (`preprocess.load_audio`) и pydub зовут ffmpeg **по голому имени**
    из PATH. imageio-ffmpeg хранит бинарь как `ffmpeg-win-….exe`, поэтому
    подкладываем рядом алиас `ffmpeg.exe` и добавляем его каталог в PATH.
    """
    import imageio_ffmpeg

    real = imageio_ffmpeg.get_ffmpeg_exe()
    bindir = os.path.join(tempfile.gettempdir(), "a2t_ffmpeg")
    os.makedirs(bindir, exist_ok=True)
    alias = os.path.join(bindir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not os.path.exists(alias):
        import shutil

        shutil.copy2(real, alias)
    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    try:
        from pydub import AudioSegment

        AudioSegment.converter = alias
        AudioSegment.ffmpeg = alias
    except Exception:
        pass
    return alias


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _decode_to_wav(src: str, exe: str) -> str:
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    _run_ffmpeg([exe, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav])
    return wav


def _duration(wav: str) -> float:
    import soundfile as sf

    info = sf.info(wav)
    return info.frames / float(info.samplerate)


def _silence_midpoints(wav: str, exe: str) -> list[float]:
    proc = subprocess.run(
        [exe, "-i", wav, "-af",
         f"silencedetect=noise={SILENCE_DB}:d={MIN_SILENCE}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    points: list[float] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip())
            except (IndexError, ValueError):
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip())
                points.append((start + end) / 2.0)
            except (IndexError, ValueError):
                pass
            start = None
    return points


def _windows(duration: float, silences: list[float],
             max_chunk: float = CHUNK_SEC) -> list[tuple[float, float]]:
    bounds = [0.0]
    while bounds[-1] < duration - 0.05:
        target = bounds[-1] + max_chunk
        if target >= duration:
            bounds.append(duration)
            break
        cand = [s for s in silences if bounds[-1] + 1.0 < s <= target]
        bounds.append(cand[-1] if cand else target)
    if len(bounds) < 2:
        return [(0.0, duration)]
    return list(zip(bounds[:-1], bounds[1:]))


def _slice(wav: str, a: float, b: float, exe: str) -> str:
    fd, out = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    _run_ffmpeg([exe, "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", wav,
                 "-ac", "1", "-ar", "16000", out])
    return out


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---- движок (модель грузится один раз, переиспользуется для пачки) -----------

class Transcriber:
    """Держит загруженную модель GigaAM (+ ленивый Silero) для пачки файлов."""

    def __init__(self, model_name: str = DEFAULT_MODEL, progress=None) -> None:
        self.exe = _ffmpeg_exe()
        import gigaam

        _log(f"Загрузка модели {model_name} (первый раз — скачивание)…", progress)
        self.model = gigaam.load_model(model_name)
        self.model_name = model_name
        self._apply_te = None  # ленивый Silero text-enhancement

    def _enhance(self, text: str, progress=None) -> str:
        """Пунктуация + заглавные через Silero `silero_te`. Сбой → сырой текст."""
        if not text.strip():
            return text
        if self._apply_te is None:
            try:
                import torch

                _log("Загрузка модели пунктуации (первый раз — скачивание)…", progress)
                _, _, _, _, apply_te = torch.hub.load(
                    "snakers4/silero-models", "silero_te", trust_repo=True)
                self._apply_te = apply_te
            except Exception as exc:  # noqa: BLE001
                _log(f"Пунктуация недоступна ({exc}) — сырой текст.", progress)
                self._apply_te = False  # не пытаться снова
        if not self._apply_te:
            return text
        try:
            return self._apply_te(text, lan="ru")
        except Exception:
            return text

    def _chunks(self, src: str, progress=None) -> list[str]:
        wav = _decode_to_wav(src, self.exe)
        try:
            duration = _duration(wav)
            if duration <= CHUNK_SEC + 1.0:
                _log("Распознавание…", progress)
                return [str(self.model.transcribe(wav)).strip()]
            windows = _windows(duration, _silence_midpoints(wav, self.exe))
            parts: list[str] = []
            for i, (a, b) in enumerate(windows, 1):
                _log(f"Фрагмент {i}/{len(windows)} ({a:.0f}–{b:.0f}с)…", progress)
                piece = _slice(wav, a, b, self.exe)
                try:
                    parts.append(str(self.model.transcribe(piece)).strip())
                finally:
                    _safe_remove(piece)
            return parts
        finally:
            _safe_remove(wav)

    def transcribe_file(self, src: str, punctuate: bool = True, progress=None) -> str:
        """Распознать файл целиком. punctuate → пунктуация + абзацы по тишине."""
        if not os.path.isfile(src):
            raise FileNotFoundError(src)
        chunks = [c for c in self._chunks(src, progress) if c.strip()]
        if punctuate:
            chunks = [self._enhance(c, progress) for c in chunks]
            return "\n\n".join(c for c in chunks if c.strip())  # фрагмент-тишина = абзац
        return " ".join(chunks)


def transcribe_file(src: str, model_name: str = DEFAULT_MODEL,
                    punctuate: bool = True, progress=None) -> str:
    """Удобная обёртка для одного файла (CLI). Для пачки — класс Transcriber."""
    return Transcriber(model_name, progress).transcribe_file(src, punctuate, progress)


def split_for_bot(src: str, out_dir: str | None = None,
                  max_sec: float = BOT_MAX_SEC, progress=None) -> list[str]:
    """Нарезать аудио на МОНО-mp3 фрагменты ≤max_sec под бота SaluteSpeech.

    Режет по точкам тишины (как распознавание, но окно = лимит бота, не 25с),
    чтобы не рвать на полуслове. Возвращает список путей к фрагментам. Папка по
    умолчанию — `<имя>_parts/` рядом с исходником.
    """
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    exe = _ffmpeg_exe()
    base = os.path.splitext(os.path.basename(src))[0]
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(src)), base + "_parts")
    os.makedirs(out_dir, exist_ok=True)

    wav = _decode_to_wav(src, exe)
    try:
        duration = _duration(wav)
        windows = _windows(duration, _silence_midpoints(wav, exe), max_chunk=max_sec)
        outs: list[str] = []
        for i, (a, b) in enumerate(windows, 1):
            _log(f"Фрагмент {i}/{len(windows)} ({a:.0f}–{b:.0f}с)…", progress)
            out = os.path.join(out_dir, f"{base}_part{i:02d}.mp3")
            # моно mp3 (бот: моно, MP3 ок); срез из ОРИГИНАЛА, без потери качества декодом в 16к
            _run_ffmpeg([exe, "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
                         "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4", out])
            outs.append(out)
        return outs
    finally:
        _safe_remove(wav)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Локальное аудио→текст (GigaAM v2 + Silero)")
    ap.add_argument("audio", help="путь к аудиофайлу (ogg/mp3/wav/m4a/…)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"модель GigaAM (по умолчанию {DEFAULT_MODEL})")
    ap.add_argument("--no-punct", action="store_true",
                    help="не ставить пунктуацию (сырой текст)")
    ap.add_argument("--out", help="сохранить результат в файл .txt (UTF-8)")
    ap.add_argument("--split", action="store_true",
                    help="не распознавать, а нарезать на фрагменты для бота SaluteSpeech (моно mp3 ≤5 мин)")
    ap.add_argument("--max-sec", type=float, default=BOT_MAX_SEC,
                    help=f"макс. длина фрагмента при --split (по умолч. {BOT_MAX_SEC:.0f}с)")
    ap.add_argument("--out-dir", help="папка для фрагментов при --split")
    args = ap.parse_args(argv)

    if args.split:
        try:
            parts = split_for_bot(args.audio, args.out_dir, args.max_sec)
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
        print(f"Нарезано фрагментов: {len(parts)}", file=sys.stderr)
        for p in parts:
            print(p)
        return 0

    try:
        text = transcribe_file(args.audio, args.model, punctuate=not args.no_punct)
    except Exception as exc:  # noqa: BLE001 — CLI: показать причину, не трейсбек
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Сохранено: {args.out}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
