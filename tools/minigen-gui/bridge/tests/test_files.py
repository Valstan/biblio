"""Чтение файлов оператора для контекста модели (25.11, промт #90).

Заказ владельца: «в чат встроенный в МиниГене добавить инструмент добавления
файлов: картинки, документы различных форматов — текстовые, XML, XLS, DOC… —
чтобы отправлять их нейросети на съедение в контекст».

Проверяем:

- **текст читается как есть** (в том числе в cp1251 — русские файлы);
- **картинка идёт картинкой**: base64 + тип содержимого, а не текст;
- **документы извлекают текст**: xlsx, docx, pptx, odt, pdf, rtf;
- **потолки**: файл выше потолка ⇒ честное сообщение, а не зависание;
- **битое/непонятное** ⇒ честное сообщение с путём, а не пустота;
- **ничего не пишется**: каталог файла после чтения не меняется;
- **формат по содержимому**: файл без расширения (или с врущим) читается по
  содержимому.

⚠️ Тесты работают на временных файлах: живая машина и её каталоги не
затрагиваются.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_files as files  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


def _zip_bytes(entries: dict[str, str]) -> bytes:
    """Собрать zip-архив из строк (для книг и документов)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


XLSX_BYTES = _zip_bytes({
    "xl/workbook.xml": ("<?xml version='1.0'?><workbook><sheets>"
                         "<sheet name='Лист1' sheetId='1' r:id='rId1'/></sheets>"
                         "</workbook>"),
    "xl/sharedStrings.xml": ("<?xml version='1.0'?><sst count='2' uniqueCount='2'>"
                              "<si><t>Товар</t></si>"
                              "<si><t>Цена</t></si></sst>"),
    "xl/worksheets/sheet1.xml": (
        "<?xml version='1.0'?><worksheet><sheetData>"
        "<row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1' t='s'><v>1</v></c></row>"
        "<row r='2'><c r='A2' t='inlineStr'><is><t>Гвоздь</t></is></c>"
        "<c r='B2'><v>12.5</v></c></row>"
        "</sheetData></worksheet>"),
})

DOCX_BYTES = _zip_bytes({
    "word/document.xml": (
        "<?xml version='1.0'?><w:document xmlns:w='urn:w'><w:body>"
        "<w:p><w:r><w:t>Первая строка</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Вторая строка</w:t></w:r>"
        "<w:r><w:t xml:space='preserve'> и хвост</w:t></w:r></w:p>"
        "</w:body></w:document>"),
})

PPTX_BYTES = _zip_bytes({
    "ppt/slides/slide1.xml": (
        "<?xml version='1.0'?><p:sld xmlns:p='urn:p' xmlns:a='urn:a'><p:cSld>"
        "<p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Слайд один</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"),
})

ODT_BYTES = _zip_bytes({
    "content.xml": (
        "<?xml version='1.0'?><office:document-content "
        "xmlns:office='urn:o' xmlns:text='urn:text'><office:body><text:p>"
        "Абзац один</text:p><text:p>Абзац два</text:p></office:body>"
        "</office:document-content>"),
})

PDF_TEXT = "Hello from PDF"


def _pdf_bytes(compress: bool = False) -> bytes:
    """Настоящий минимальный PDF с текстом (сжатый и несжатый)."""
    content = ("BT /F1 12 Tf 72 720 Td (%s) Tj ET" % PDF_TEXT).encode("utf-8")
    if compress:
        body = zlib.compress(content)
        extra = " /Filter /FlateDecode"
    else:
        body = content
        extra = ""
    return ("%%PDF-1.4\n1 0 obj\n<< /Length %d%s >>\nstream\n" % (len(body), extra)
            ).encode("ascii") + body + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


#: RTF: \\uN — код символа **десятичный** (1055 = «П»).
RTF_BYTES = (b"{\\rtf1\\ansi\\ansicpg1251 {\\fonttbl{\\f0 Arial;}}"
             b"\\f0\\fs24 \\u1055\\u1088\\u1080\\u1074\\u1077\\u1090? RTF\\par}")


class FilesTest(unittest.TestCase):
    """Чтение файлов каждого семейства форматов."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    # ── текст ────────────────────────────────────────────────────────────
    def test_text_file_is_read_as_is(self) -> None:
        path = self._write("заметка.txt", "Привет, МиниГен!\nВторая строка.\n".encode("utf-8"))
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["kind"], "text")
        self.assertIn("Привет, МиниГен!", out["text"])
        self.assertIn("Вторая строка.", out["text"])

    def test_text_in_cp1251_is_read(self) -> None:
        """Русские файлы часто в cp1251 — их тоже надо читать."""
        path = self._write("старое.txt", "Привет из cp1251".encode("cp1251"))
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("Привет из cp1251", out["text"])

    def test_xml_and_csv_are_text(self) -> None:
        path = self._write("data.csv", "a,b\n1,2\n".encode("utf-8"))
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"])
        self.assertEqual(out["kind"], "text")
        self.assertIn("a,b", out["text"])

    # ── картинки ─────────────────────────────────────────────────────────
    def test_image_is_read_as_image(self) -> None:
        path = self._write("схема.png", PNG)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["kind"], "image")
        self.assertEqual(out["media_type"], "image/png")
        self.assertEqual(base64.b64decode(out["image"]), PNG)
        self.assertEqual(out["text"], "", "картинка — не текст")

    def test_image_without_extension_is_detected_by_content(self) -> None:
        path = self._write("без_имени", PNG)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["kind"], "image")
        self.assertEqual(out["media_type"], "image/png")

    def test_too_big_image_is_refused_honestly(self) -> None:
        path = self._write("огромная.png", b"\x89PNG\r\n\x1a\n" + b"0" * (files.MAX_IMAGE_BYTES + 1))
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertIn("вендор", out["error"])

    # ── документы ───────────────────────────────────────────────────────
    def test_xlsx_is_read(self) -> None:
        path = self._write("книга.xlsx", XLSX_BYTES)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["kind"], "document")
        self.assertIn("Товар", out["text"])
        self.assertIn("Цена", out["text"])
        self.assertIn("Гвоздь", out["text"])
        self.assertIn("12.5", out["text"])

    def test_docx_is_read(self) -> None:
        path = self._write("документ.docx", DOCX_BYTES)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("Первая строка", out["text"])
        self.assertIn("Вторая строка и хвост", out["text"])

    def test_pptx_is_read(self) -> None:
        path = self._write("презентация.pptx", PPTX_BYTES)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("Слайд один", out["text"])

    def test_odt_is_read(self) -> None:
        path = self._write("текст.odt", ODT_BYTES)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("Абзац один", out["text"])
        self.assertIn("Абзац два", out["text"])

    def test_pdf_is_read(self) -> None:
        path = self._write("договор.pdf", _pdf_bytes())
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn(PDF_TEXT, out["text"])

    def test_compressed_pdf_is_read(self) -> None:
        """Сжатый поток содержимого (FlateDecode) — тоже."""
        path = self._write("сжатый.pdf", _pdf_bytes(compress=True))
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn(PDF_TEXT, out["text"])

    def test_rtf_is_read(self) -> None:
        path = self._write("письмо.rtf", RTF_BYTES)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("Привет RTF", out["text"])

    def test_zip_with_wrong_extension_is_read_by_content(self) -> None:
        """Файл «.txt», который на самом деле книга Excel."""
        path = self._write("переименованный.txt", XLSX_BYTES)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("Товар", out["text"])
        self.assertTrue(any("содержимому" in w for w in out["warnings"]))

    # ── формат по содержимому ──────────────────────────────────────────
    def test_text_without_extension_is_read(self) -> None:
        """Файл без расширения (`README`, `Makefile`) — обычное дело."""
        path = self._write("README", "Текст без расширения".encode("utf-8"))
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["kind"], "text")
        self.assertIn("Текст без расширения", out["text"])

    def test_binary_without_extension_is_refused(self) -> None:
        """Мусор без расширения — честный отказ, а не текст из мусора."""
        path = self._write("blob", b"\x00\x01\x02\x03\xff\xfe")
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertIn("не поддерживается", out["error"])

    def test_image_with_a_wrong_extension_stays_an_image(self) -> None:
        """Картинка, переименованная в `.txt`, должна уйти картинкой."""
        path = self._write("переименованная.txt", PNG)
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["kind"], "image")
        self.assertEqual(out["media_type"], "image/png")
        self.assertEqual(base64.b64decode(out["image"]), PNG)
        self.assertEqual(out["text"], "", "картинка — не текст")

    # ── честные ошибки ──────────────────────────────────────────────────
    def test_missing_file_is_an_honest_error(self) -> None:
        out = files.read_for_context(str(self.root / "нет.txt"))
        self.assertFalse(out["ok"])
        self.assertIn("нет.txt", out["error"])

    def test_broken_archive_is_an_honest_error(self) -> None:
        path = self._write("битый.xlsx", b"PK\x03\x04" + "это не архив".encode("utf-8"))
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertTrue(out["error"])

    def test_unsupported_format_is_an_honest_error(self) -> None:
        path = self._write("фильм.mkv", b"\x00\x01\x02\x03")
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertIn("не поддерживается", out["error"])

    def test_legacy_doc_without_ole_is_an_honest_error(self) -> None:
        """Старый .doc, который не составной файл, — честно и без мусора."""
        path = self._write("старый.doc", "просто текст, а не Word".encode("utf-8"))
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertIn(".docx", out["error"])

    def test_empty_path_is_an_honest_error(self) -> None:
        out = files.read_for_context("")
        self.assertFalse(out["ok"])
        self.assertIn("путь", out["error"])

    # ── потолки ─────────────────────────────────────────────────────────
    def test_too_big_file_is_refused_honestly(self) -> None:
        path = self._write("огромный.txt", b"a" * (files.MAX_FILE_BYTES + 1))
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertIn("больше", out["error"])

    def test_long_text_is_cut_with_a_warning(self) -> None:
        path = self._write("длинный.txt", ("я" * (files.MAX_TEXT_CHARS + 5000)).encode("utf-8"))
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(len(out["text"]), files.MAX_TEXT_CHARS)
        self.assertTrue(any("обрезан" in w for w in out["warnings"]))

    def test_zip_bomb_is_refused_instead_of_hanging(self) -> None:
        """200 КБ архива, раздувающегося в сотни мегабайт, — честный отказ.

        ⚠️ Живой случай (26.11): без потолка на распакованный объём чтение
        вешало мост на минуты.
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml",
                             "x" * (files.MAX_UNPACKED_BYTES + 1024))
            archive.writestr("xl/sharedStrings.xml", "<sst/>")
        path = self._write("бомба.xlsx", buffer.getvalue())
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertIn("больше", out["error"])
        self.assertEqual(out["text"], "")

    def test_pdf_bomb_is_refused_instead_of_hanging(self) -> None:
        """PDF-поток, раздувающийся в сотни мегабайт, — честный отказ."""
        body = zlib.compress(b"(" + b"A" * (files.MAX_UNPACKED_BYTES + 1024) + b")")
        pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(body)).encode("ascii")
               + b" /Filter /FlateDecode >>\nstream\n" + body
               + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
        path = self._write("бомба.pdf", pdf)
        out = files.read_for_context(str(path))
        self.assertFalse(out["ok"])
        self.assertEqual(out["text"], "")

    def test_reading_time_is_capped(self) -> None:
        """Потолок времени: файл сложнее — честный отказ, а не зависание."""
        path = self._write("сложный.rtf", RTF_BYTES)
        saved = getattr(files._LOCAL, "budget", None)
        # Лимит отрицательный — истёк всегда: так проверка не зависит от
        # разрешения часов (`monotonic` на Windows грубый).
        files._LOCAL.budget = files._Budget(-1.0)
        try:
            with self.assertRaises(files.FileError) as caught:
                files._read_rtf(path.read_bytes())
            self.assertIn("секунд", str(caught.exception))
        finally:
            files._LOCAL.budget = saved

    def test_rows_limit_covers_the_whole_book(self) -> None:
        """Потолок строк — на всю книгу, а не на каждый лист."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for index in range(1, 6):
                rows = "".join(
                    '<row r="%d"><c r="A%d" t="inlineStr"><is><t>v</t></is></c></row>'
                    % (number, number) for number in range(1, 1001)
                )
                archive.writestr(
                    "xl/worksheets/sheet%d.xml" % index,
                    "<worksheet><sheetData>%s</sheetData></worksheet>" % rows)
        path = self._write("много.xlsx", buffer.getvalue())
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertLessEqual(out["text"].count("\n"), files.MAX_ROWS + 10)

    # ── ничего не пишется ───────────────────────────────────────────────
    def test_nothing_is_written_to_the_folder(self) -> None:
        """Каталог файла после чтения не меняется (приёмка №8 промта)."""
        folder = self.root / "папка"
        folder.mkdir()
        path = folder / "заметка.txt"
        path.write_bytes("Привет".encode("utf-8"))
        before = sorted(os.listdir(folder))
        stat_before = path.stat().st_mtime_ns
        out = files.read_for_context(str(path))
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(sorted(os.listdir(folder)), before)
        self.assertEqual(path.stat().st_mtime_ns, stat_before)

    def test_size_is_reported(self) -> None:
        path = self._write("размер.txt", b"x" * 1234)
        out = files.read_for_context(str(path))
        self.assertEqual(out["size"], 1234)
        self.assertEqual(out["name"], "размер.txt")


if __name__ == "__main__":
    unittest.main()
