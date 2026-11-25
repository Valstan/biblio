"""Чтение файлов оператора — содержимое уходит модели в контекст.

Заказ владельца (24.11, промт #90): «в чат встроенный в МиниГене добавить
инструмент добавления файлов: картинки, документы различных форматов — текстовые,
XML, XLS, DOC и так далее, все самые популярные форматы, — чтобы отправлять их
нейросети на съедение в контекст».

**Что здесь и почему так**

- чтение живёт **в мосте** (Python), а не в окне: окно не ходит на диск, оно
  присылает путь и получает содержимое (тот же принцип, что у всех кнопок);
- **ничего не пишется**: модуль только читает. Каталог файла не меняется,
  временных копий рядом с ним не появляется;
- **потолки обязательны**: файл на 200 МБ или книга на 200 000 строк ⇒
  оператору честное сообщение, а не зависание окна;
- **нечитаемое — честно**: битый, зашифрованный или неподдерживаемый файл даёт
  сообщение с полным путём, а не пустоту («файл прочитан, вот он»);
- формат определяется по расширению **и** по содержимому (файл `.xls`, который
  на самом деле zip, читается как книга Excel).

Форматы:

| семейство | что происходит |
|---|---|
| текст (txt, md, csv, json, xml, код и прочие) | как есть, с потолком длины |
| текст без расширения (`README`, `Makefile`) | определяется по содержимому — и читается |
| картинки (png, jpg, gif, bmp, webp) | **блок изображения** (base64), а не текст |
| картинка с чужим расширением (`.txt`) | определяется по содержимому — и всё равно картинка |
| xlsx / xlsm | zip + разбор XML: разделители и общие строки |
| docx / docm | zip + `word/document.xml`: абзацы |
| pptx / pptm | zip + слайды: строки |
| odt / ods / odp | zip + `content.xml`: абзацы |
| pdf | текст из потоков содержимого (сжатые и несжатые) |
| rtf | текст без управляющих слов |
| doc / xls (старые, Word 97-2003 / Excel 97-2003) | составной файл OLE: таблица кусков |

⚠️ Старые `.doc`/`.xls` читаются «как получится»: если разбор не идёт — честное
сообщение с подсказкой (сохранить в `.docx`/`.xlsx`), а не мусор в контексте
модели.

⚠️ **Потолки обязательны** (живая проба 26.11): без них файл-«бомба» — 200 КБ
на диске, раздувающийся в сотни мегабайт, — вешал чтение дольше 10 минут.
Поэтому у чтения есть потолки: размер файла, размер картинки, длина текста,
число строк (**на всю книгу**), **распакованный** объём (zip и PDF) и
**время** чтения. Превышение ⇒ честное сообщение оператору **с полным путём**,
а не зависание и не молчание.
"""

from __future__ import annotations

import base64
import io
import re
import threading
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any

#: Текстовые расширения (читаются как есть).
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".log", ".htm",
    ".html", ".css", ".scss", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".py",
    ".rb", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".cs", ".php", ".sh",
    ".bash", ".ps1", ".bat", ".cmd", ".sql", ".diff", ".patch", ".srt", ".vtt",
    ".tex", ".env",
}

#: Картинки: расширение → тип содержимого (его же ждёт вендор).
IMAGE_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

#: Документы (текст извлекается).
DOC_EXTS = {
    ".xlsx", ".xlsm", ".xltx", ".docx", ".docm", ".dotx", ".pptx", ".pptm",
    ".potx", ".odt", ".ods", ".odp", ".odg", ".pdf", ".rtf",
    # старые форматы Office — читаются best effort
    ".doc", ".xls", ".ppt",
}

#: Потолок размера файла, который вообще читаем (байт).
MAX_FILE_BYTES = 20 * 1024 * 1024
#: Потолок картинки (байт). Платформа объявляет 10 МБ (`max_image_bytes_inline`).
MAX_IMAGE_BYTES = 10 * 1024 * 1024
#: Потолок текста, который уходит в контекст (символов).
MAX_TEXT_CHARS = 200_000
#: Потолок строк таблицы (xlsx) и абзацев (docx).
MAX_ROWS = 5_000
#: Потолок символов, извлечённых из одного документа.
MAX_DOC_CHARS = 200_000
#: Потолок **распакованного** объёма (байт): zip-архив и PDF-поток могут
#: раздуться в сотни раз (200 КБ на диске → сотни мегабайт в памяти). Без
#: этого потолка чтение «бомбы» вешает мост на минуты.
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
#: Потолок времени чтения одного файла (секунд).
MAX_READ_SECONDS = 20


class FileError(Exception):
    """Файл не прочитан — сообщение оператору честное, с путём."""


# ─────────────────────────────────────────────── потолок времени чтения
#
# Чтение «бомбы» (zip или PDF, который раздувается в сотни раз) занимает
# минуты и вешает мост. Поэтому у чтения есть **время**: истекло — честное
# сообщение оператору, а не зависание. Счётчик живёт в потоке: чтения бывают
# параллельными.

class _Budget:
    """Потолок времени чтения одного файла."""

    def __init__(self, seconds: float = MAX_READ_SECONDS) -> None:
        self.limit = float(seconds)
        self.started = time.monotonic()

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.started) > self.limit


_LOCAL = threading.local()


def _budget() -> _Budget:
    """Потолок времени текущего чтения (для потока)."""
    current = getattr(_LOCAL, "budget", None)
    if current is None:
        current = _Budget()
        _LOCAL.budget = current
    return current


def _check_time() -> None:
    """Истёкло ли время чтения — тогда честно останавливаемся."""
    if _budget().expired:
        raise FileError("чтение заняло больше %d секунд — файл слишком сложный"
                        % int(MAX_READ_SECONDS))


# ─────────────────────────────────────── приложенные оператором файлы
#
# Нейросеть читает **только** то, что приложил оператор (кнопкой или
# перетаскиванием). Произвольные файлы с диска она не читает: иначе она могла
# бы утащить в переписку ключи и другие чужие файлы (D-038). Реестр живёт в
# памяти моста: переживать перезапуск программы нечего — содержимое уже в
# контексте хода.

_ATTACHED: dict[str, dict[str, Any]] = {}


def _key(path: str) -> str:
    """Ключ реестра: путь приведённый к настоящему виду."""
    try:
        return str(Path(str(path)).expanduser().resolve())
    except OSError:
        return str(path)


def register_attached(path: str, name: str = "", kind: str = "",
                      size: int = 0) -> dict[str, Any]:
    """Запомнить файл, приложенный оператором (путь → что о нём знаем)."""
    item = {"path": str(path), "name": name or Path(str(path)).name,
            "kind": kind, "size": int(size)}
    _ATTACHED[_key(path)] = item
    return item


def attached_paths() -> list[str]:
    """Пути файлов, приложенных оператором."""
    return sorted(_ATTACHED)


def is_attached(path: str) -> bool:
    """Прикладывал ли оператор этот файл."""
    return _key(path) in _ATTACHED


def attached_info(path: str) -> dict[str, Any]:
    """Что известно о приложенном файле (пусто — не приложен)."""
    return dict(_ATTACHED.get(_key(path), {}))


# ─────────────────────────────────────────────────────────────── определение

def classify(path: str) -> tuple[str, str]:
    """Вид файла: (kind, media_type).

    `kind`: `text` | `image` | `document` | `unknown`.
    """
    suffix = Path(str(path)).suffix.lower()
    if suffix in IMAGE_MEDIA:
        return "image", IMAGE_MEDIA[suffix]
    if suffix in DOC_EXTS:
        return "document", ""
    if suffix in TEXT_EXTS:
        return "text", ""
    # без расширения — пробуем по содержимому
    return "unknown", ""


def image_media_by_content(head: bytes) -> str:
    """Тип содержимого картинки по её первым байтам ('' — это не картинка).

    ⚠️ Расширение может врать: картинка, переименованная в `.txt`, должна
    уйти модели **картинкой**, а не текстом-мусором.
    """
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    return ""


def looks_like_text(data: bytes) -> bool:
    """Похоже ли содержимое на текст (а не на мусор).

    Текст без расширения (`README`, `Makefile`, `.gitignore`) — обычное дело:
    оператор прислал файл, а модель должна его прочитать, а не отказать.
    """
    if not data:
        return False
    head = data[:4096]
    if b"\x00" in head:
        return False                      # текст с нулями — это не текст
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass
    try:
        head.decode("cp1251")
        return True
    except UnicodeDecodeError:
        return False


def _kind_by_content(target: Path) -> tuple[str, str]:
    """Формат без расширения — по первым байтам."""
    try:
        head = target.read_bytes()[:16]
    except OSError:
        return "unknown", ""
    media = image_media_by_content(head)
    if media:
        return "image", media
    if head.startswith(b"%PDF"):
        return "document", ""
    if head.startswith(b"PK\x03\x04"):
        return "document", ""
    if head.startswith(b"{\\rtf"):
        return "document", ""
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "document", ""
    try:
        sample = target.read_bytes()[:4096]
    except OSError:
        return "unknown", ""
    if looks_like_text(sample):
        return "text", ""
    return "unknown", ""


def human_size(size: int) -> str:
    """Размер по-человечески: «12 КБ», «3 МБ»."""
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            if unit == "Б":
                return "%d %s" % (int(value), unit)
            return "%.1f %s" % (value, unit)
        value /= 1024
    return "%d Б" % size


# ──────────────────────────────────────────────────────────────── чтение

def read_for_context(path: str) -> dict[str, Any]:
    """Прочитать файл для контекста модели.

    Возвращает словарь:

    ```
    {"ok": bool, "path": str, "name": str, "kind": str, "media_type": str,
     "size": int, "text": str, "image": str, "warnings": [str], "error": str}
    ```

    ⚠️ Файл **не пишется** и **не меняется** — только читается.
    """
    raw_path = str(path or "").strip()
    result: dict[str, Any] = {
        "ok": False,
        "path": raw_path,
        "name": Path(raw_path).name if raw_path else "",
        "kind": "unknown",
        "media_type": "",
        "size": 0,
        "text": "",
        "image": "",
        "warnings": [],
        "error": "",
    }
    if not raw_path:
        result["error"] = "путь к файлу не указан"
        return result
    target = Path(raw_path).expanduser()
    if not target.exists():
        result["error"] = "файла нет: %s" % target
        return result
    if not target.is_file():
        result["error"] = "это не файл: %s" % target
        return result
    try:
        size = target.stat().st_size
    except OSError as exc:
        result["error"] = "не читается: %s" % exc
        return result
    result["size"] = size
    kind, media = classify(raw_path)
    result["kind"] = kind
    result["media_type"] = media
    if kind == "image":
        if size > MAX_IMAGE_BYTES:
            result["error"] = ("картинка больше %s — вендор такие не принимает"
                               % human_size(MAX_IMAGE_BYTES))
            return result
        try:
            data = target.read_bytes()
        except OSError as exc:
            result["error"] = "не читается: %s" % exc
            return result
        result["image"] = base64.b64encode(data).decode("ascii")
        result["ok"] = True
        return result
    if kind == "unknown":
        kind, media = _kind_by_content(target)
        result["kind"] = kind
        result["media_type"] = media
        if kind == "image":
            if size > MAX_IMAGE_BYTES:
                result["error"] = ("картинка больше %s — вендор такие не принимает"
                                   % human_size(MAX_IMAGE_BYTES))
                return result
            try:
                data = target.read_bytes()
            except OSError as exc:
                result["error"] = "не читается: %s" % exc
                return result
            result["image"] = base64.b64encode(data).decode("ascii")
            result["ok"] = True
            return result
    if kind == "unknown":
        result["error"] = ("формат не поддерживается: %s"
                           % (Path(raw_path).suffix or "без расширения"))
        return result
    if size > MAX_FILE_BYTES:
        result["error"] = ("файл больше %s — читать не буду"
                           % human_size(MAX_FILE_BYTES))
        return result
    try:
        data = target.read_bytes()
    except OSError as exc:
        result["error"] = "не читается: %s" % exc
        return result
    # Потолок времени чтения: «бомба» (zip или PDF, раздувающийся в сотни
    # раз) иначе вешает мост на минуты.
    _LOCAL.budget = _Budget()
    try:
        if kind == "text":
            # ⚠️ Содержимое важнее расширения: картинка, переименованная в
            # `.txt`, должна уйти модели **картинкой**, а не текстом-мусором.
            image_media = image_media_by_content(data[:16])
            magic = _magic_document(data)
            if image_media:
                result["kind"] = "image"
                result["media_type"] = image_media
                if size > MAX_IMAGE_BYTES:
                    result["error"] = ("картинка больше %s — вендор такие "
                                       "не принимает"
                                       % human_size(MAX_IMAGE_BYTES))
                    return result
                result["image"] = base64.b64encode(data).decode("ascii")
                result["warnings"] = ["формат определён по содержимому, "
                                      "а не по расширению"]
                result["ok"] = True
                return result
            if magic:
                text, warnings = _read_document(data, magic)
                warnings.append("формат определён по содержимому, а не по расширению")
            else:
                text, warnings = _read_text(data)
        else:
            text, warnings = _read_document(data, Path(raw_path).suffix.lower())
    except FileError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001 — читаемость не должна ронять чат
        result["error"] = "не разобрался: %s" % exc
        return result
    finally:
        _LOCAL.budget = None
    limit = MAX_DOC_CHARS if kind == "document" else MAX_TEXT_CHARS
    if len(text) > limit:
        text = text[:limit]
        warnings.append("файл обрезан: показаны первые %s символов" % limit)
    result["text"] = text
    result["warnings"] = warnings
    result["ok"] = True
    return result


def _magic_document(data: bytes) -> str:
    """Документ ли это на самом деле (по содержимому) — и какой.

    Возвращает подсказку расширения (`""` — документ не обнаружен).
    """
    if data.startswith(b"PK\x03\x04"):
        return ".zip"
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"{\\rtf"):
        return ".rtf"
    if data.startswith(_OLE_MAGIC):
        streams = _ole_streams(data)
        if "Workbook" in streams or "Book" in streams:
            return ".xls"
        return ".doc"
    return ""


# ──────────────────────────────────────────────────────────────── текст

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "cp1252")


def _read_text(data: bytes) -> tuple[str, list[str]]:
    """Текст как есть + предупреждения (кодировка, обрезка)."""
    warnings: list[str] = []
    # UTF-16 — только по BOM: без BOM это «мусор», а не текст.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace"), warnings
    text = ""
    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = data.decode("utf-8", "replace")
        warnings.append("часть символов заменена: кодировку определить не удалось")
    if not text.strip():
        warnings.append("файл пустой или без текста")
    return text, warnings


# ───────────────────────────────────────────────────────────── документы

def _read_document(data: bytes, suffix: str) -> tuple[str, list[str]]:
    """Текст документа по расширению (с запасным вариантом по содержимому)."""
    warnings: list[str] = []
    if suffix in (".xlsx", ".xlsm", ".xltx"):
        return _read_xlsx(data)
    if suffix in (".docx", ".docm", ".dotx"):
        return _read_docx(data)
    if suffix in (".pptx", ".pptm", ".potx"):
        return _read_pptx(data)
    if suffix in (".odt", ".ods", ".odp", ".odg"):
        return _read_odf(data)
    if suffix == ".pdf":
        return _read_pdf(data)
    if suffix == ".rtf":
        return _read_rtf(data)
    if suffix == ".doc":
        return _read_legacy_doc(data)
    if suffix == ".xls":
        return _read_legacy_xls(data)
    if suffix == ".ppt":
        raise FileError("старый формат PowerPoint (.ppt) не читается — "
                        "сохраните в .pptx")
    if suffix == ".zip":
        return _read_zip_document(data)
    # расширение соврало — пробуем по содержимому
    if data.startswith(b"PK\x03\x04"):
        text, warns = _read_zip_document(data)
        warns.append("формат определён по содержимому, а не по расширению")
        return text, warns
    if data.startswith(b"%PDF"):
        return _read_pdf(data)
    if data.startswith(b"{\\rtf"):
        return _read_rtf(data)
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        streams = _ole_streams(data)
        if "WordDocument" in streams:
            return _read_legacy_doc(data)
        if "Workbook" in streams or "Book" in streams:
            return _read_legacy_xls(data)
        raise FileError("старый составной формат Office не читается — "
                        "сохраните в .docx или .xlsx")
    raise FileError("формат не поддерживается")


def _read_zip_document(data: bytes) -> tuple[str, list[str]]:
    """Документ в zip (книга/документ/презентация/ODF) — по содержимому."""
    for reader in (_read_xlsx, _read_docx, _read_pptx, _read_odf):
        try:
            text, warns = reader(data)
        except Exception:  # noqa: BLE001 — пробуем следующий разборщик
            continue
        if text.strip():
            return text, warns
    raise FileError("в архиве не найдено знакомых документов (книга, "
                    "документ, презентация)")


def _zip_open(data: bytes) -> zipfile.ZipFile:
    """Открыть zip-архив с потолком на распакованный объём.

    ⚠️ «Бомба» — 200 КБ архива, который распаковывается в сотни мегабайт:
    без потолка чтение вешает мост на минуты (живая проба 26.11: PDF-бомба
    читалась дольше 10 минут). Потолок считаем по **объявленным** размерам
    членов архива — распаковывать их до проверки нельзя.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileError("архив не открывается: %s" % exc)
    total = 0
    for info in archive.infolist():
        total += int(info.file_size)
        if total > MAX_UNPACKED_BYTES:
            archive.close()
            raise FileError("в архиве больше %s данных — читать не буду"
                            % human_size(MAX_UNPACKED_BYTES))
    return archive


def _local(tag: str) -> str:
    """Имя элемента без пространства имён: `{ns}row` → `row`."""
    return tag.rsplit("}", 1)[-1]


def _read_xlsx(data: bytes) -> tuple[str, list[str]]:
    """Книга Excel: строки таблицы через разделитель табуляцией."""
    import xml.etree.ElementTree as ET

    with _zip_open(data) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root:
                if _local(item.tag) != "si":
                    continue
                shared.append("".join(node.text or "" for node in item.iter()
                                      if _local(node.tag) == "t"))
        sheets = sorted(name for name in names
                        if name.startswith("xl/worksheets/") and name.endswith(".xml"))
        if not sheets:
            raise FileError("в книге нет листов")
        lines: list[str] = []
        for name in sheets:
            _check_time()
            root = ET.fromstring(archive.read(name))
            lines.append("--- %s ---" % name.rsplit("/", 1)[-1])
            for row in root.iter():
                if _local(row.tag) != "row":
                    continue
                cells: list[str] = []
                for cell in row:
                    if _local(cell.tag) != "c":
                        continue
                    kind = cell.get("t") or ""
                    value = ""
                    if kind == "inlineStr":
                        value = "".join(node.text or "" for node in cell.iter()
                                        if _local(node.tag) == "t")
                    else:
                        holder = next((node for node in cell
                                       if _local(node.tag) == "v"), None)
                        raw = (holder.text or "") if holder is not None else ""
                        if kind == "s":
                            try:
                                value = shared[int(raw)]
                            except (ValueError, IndexError):
                                value = raw
                        else:
                            value = raw
                    cells.append(value)
                if any(cells):
                    lines.append("\t".join(cells))
                if len(lines) >= MAX_ROWS:
                    lines.append("… таблица обрезана на %d строках" % MAX_ROWS)
                    break
            if len(lines) >= MAX_ROWS:
                # Потолок строк — на **всю книгу**, а не на каждый лист: иначе
                # книга из 100 листов по 5000 строк дала бы полмиллиона строк.
                break
    return "\n".join(lines), []


def _read_docx(data: bytes) -> tuple[str, list[str]]:
    """Документ Word: абзацы."""
    import xml.etree.ElementTree as ET

    with _zip_open(data) as archive:
        if "word/document.xml" not in archive.namelist():
            raise FileError("в документе нет текста")
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for node in root.iter():
        if _local(node.tag) != "p":
            continue
        _check_time()
        parts: list[str] = []
        for item in node.iter():
            name = _local(item.tag)
            if name == "t":
                parts.append(item.text or "")
            elif name == "tab":
                parts.append("\t")
            elif name in ("br", "cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs), []


def _read_pptx(data: bytes) -> tuple[str, list[str]]:
    """Презентация: строки слайдов."""
    import xml.etree.ElementTree as ET

    with _zip_open(data) as archive:
        slides = sorted(name for name in archive.namelist()
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
        if not slides:
            raise FileError("в презентации нет слайдов")
        lines: list[str] = []
        for name in slides:
            _check_time()
            root = ET.fromstring(archive.read(name))
            lines.append("--- %s ---" % name.rsplit("/", 1)[-1])
            for node in root.iter():
                if _local(node.tag) != "p":
                    continue
                text = "".join(item.text or "" for item in node.iter()
                               if _local(item.tag) == "t")
                if text.strip():
                    lines.append(text)
    return "\n".join(lines), []


def _read_odf(data: bytes) -> tuple[str, list[str]]:
    """Документ ODF (odt/ods/odp): абзацы."""
    import xml.etree.ElementTree as ET

    with _zip_open(data) as archive:
        if "content.xml" not in archive.namelist():
            raise FileError("в документе нет содержимого")
        root = ET.fromstring(archive.read("content.xml"))
    lines: list[str] = []
    for node in root.iter():
        if _local(node.tag) in ("p", "h"):
            _check_time()
            text = "".join(node.itertext())
            if text.strip():
                lines.append(text)
    return "\n".join(lines), []


_RTF_CONTROL = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?")
_RTF_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")

#: Кодовые страницы RTF → кодировка Python.
_RTF_CODEPAGES = {1250: "cp1250", 1251: "cp1251", 1252: "cp1252",
                  866: "cp866", 65001: "utf-8"}


def _read_rtf(data: bytes) -> tuple[str, list[str]]:
    """RTF: текст без управляющих слов.

    Русские документы часто в cp1251 — кодовая страница берётся из самого
    файла (`\\ansicpg205`), иначе текст «разъезжается».
    """
    probe = data.decode("latin-1", "replace")
    codepage = "cp1252"
    match = re.search(r"\\ansicpg(\d+)", probe)
    if match:
        codepage = _RTF_CODEPAGES.get(int(match.group(1)), "cp1252")
    text = data.decode(codepage, "replace")
    out: list[str] = []
    position = 0
    length = len(text)
    skip = 1                     # сколько знаков пропускать после \\u
    while position < length:
        _check_time()
        char = text[position]
        if char == "\\":
            control = _RTF_CONTROL.match(text, position)
            if control:
                word = control.group(1)
                param = control.group(2)
                position = control.end()
                if word == "u" and param is not None:
                    out.append(chr(int(param) % 65536))
                    # запасной знак после \\uN (обычно «?») — пропускаем, но
                    # только если это не начало следующей команды
                    if position < length and text[position] != "\\":
                        position += 1
                elif word == "uc" and param is not None:
                    skip = max(0, int(param))
                continue
            hexa = _RTF_HEX.match(text, position)
            if hexa:
                out.append(bytes([int(hexa.group(1), 16)]).decode(codepage,
                                                                   "replace"))
                position = hexa.end()
                continue
            if text[position:position + 2] in ("\\\\", "\\{", "\\}"):
                out.append(text[position + 1])
                position += 2
                continue
            position += 1
            continue
        if char in "{}":
            position += 1
            continue
        out.append(char)
        position += 1
    lines = [line.strip() for line in "".join(out).splitlines()]
    return "\n".join(line for line in lines if line), []


# ────────────────────────────────────────────────────────────────── pdf

_PDF_TOKEN = re.compile(
    rb"\((?:\\.|[^\\()])*\)"          # строка в скобках
    rb"|<[0-9A-Fa-f\s]*>"            # строка в шестнадцатеричном виде
    rb"|T\*|Td|TD|TJ|Tj|'|\"|ET|BT"   # операторы текста
)


def _read_pdf(data: bytes) -> tuple[str, list[str]]:
    """PDF: текст из потоков содержимого (сжатых и несжатых)."""
    chunks: list[str] = []
    for raw in _pdf_streams(data):
        text = _pdf_text(raw)
        if text.strip():
            chunks.append(text)
    out = "\n".join(chunk for chunk in chunks if chunk.strip())
    if not out.strip():
        raise FileError("текст из PDF не извлёкся (это может быть скан или "
                        "шрифт с нестандартной кодировкой)")
    return out, []


def _pdf_streams(data: bytes) -> list[bytes]:
    """Потоки содержимого PDF: пробуем распаковать (FlateDecode).

    ⚠️ Потолок на **распакованный** объём: PDF-бомба (200 КБ на диске →
    сотни мегабайт в памяти) без потолка читалась дольше 10 минут и вешала
    мост (живая проба 26.11). Распаковываем по частям и следим за объёмом.
    """
    out: list[bytes] = []
    unpacked = 0
    for match in re.finditer(rb"stream\r?\n", data):
        _check_time()
        start = match.end()
        end = data.find(b"endstream", start)
        if end == -1:
            continue
        blob = data[start:end].rstrip(b"\r\n")
        if not blob:
            continue
        try:
            chunk = _inflate_limited(blob)
        except FileError:
            raise
        except zlib.error:
            chunk = None
        if chunk is None:
            chunk = blob
        unpacked += len(chunk)
        if unpacked > MAX_UNPACKED_BYTES:
            raise FileError("в PDF больше %s данных — читать не буду"
                            % human_size(MAX_UNPACKED_BYTES))
        out.append(chunk)
    return out


def _inflate_limited(blob: bytes) -> bytes:
    """Распаковать поток, но не больше потолка (иначе — честный отказ)."""
    decompressor = zlib.decompressobj()
    out = bytearray()
    step = 256 * 1024
    try:
        pending = blob
        while pending:
            _check_time()
            piece = decompressor.decompress(pending, step)
            out += piece
            if len(out) > MAX_UNPACKED_BYTES:
                raise FileError("поток PDF раздувается больше %s — читать не буду"
                                % human_size(MAX_UNPACKED_BYTES))
            pending = decompressor.unconsumed_tail
            if not pending:
                break
    except zlib.error:
        if out:
            return bytes(out)
        return None
    return bytes(out)


def _pdf_decode_string(raw: bytes) -> str:
    """Строка PDF: раскрыть escapes, распознать UTF-16."""
    body = raw[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index:index + 1]
        if char == b"\\" and index + 1 < len(body):
            nxt = body[index + 1:index + 2]
            mapping = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                       b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
            if nxt in mapping:
                out += mapping[nxt]
                index += 2
                continue
            if nxt.isdigit():
                digits = body[index + 1:index + 4]
                digits = bytes(d for d in digits if 48 <= d <= 55)[:3]
                try:
                    out.append(int(digits, 8) & 0xFF)
                except ValueError:
                    pass
                index += 1 + len(digits)
                continue
            if nxt in (b"\n", b"\r"):
                index += 2
                continue
            out += nxt
            index += 2
            continue
        out += char
        index += 1
    if out[:2] == b"\xfe\xff":
        return bytes(out[2:]).decode("utf-16-be", "replace")
    return bytes(out).decode("cp1252", "replace")


def _pdf_text(stream: bytes) -> str:
    """Текст одного потока содержимого."""
    pieces: list[str] = []
    for match in _PDF_TOKEN.finditer(stream):
        token = match.group(0)
        if token.startswith(b"("):
            pieces.append(_pdf_decode_string(token))
        elif token.startswith(b"<"):
            digits = re.sub(rb"[^0-9A-Fa-f]", b"", token[1:-1])
            if len(digits) % 2:
                digits += b"0"
            try:
                pieces.append(bytes.fromhex(digits.decode("ascii")).decode(
                    "cp1252", "replace"))
            except ValueError:
                continue
        elif token in (b"T*", b"Td", b"TD", b"ET"):
            pieces.append("\n")
    return "".join(pieces)


# ───────────────────────────────────────────── старые форматы Office (OLE)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _ole_streams(data: bytes) -> dict[str, bytes]:
    """Потоки составного файла (OLE2): имя → содержимое."""
    if not data.startswith(_OLE_MAGIC) or len(data) < 512:
        return {}
    _check_time()
    try:
        sector_shift = int.from_bytes(data[0x1E:0x20], "little")
        mini_shift = int.from_bytes(data[0x20:0x22], "little")
        sector_size = 1 << sector_shift
        mini_size = 1 << mini_shift
    except (IndexError, ValueError):
        return {}
    if sector_size not in (512, 4096) or mini_size != 64:
        return {}

    def sector_bytes(index: int) -> bytes:
        start = (index + 1) * sector_size
        return data[start:start + sector_size]

    fat_sectors: list[int] = []
    for i in range(109):
        value = int.from_bytes(data[0x4C + i * 4:0x50 + i * 4], "little")
        if value in (0xFFFFFFFF, 0xFFFFFFFE):
            break
        fat_sectors.append(value)
    difat_start = int.from_bytes(data[0x44:0x48], "little")
    difat_count = int.from_bytes(data[0x48:0x4C], "little")
    per_sector = sector_size // 4 - 1
    seen: set[int] = set()
    while (difat_start not in (0xFFFFFFFF, 0xFFFFFFFE)
           and difat_start not in seen and len(seen) <= difat_count + 4):
        seen.add(difat_start)
        block = sector_bytes(difat_start)
        for i in range(per_sector):
            value = int.from_bytes(block[i * 4:i * 4 + 4], "little")
            if value in (0xFFFFFFFF, 0xFFFFFFFE):
                break
            fat_sectors.append(value)
        difat_start = int.from_bytes(block[per_sector * 4:per_sector * 4 + 4],
                                     "little")

    fat: list[int] = []
    for number in fat_sectors:
        block = sector_bytes(number)
        for i in range(sector_size // 4):
            fat.append(int.from_bytes(block[i * 4:i * 4 + 4], "little"))

    def chain(start: int) -> list[int]:
        out: list[int] = []
        current = start
        walked: set[int] = set()
        while current not in (0xFFFFFFFE, 0xFFFFFFFF) and current not in walked:
            walked.add(current)
            out.append(current)
            if current >= len(fat):
                break
            current = fat[current]
        return out

    def read_chain(start: int, size: int = -1) -> bytes:
        blob = b"".join(sector_bytes(number) for number in chain(start))
        return blob[:size] if size >= 0 else blob

    dir_blob = read_chain(int.from_bytes(data[0x30:0x34], "little"))
    entries: list[dict[str, Any]] = []
    for offset in range(0, len(dir_blob) - 127, 128):
        chunk = dir_blob[offset:offset + 128]
        obj_type = chunk[0x42]
        if obj_type not in (1, 2, 5):
            continue
        name_len = int.from_bytes(chunk[0x40:0x42], "little")
        name = ""
        if name_len >= 2:
            name = chunk[:name_len - 2].decode("utf-16-le", "replace")
        entries.append({
            "name": name,
            "type": obj_type,
            "start": int.from_bytes(chunk[0x74:0x78], "little"),
            "size": int.from_bytes(chunk[0x78:0x80], "little"),
        })

    root = next((item for item in entries if item["type"] == 5), None)
    mini_blob = read_chain(root["start"], root["size"]) if root else b""
    mini_blob = mini_blob if isinstance(mini_blob, bytes) else b""
    mini_cutoff = int.from_bytes(data[0x38:0x3C], "little")
    mini_fat_blob = read_chain(int.from_bytes(data[0x3C:0x40], "little"))
    mini_fat = [int.from_bytes(mini_fat_blob[i * 4:i * 4 + 4], "little")
                for i in range(len(mini_fat_blob) // 4)]

    def read_mini(start: int, size: int) -> bytes:
        out = b""
        current = start
        walked: set[int] = set()
        while current not in (0xFFFFFFFE, 0xFFFFFFFF) and current not in walked:
            walked.add(current)
            out += mini_blob[current * mini_size:(current + 1) * mini_size]
            if current >= len(mini_fat):
                break
            current = mini_fat[current]
        return out[:size]

    streams: dict[str, bytes] = {}
    for item in entries:
        if item["type"] != 2:
            continue
        if item["size"] < mini_cutoff:
            streams[item["name"]] = read_mini(item["start"], item["size"])
        else:
            streams[item["name"]] = read_chain(item["start"], item["size"])
    return streams


def _read_legacy_doc(data: bytes) -> tuple[str, list[str]]:
    """Старый Word (97-2003): текст по таблице кусков."""
    streams = _ole_streams(data)
    doc = streams.get("WordDocument")
    if not doc or len(doc) < 0x200:
        raise FileError("старый формат Word (.doc) не читается — "
                        "сохраните в .docx")
    if int.from_bytes(doc[0:2], "little") != 0xA5EC:
        raise FileError("это не документ Word — сохраните в .docx")
    flags = int.from_bytes(doc[0x0A:0x0C], "little")
    if flags & 0x0100:
        raise FileError("документ зашифрован — снимите пароль и сохраните в .docx")
    which = 1 if flags & 0x0200 else 0
    table = streams.get("%dTable" % which) or streams.get("1Table") \
        or streams.get("0Table")
    if not table:
        raise FileError("старый формат Word (.doc) не читается — "
                        "сохраните в .docx")
    ccp_text = int.from_bytes(doc[0x4C:0x50], "little")
    fc_clx = int.from_bytes(doc[0x1A2:0x1A6], "little")
    lcb_clx = int.from_bytes(doc[0x1A6:0x1AA], "little")
    if lcb_clx <= 0 or fc_clx + lcb_clx > len(table):
        raise FileError("старый формат Word (.doc) не читается — "
                        "сохраните в .docx")
    clx = table[fc_clx:fc_clx + lcb_clx]
    plc = b""
    position = 0
    while position < len(clx):
        marker = clx[position]
        if marker == 0x01:
            size = int.from_bytes(clx[position + 1:position + 3], "little")
            position += 3 + size
            continue
        if marker == 0x02:
            size = int.from_bytes(clx[position + 1:position + 5], "little")
            plc = clx[position + 5:position + 5 + size]
            break
        break
    if not plc or len(plc) < 16:
        raise FileError("старый формат Word (.doc) не читается — "
                        "сохраните в .docx")
    count = (len(plc) - 4) // 12
    if count <= 0:
        raise FileError("старый формат Word (.doc) не читается — "
                        "сохраните в .docx")
    boundaries = [int.from_bytes(plc[i * 4:i * 4 + 4], "little")
                  for i in range(count + 1)]
    base = (count + 1) * 4
    lid = int.from_bytes(doc[0x06:0x08], "little")
    encoding = "cp1251" if lid == 0x0419 else "cp1252"
    pieces: list[str] = []
    for index in range(count):
        pcd = plc[base + index * 8:base + index * 8 + 8]
        if len(pcd) < 8:
            break
        fc = int.from_bytes(pcd[2:6], "little")
        compressed = bool(fc & 0x40000000)
        offset = (fc & 0x3FFFFFFF) // 2 if compressed else (fc & 0x3FFFFFFF)
        length = boundaries[index + 1] - boundaries[index]
        if length <= 0:
            continue
        if compressed:
            pieces.append(doc[offset:offset + length].decode(encoding, "replace"))
        else:
            pieces.append(doc[offset:offset + length * 2].decode("utf-16-le",
                                                                 "replace"))
    text = "".join(pieces)
    if ccp_text > 0:
        text = text[:ccp_text]
    if not _printable_ratio_ok(text):
        raise FileError("старый формат Word (.doc) не разобрался — "
                        "сохраните в .docx")
    return text, ["старый формат Word (.doc): текст извлечён best effort"]


def _read_legacy_xls(data: bytes) -> tuple[str, list[str]]:
    """Старая книга Excel (97-2003): строки листов."""
    streams = _ole_streams(data)
    book = streams.get("Workbook") or streams.get("Book")
    if not book:
        raise FileError("старый формат Excel (.xls) не читается — "
                        "сохраните в .xlsx")
    sheets = _xls_sheets(book)
    lines: list[str] = []
    for title, body in sheets:
        lines.append("--- %s ---" % title)
        lines.extend(_xls_rows(body))
    if not any(line for line in lines if not line.startswith("--- ")):
        raise FileError("старый формат Excel (.xls) не разобрался — "
                        "сохраните в .xlsx")
    return "\n".join(lines), ["старый формат Excel (.xls): текст извлечён best effort"]


def _xls_records(book: bytes) -> list[tuple[int, bytes]]:
    records: list[tuple[int, bytes]] = []
    position = 0
    while position + 4 <= len(book):
        rid = int.from_bytes(book[position:position + 2], "little")
        size = int.from_bytes(book[position + 2:position + 4], "little")
        if size < 0 or position + 4 + size > len(book):
            break
        records.append((rid, book[position + 4:position + 4 + size]))
        position += 4 + size
    return records


def _xls_sheets(book: bytes) -> list[tuple[str, bytes]]:
    """Листы книги: имя и содержимое её потока."""
    records = _xls_records(book)
    bounds: list[tuple[int, str]] = []
    for rid, body in records:
        if rid != 0x0085 or len(body) < 6:
            continue
        position = int.from_bytes(body[0:4], "little")
        length = body[6] if len(body) > 6 else 0
        name = body[8:8 + max(0, length)].decode("utf-16-le", "replace") \
            if length else "лист"
        bounds.append((position, name))
    if not bounds:
        return [("книга", book)]
    out: list[tuple[str, bytes]] = []
    for index, (position, name) in enumerate(bounds):
        end = bounds[index + 1][0] if index + 1 < len(bounds) else len(book)
        out.append((name, book[position:end]))
    return out


def _xls_rows(sheet: bytes) -> list[str]:
    """Строки листа: ячейки по строкам через табуляцию."""
    records = _xls_records(sheet)
    shared: list[str] = []
    for rid, body in records:
        if rid == 0x00FC:            # SST
            shared = _xls_sst(body)
            break
    cells: dict[int, dict[int, str]] = {}
    for rid, body in records:
        if rid == 0x00FD and len(body) >= 10:      # LABELSST
            row = int.from_bytes(body[0:2], "little")
            col = int.from_bytes(body[2:4], "little")
            index = int.from_bytes(body[6:10], "little")
            value = shared[index] if 0 <= index < len(shared) else ""
            cells.setdefault(row, {})[col] = value
        elif rid == 0x0203 and len(body) >= 14:    # NUMBER
            row = int.from_bytes(body[0:2], "little")
            col = int.from_bytes(body[2:4], "little")
            import struct
            value = struct.unpack("<d", body[6:14])[0]
            cells.setdefault(row, {})[col] = ("%g" % value)
        elif rid == 0x027E and len(body) >= 10:    # RK
            row = int.from_bytes(body[0:2], "little")
            col = int.from_bytes(body[2:4], "little")
            cells.setdefault(row, {})[col] = _xls_rk(body[6:10])
    lines: list[str] = []
    for row in sorted(cells)[:MAX_ROWS]:
        values = cells[row]
        lines.append("\t".join(values.get(col, "") for col in sorted(values)))
    return lines


def _xls_rk(raw: bytes) -> str:
    """Число RK: 4 байта (знак и дробная часть в двух младших битах)."""
    import struct
    value = int.from_bytes(raw, "little")
    divided = bool(value & 0x01)
    is_text = bool(value & 0x02)
    if is_text:
        return ""
    number = value >> 2
    if number & 0x20000000:
        number -= 0x40000000
    if divided:
        return "%g" % (number / 100.0)
    return "%g" % float(number)


def _xls_sst(body: bytes) -> list[str]:
    """Общие строки книги (SST): только то, что помещается в запись."""
    if len(body) < 8:
        return []
    unique = int.from_bytes(body[4:8], "little")
    strings: list[str] = []
    position = 8
    for _ in range(unique):
        if position + 3 > len(body):
            break
        length = int.from_bytes(body[position:position + 2], "little")
        flags = body[position + 2]
        position += 3
        run_count = 0
        ext_size = 0
        if flags & 0x04:
            run_count = int.from_bytes(body[position:position + 2], "little")
            position += 2
        if flags & 0x08:
            ext_size = int.from_bytes(body[position:position + 4], "little")
            position += 4
        if flags & 0x01:
            raw = body[position:position + length * 2]
            position += length * 2
            strings.append(raw.decode("utf-16-le", "replace"))
        else:
            raw = body[position:position + length]
            position += length
            strings.append(raw.decode("latin-1", "replace"))
        position += run_count * 4 + ext_size
    return strings


def _printable_ratio_ok(text: str) -> bool:
    """Текст похож на настоящий (а не на мусор из разбора)."""
    sample = text[:4000]
    if not sample:
        return False
    printable = sum(1 for char in sample if char.isprintable() or char in "\r\n\t")
    return printable / len(sample) > 0.9
