"""Файлы оператора в чате «Управляющего» (25.11, промт #90).

Заказ владельца: «в чат встроенный в МиниГене добавить инструмент добавления
файлов: картинки, документы различных форматов… чтобы отправлять их нейросети
на съедение в контекст. Нужна и кнопка добавления файлов, и зона для
перетаскивания файлов прямо в чат».

Проверяем:

- **текст и документ уходят в контекст блоком** (вендор принимает блоки);
- **картинка уходит картинкой** (блок image с base64), а не текстом;
- **в переписке видно, что приложено** (имя, вид, размер) — и после
  перезапуска окна тоже (вложения лежат в файле истории);
- **нейросеть читает только то, что приложил оператор**: чужой файл с диска
  прочитать нельзя (иначе она утащила бы в переписку ключи — D-038);
- **битый файл не роняет чат**: в контекст уходит честное сообщение;
- **файл без текста** (оператор приложил и ничего не написал) — задача всё
  равно формулируется.

⚠️ Тесты работают на временных файлах: живая машина не затрагивается.
"""

from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_bridge as bridge_mod   # noqa: E402
import minigen_chat_store as store    # noqa: E402
import minigen_files as files         # noqa: E402
import minigen_state as state          # noqa: E402
import minigen_tools as tools_mod      # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml",
                         "<?xml version='1.0'?><w:document xmlns:w='urn:w'><w:body>"
                         "<w:p><w:r><w:t>Строка документа</w:t></w:r></w:p>"
                         "</w:body></w:document>")
    return buffer.getvalue()


class _FakeAgent:
    """Агент-заглушка: запоминает блоки контекста."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.task = ""
        self.council_note = ""
        self.pending = None

    def ask(self, history, user_text, area_slug="minigen", model="", note="",
            blocks=None, council_note=""):
        self.task = user_text
        self.blocks = list(blocks or [])
        self.council_note = council_note
        return {"status": "done", "text": "файл посмотрен", "calls": [],
                "history": list(history)}

    def confirm(self, approved, history, area_slug="minigen", model=""):
        return {"status": "done", "text": "готово", "calls": [],
                "history": list(history)}


class _FakeCore:
    def refresh_env(self) -> None:
        pass


class ChatFilesTest(unittest.TestCase):
    """Мост: вложения оператора в чате."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved_file = store.HISTORY_FILE
        store.HISTORY_FILE = self.root / "chat-history.json"
        self._saved_settings = state.read_settings
        state.read_settings = lambda: {}
        self.bridge = bridge_mod.Bridge.__new__(bridge_mod.Bridge)
        self.bridge.chat_history = []
        self.bridge.agent = _FakeAgent()
        self.bridge.core = _FakeCore()
        self.bridge._report_call = lambda cmd, result: None
        self.bridge._unread_reports = lambda: ""
        files._ATTACHED.clear()

    def tearDown(self) -> None:
        store.HISTORY_FILE = self._saved_file
        state.read_settings = self._saved_settings
        files._ATTACHED.clear()
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    # ── текст и документ ───────────────────────────────────────────────
    def test_text_file_goes_to_the_context(self) -> None:
        path = self._write("заметка.txt", "Текст для модели".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "прочитай",
                                   "attachments": [{"path": str(path)}]})
        blocks = self.bridge.agent.blocks
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "text")
        self.assertIn("Текст для модели", blocks[0]["text"])
        self.assertIn("заметка.txt", blocks[0]["text"])

    def test_document_goes_to_the_context_as_text(self) -> None:
        path = self._write("док.docx", _docx_bytes())
        self.bridge.cmd_chat_send({"text": "посмотри",
                                   "attachments": [{"path": str(path)}]})
        block = self.bridge.agent.blocks[0]
        self.assertEqual(block["type"], "text")
        self.assertIn("Строка документа", block["text"])

    # ── картинка ────────────────────────────────────────────────────────
    def test_image_goes_to_the_context_as_an_image(self) -> None:
        path = self._write("схема.png", PNG)
        self.bridge.cmd_chat_send({"text": "что на картинке?",
                                   "attachments": [{"path": str(path)}]})
        block = self.bridge.agent.blocks[0]
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["type"], "base64")
        self.assertEqual(block["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(block["source"]["data"]), PNG)

    # ── видимая переписка ───────────────────────────────────────────────
    def test_the_reply_shows_the_attachment(self) -> None:
        path = self._write("схема.png", PNG)
        self.bridge.cmd_chat_send({"text": "посмотри",
                                   "attachments": [{"path": str(path)}]})
        items = store.load()
        self.assertEqual(items[0]["role"], "user")
        self.assertEqual(items[0]["attachments"][0]["name"], "схема.png")
        self.assertEqual(items[0]["attachments"][0]["kind"], "image")
        self.assertTrue(items[0]["attachments"][0]["size"] > 0)

    def test_attachments_survive_the_restart(self) -> None:
        """Вложения лежат в файле истории — окно увидит их после перезапуска."""
        path = self._write("заметка.txt", "текст".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "прочитай",
                                   "attachments": [{"path": str(path)}]})
        restored = store.load()
        self.assertEqual(restored[0]["attachments"][0]["name"], "заметка.txt")
        memory = store.agent_history(restored)
        self.assertIn("заметка.txt", memory[0]["content"])

    # ── границы дозволенного ───────────────────────────────────────────
    def test_the_model_cannot_read_a_file_it_was_not_given(self) -> None:
        """Нейросеть читает только то, что приложил оператор (D-038)."""
        secret = self._write("ключи.txt", "секрет".encode("utf-8"))
        out = self.bridge.cmd_files_read({"path": str(secret)})
        self.assertFalse(out["ok"])
        self.assertIn("не прикладывал", out["error"])

    def test_the_model_can_read_an_attached_file(self) -> None:
        path = self._write("заметка.txt", "текст".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "прочитай",
                                   "attachments": [{"path": str(path)}]})
        out = self.bridge.cmd_files_read({"path": str(path)})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("текст", out["text"])

    def test_read_without_a_path_is_an_honest_error(self) -> None:
        out = self.bridge.cmd_files_read({})
        self.assertFalse(out["ok"])
        self.assertIn("путь", out["error"])

    # ── битое и пустое ──────────────────────────────────────────────────
    def test_a_broken_file_does_not_break_the_chat(self) -> None:
        path = self._write("битый.xlsx", b"PK\x03\x04" + "не архив".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "посмотри",
                                   "attachments": [{"path": str(path)}]})
        block = self.bridge.agent.blocks[0]
        self.assertIn("не прочитан", block["text"])
        # реплика владельца всё равно в переписке
        self.assertEqual(store.load()[0]["role"], "user")

    def test_the_error_message_names_the_full_path(self) -> None:
        """Приёмка #6: сообщение о поломке — с полным путём файла."""
        folder = self.root / "папка"
        folder.mkdir()
        path = folder / "битый.xlsx"
        path.write_bytes(b"PK\x03\x04" + "не архив".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "посмотри",
                                   "attachments": [{"path": str(path)}]})
        block = self.bridge.agent.blocks[0]
        self.assertIn(str(path), block["text"])

    def test_a_file_without_a_word_still_makes_a_task(self) -> None:
        path = self._write("заметка.txt", "текст".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "",
                                   "attachments": [{"path": str(path)}]})
        self.assertIn("заметка.txt", self.bridge.agent.task)
        self.assertEqual(len(self.bridge.agent.blocks), 1)

    def test_the_council_receives_the_content_of_the_file(self) -> None:
        """Совет должен видеть файл: иначе роли ищут его по диску (живой случай)."""
        path = self._write("щиток.txt", "На щитке 17 выключателей".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "сколько выключателей?",
                                   "attachments": [{"path": str(path)}]})
        note = self.bridge.agent.council_note
        self.assertIn("17 выключателей", note)
        self.assertIn("щиток.txt", note)

    def test_the_council_is_told_about_a_picture(self) -> None:
        """Картинку совет не видит — но знает, что она приложена."""
        path = self._write("схема.png", PNG)
        self.bridge.cmd_chat_send({"text": "что на картинке?",
                                   "attachments": [{"path": str(path)}]})
        note = self.bridge.agent.council_note
        self.assertIn("схема.png", note)
        self.assertIn("изображение", note)

    def test_a_request_without_text_and_files_is_refused(self) -> None:
        out = self.bridge.cmd_chat_send({"text": "   "})
        self.assertEqual(out["status"], "error")

    def test_nothing_is_written_next_to_the_file(self) -> None:
        """Каталог файла после чата не меняется (приёмка №8 промта)."""
        folder = self.root / "папка"
        folder.mkdir()
        path = folder / "заметка.txt"
        path.write_bytes("текст".encode("utf-8"))
        before = sorted(item.name for item in folder.iterdir())
        self.bridge.cmd_chat_send({"text": "прочитай",
                                   "attachments": [{"path": str(path)}]})
        self.assertEqual(sorted(item.name for item in folder.iterdir()), before)

    def test_the_tool_does_not_repeat_the_picture(self) -> None:
        """Картинка уже в контексте — в ответе инструмента её не повторяем."""
        path = self._write("схема.png", PNG)
        self.bridge.cmd_chat_send({"text": "посмотри",
                                   "attachments": [{"path": str(path)}]})
        out = self.bridge.cmd_files_read({"path": str(path)})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["image"], "")
        self.assertTrue(any("контекст" in w for w in out["warnings"]))

    def test_the_file_stays_readable_after_the_restart(self) -> None:
        """Реестр вложений живёт в памяти, но в переписке вложение осталось."""
        path = self._write("заметка.txt", "текст".encode("utf-8"))
        self.bridge.cmd_chat_send({"text": "прочитай",
                                   "attachments": [{"path": str(path)}]})
        files._ATTACHED.clear()            # как после перезапуска программы
        out = self.bridge.cmd_files_read({"path": str(path)})
        self.assertTrue(out["ok"], out.get("error"))
        self.assertIn("текст", out["text"])


class AttachToolTest(unittest.TestCase):
    """Инструмент `files.attach` в каталоге инструментов нейросети."""

    def test_the_tool_exists_and_reads_only(self) -> None:
        tool = next((item for item in tools_mod.TOOLS
                     if item["name"] == "files.attach"), None)
        self.assertIsNotNone(tool, "инструмента files.attach нет в каталоге")
        self.assertFalse(tool["dangerous"],
                         "чтение файла не должно требовать подтверждения")
        self.assertEqual(tool["command"], "files.read")
        self.assertIn("path", tool["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
