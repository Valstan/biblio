"""Тесты токена копии «МиниГена» (25.11, промт #130).

Заказ владельца: «каждая копия по умолчанию получает свою папку в репозитории
программы и токен только на неё; токен выпускает владелец по заявке,
приходящей ему в его собственный „МиниГен“».

Проверяем:

- **токен — не GitHub-кред, а подтверждение папки**: доказательство связывает
  токен с папкой копии, и токен другой копии к этой папке **не подходит**
  (доступ закрыт);
- **секрет живёт вне репозитория** (в сторе на машине) и наружу отдаётся
  только отпечаток и маска;
- **заявка** несёт родословную и подпись, но **не** секрет;
- **«свой/чужой»** определяется по родословной и по закреплённому отпечатку
  (TOFU): подделать заявку под чужую копию не выйдет.

⚠️ Тесты не трогают живой стор программы: пути подменяются на временные.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent.parent
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import minigen_clone as clone_mod   # noqa: E402
import minigen_karman as karman     # noqa: E402
import minigen_lineage as lineage   # noqa: E402
import minigen_state as state       # noqa: E402
import minigen_token as token_mod   # noqa: E402


class _TokenTestCase(unittest.TestCase):
    """Общая подмена живых путей и родословной."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.credentials = root / "credentials"
        self.token_file = self.credentials / "copy-token.txt"
        self.meta_file = self.credentials / "copy-token-meta.json"
        self.secret_file = self.credentials / "copy-local-secret.txt"
        self.trust_file = root / "copy-trust.json"
        self.issued_dir = self.credentials / "issued"
        self._saved = {
            "token": token_mod.TOKEN_FILE,
            "meta": token_mod.META_FILE,
            "secret": token_mod.LOCAL_SECRET_FILE,
            "local": token_mod.LOCAL,
            "issued": token_mod.ISSUED_DIR,
            "trust": token_mod._trust_file,
            "instance": lineage.instance,
            "karman_call": karman.call,
            "karman_project": karman.ensure_project,
            "karman_publish": karman.publish,
            "settings": state.SETTINGS_FILE,
        }
        token_mod.TOKEN_FILE = self.token_file
        token_mod.META_FILE = self.meta_file
        token_mod.LOCAL_SECRET_FILE = self.secret_file
        token_mod.LOCAL = root
        token_mod.ISSUED_DIR = self.issued_dir
        token_mod._trust_file = lambda: self.trust_file
        # ⚠️ Настройки программы — живой файл: подменяем на временный, иначе
        # тест дописал бы путь токена в настройки боевой программы.
        self.settings_file = root / "settings.json"
        state.SETTINGS_FILE = self.settings_file
        lineage.instance = lambda: {"instance_id": "mg-11111111", "family": "minigen",
                                    "generation": 2,
                                    "fork_of": {"commit": "abc1234"}}
        self._karman_calls: list[tuple[str, dict]] = []

    def tearDown(self) -> None:
        token_mod.TOKEN_FILE = self._saved["token"]
        token_mod.META_FILE = self._saved["meta"]
        token_mod.LOCAL_SECRET_FILE = self._saved["secret"]
        token_mod.LOCAL = self._saved["local"]
        token_mod.ISSUED_DIR = self._saved["issued"]
        token_mod._trust_file = self._saved["trust"]
        lineage.instance = self._saved["instance"]
        karman.call = self._saved["karman_call"]
        karman.ensure_project = self._saved["karman_project"]
        karman.publish = self._saved["karman_publish"]
        state.SETTINGS_FILE = self._saved["settings"]
        self.tmp.cleanup()

    def settings(self) -> dict:
        """Настройки программы — как их видит мост."""
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def as_copy(self, instance_id: str) -> None:
        """Машина притворяется другой копией (своя родословная)."""
        lineage.instance = lambda: {"instance_id": instance_id, "family": "minigen",
                                    "generation": 1,
                                    "fork_of": {"commit": "abc1234"}}

    def fake_channel(self, prompts: list[dict]) -> None:
        """Канал КАРМАНА отдаёт заданные промты."""
        def fake_call(method: str, path: str, body=None, token: str = ""):
            return 200, {"prompts": prompts}
        karman.call = fake_call
        karman.ensure_project = lambda name: (200, {"added": [name]})
        karman.publish = lambda project, title, body: (
            201, {"id": "1", "project": project, "title": title})

    @staticmethod
    def request_body(instance_id: str = "mg-22222222", key_id: str = "abcd12345678",
                     signature: str = "sig") -> dict:
        return {
            "kind": "copy-token-request",
            "instance_id": instance_id,
            "name": "копия на другой машине",
            "folder": "copies/%s/" % instance_id,
            "family": "minigen",
            "generation": 1,
            "fork_commit": "abc1234",
            "key_id": key_id,
            "signed_at": 1,
            "signature": signature,
        }


class TokenStoreTest(_TokenTestCase):
    def test_token_is_issued_outside_the_repository(self) -> None:
        report = token_mod.issue()
        self.assertTrue(report.get("ok"), report)
        self.assertTrue(self.token_file.is_file())
        # путь токена — не внутри репозитория программы
        repo = Path(BRIDGE_DIR).parent.parent
        self.assertNotIn(str(repo).lower(), str(self.token_file).lower())
        meta = json.loads(self.meta_file.read_text(encoding="utf-8"))
        self.assertEqual(meta["instance_id"], "mg-11111111")
        self.assertEqual(meta["folder"], "copies/mg-11111111/")
        self.assertTrue(meta["proof"])

    def test_status_shows_only_fingerprint_and_mask(self) -> None:
        token_mod.issue()
        state = token_mod.status()
        self.assertTrue(state["has_token"])
        self.assertTrue(state["token_id"])
        self.assertNotIn(state["token_id"], state["masked"])
        self.assertTrue(state["masked"].endswith(
            self.token_file.read_text(encoding="utf-8")[-4:]))
        self.assertNotIn(self.token_file.read_text(encoding="utf-8"), json.dumps(state))

    def test_token_id_does_not_reveal_the_secret(self) -> None:
        secret = "super-secret-value"
        ident = token_mod.token_id(secret)
        self.assertNotIn(secret, ident)
        self.assertEqual(ident, token_mod.token_id(secret))
        self.assertNotEqual(ident, token_mod.token_id("other"))

    def test_mask_hides_all_but_the_tail(self) -> None:
        masked = token_mod.mask("abcdefgh1234")
        self.assertTrue(masked.endswith("1234"))
        self.assertNotIn("abcdefgh", masked)


class TokenScopeTest(_TokenTestCase):
    def test_own_folder_is_allowed(self) -> None:
        token_mod.issue()
        check = token_mod.verify()
        self.assertTrue(check["ok"], check)
        self.assertIn("copies/mg-11111111", check["reason"])

    def test_foreign_folder_is_refused(self) -> None:
        token_mod.issue()
        check = token_mod.verify(folder="copies/mg-99999999/")
        self.assertFalse(check["ok"])
        self.assertIn("чужая папка", check["reason"])

    def test_foreign_token_is_refused(self) -> None:
        # токен выпущен другой копии: под другой папкой и с чужим доказательством
        other = token_mod.issue(instance_id="mg-99999999",
                                folder="copies/mg-99999999/")
        res = token_mod.set_token(other["token"])
        self.assertFalse(res["ok"], res)
        check = token_mod.verify(folder="copies/mg-99999999/")
        self.assertFalse(check["ok"], check)

    def test_token_without_proof_is_not_confirmed(self) -> None:
        token_mod.issue()
        secret = self.token_file.read_text(encoding="utf-8")
        self.meta_file.unlink()          # токен пришёл без доказательства
        token_mod.set_token(secret)
        state = token_mod.status()
        self.assertFalse(state["bound"])
        self.assertIn("без доказательства", state["reason"])

    def test_check_without_token_is_honest(self) -> None:
        check = token_mod.verify()
        self.assertFalse(check["ok"])
        self.assertIn("токена нет", check["reason"])


class TransferTest(_TokenTestCase):
    """Передача токена от владельца копии — главный путь заказа #130."""

    def test_code_from_issue_is_accepted_by_its_copy(self) -> None:
        issued = token_mod.issue(instance_id="mg-22222222",
                                 folder="copies/mg-22222222/")
        self.as_copy("mg-22222222")
        res = token_mod.set_token(issued["token"])
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["bound"], res)
        check = token_mod.verify()
        self.assertTrue(check["ok"], check)
        self.assertIn("copies/mg-22222222", check["reason"])

    def test_code_of_another_copy_is_refused_and_not_stored(self) -> None:
        issued = token_mod.issue(instance_id="mg-22222222",
                                 folder="copies/mg-22222222/")
        self.as_copy("mg-33333333")
        res = token_mod.set_token(issued["token"])
        self.assertFalse(res["ok"], res)
        self.assertIn("другой копии", res["error"])
        # токен в стор не лёг: чужая копия доступ не получила
        self.assertFalse(self.token_file.is_file())
        check = token_mod.verify()
        self.assertFalse(check["ok"], check)

    def test_issuing_for_another_copy_keeps_the_own_token(self) -> None:
        token_mod.issue()                        # свой токен
        own = self.token_file.read_text(encoding="utf-8")
        token_mod.issue(instance_id="mg-22222222",
                        folder="copies/mg-22222222/")
        self.assertEqual(self.token_file.read_text(encoding="utf-8"), own)
        state = token_mod.status()
        self.assertTrue(state["bound"], state)

    def test_code_carries_the_proof_and_the_record_keeps_no_secret(self) -> None:
        issued = token_mod.issue(instance_id="mg-22222222",
                                 folder="copies/mg-22222222/")
        secret, proof = token_mod.unpack(issued["token"])
        self.assertTrue(secret)
        self.assertTrue(proof)
        self.assertTrue(token_mod.check_proof(secret, "mg-22222222",
                                              "copies/mg-22222222/", proof))
        record = token_mod._issued_record_path("mg-22222222")
        self.assertTrue(record.is_file())
        self.assertNotIn(secret, record.read_text(encoding="utf-8"))

    def test_a_forged_proof_does_not_pass(self) -> None:
        issued = token_mod.issue(instance_id="mg-22222222",
                                 folder="copies/mg-22222222/")
        self.as_copy("mg-22222222")
        secret, proof = token_mod.unpack(issued["token"])
        forged = token_mod.pack(secret, "0" * 64)
        res = token_mod.set_token(forged)
        self.assertFalse(res["ok"], res)
        self.assertFalse(self.token_file.is_file())


class SafeIdentTest(_TokenTestCase):
    """Идентификатор из заявки — путь строить нельзя (D-038)."""

    def test_path_in_instance_id_is_neutralised(self) -> None:
        ident = token_mod.safe_ident("../../etc/passwd")
        self.assertNotIn("/", ident)
        self.assertNotIn("..", ident)
        folder = token_mod.instance_folder("../../etc/passwd")
        self.assertTrue(folder.startswith("copies/"))
        self.assertNotIn("..", folder)

    def test_issued_record_stays_inside_the_store(self) -> None:
        token_mod.issue(instance_id="../../evil")
        record = token_mod._issued_record_path("../../evil")
        self.assertEqual(record.parent, token_mod.ISSUED_DIR)
        self.assertTrue(str(record).startswith(str(self.credentials)))

    def test_folder_is_the_same_in_the_lineage_and_in_the_token(self) -> None:
        """Папка копии — одна на всех: иначе доказательство не сошлось бы.

        ⚠️ Токен привязан к строке папки (HMAC считается от неё). Если клон
        и токен посчитают папку по-разному, выпущенный токен окажется
        «чужим» для своей же копии.
        """
        self.assertEqual(token_mod.instance_folder(),
                         lineage.copies_folder("mg-11111111"))
        self.assertEqual(token_mod.instance_folder(), "copies/mg-11111111/")

    def test_the_clone_registers_the_very_same_folder(self) -> None:
        """Папка, которую заводит клон, — та же, под которую выпущен токен."""
        folder = clone_mod._safe_ident("mg-11111111")
        self.assertEqual("copies/%s" % folder,
                         token_mod.instance_folder().rstrip("/"))


class SettingsPublicationTest(_TokenTestCase):
    """В настройках программы — путь и отпечаток токена, но не сам токен.

    Заказ владельца (25.11, задание 2): «токен не попадает в репозиторий, а
    хранится в сторе на машине копии; в программе — только путь и
    идентификатор токена».
    """

    def test_settings_carry_the_path_and_the_fingerprint(self) -> None:
        token_mod.issue()
        settings = self.settings()
        self.assertEqual(settings.get("copyTokenPath"), str(self.token_file))
        self.assertEqual(settings.get("copyTokenId"),
                         token_mod.token_id(self.token_file.read_text(
                             encoding="utf-8").strip()))
        self.assertEqual(settings.get("copyTokenFolder"), "copies/mg-11111111/")

    def test_the_secret_never_reaches_the_settings(self) -> None:
        issued = token_mod.issue()
        secret = self.token_file.read_text(encoding="utf-8").strip()
        text = self.settings_file.read_text(encoding="utf-8")
        self.assertNotIn(secret, text)
        self.assertNotIn(issued["token"], text)

    def test_the_settings_are_written_when_the_copy_accepts_a_token(self) -> None:
        """Копия получила токен от владельца — путь и отпечаток в настройках."""
        issued = token_mod.issue(instance_id="mg-22222222",
                                 folder="copies/mg-22222222/")
        self.as_copy("mg-22222222")
        res = token_mod.set_token(issued["token"])
        self.assertTrue(res["ok"], res)
        settings = self.settings()
        self.assertEqual(settings.get("copyTokenId"), issued["token_id"])
        self.assertEqual(settings.get("copyTokenFolder"), "copies/mg-22222222/")

    def test_clearing_the_token_clears_the_fingerprint(self) -> None:
        token_mod.issue()
        token_mod.clear_token()
        settings = self.settings()
        self.assertEqual(settings.get("copyTokenId"), "")
        self.assertEqual(settings.get("copyTokenPath"), str(self.token_file))

    def test_status_restores_the_settings_after_they_are_wiped(self) -> None:
        """После обновления программы путь и отпечаток в настройках есть.

        ⚠️ Копия могла получить токен **до** обновления программы: токен в
        сторе есть, а в настройках — пусто. Программа должна это увидеть и
        привести настройки в соответствие, а не молчать.
        """
        token_mod.issue()
        self.settings_file.unlink()
        state.write_settings({})          # файл снова есть, но без токена
        token_mod.status()
        settings = self.settings()
        self.assertEqual(settings.get("copyTokenPath"), str(self.token_file))
        self.assertTrue(settings.get("copyTokenId"))


class RequestTest(_TokenTestCase):
    def test_request_carries_lineage_but_no_secret(self) -> None:
        token_mod.issue()
        payload = token_mod.request_payload("моя копия")
        self.assertEqual(payload["instance_id"], "mg-11111111")
        self.assertEqual(payload["folder"], "copies/mg-11111111/")
        self.assertEqual(payload["family"], "minigen")
        self.assertEqual(payload["generation"], 2)
        self.assertEqual(payload["fork_commit"], "abc1234")
        self.assertTrue(payload["signature"])
        secret = self.token_file.read_text(encoding="utf-8")
        self.assertNotIn(secret, json.dumps(payload))

    def test_request_goes_to_the_section(self) -> None:
        sent: list[tuple[str, str, str]] = []

        def fake_publish(project: str, title: str, body: str):
            sent.append((project, title, body))
            return 201, {"id": "7"}
        karman.ensure_project = lambda name: (200, {"added": [name]})
        karman.publish = fake_publish
        result = token_mod.publish_request()
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(sent[0][0], "MiniGen-tokens")
        self.assertIn("mg-11111111", sent[0][1])
        self.assertIn("copy-token-request", sent[0][2])

    def test_own_copy_is_recognised(self) -> None:
        self.fake_channel([{"id": "1", "body": json.dumps(
            self.request_body(instance_id="mg-11111111"))}])
        items = token_mod.requests()
        self.assertEqual(len(items), 1)
        self.assertIn("своя копия", items[0]["relation"])

    def test_foreign_copy_is_marked_and_pinned(self) -> None:
        self.fake_channel([{"id": "2", "body": json.dumps(
            self.request_body(instance_id="mg-22222222", key_id="key-aaaaaa"))}])
        items = token_mod.requests()
        self.assertIn("чужая копия", items[0]["relation"])
        self.assertIn("не закреплён", items[0]["signature_state"])
        # владелец закрепляет отпечаток — дальше подделка видна
        token_mod.trust("mg-22222222", "key-aaaaaa")
        items = token_mod.requests()
        self.assertIn("закреплённая", items[0]["relation"])
        self.fake_channel([{"id": "3", "body": json.dumps(
            self.request_body(instance_id="mg-22222222", key_id="key-bbbbbb"))}])
        items = token_mod.requests()
        self.assertIn("возможна подделка", items[0]["relation"])

    def test_section_name_is_escaped_in_the_query(self) -> None:
        """Кириллица в имени раздела не должна ронять запрос.

        ⚠️ Живой случай 26.11 (в разделе промтов): имя раздела ушло в адрес
        без экранирования, кириллица роняла запрос (UnicodeEncodeError), а
        владелец видел пустой список заявок и думал, что их нет.
        """
        asked: list[str] = []

        def fake_call(method: str, path: str, body=None, token: str = ""):
            asked.append(path)
            return 200, {"prompts": []}
        karman.call = fake_call
        token_mod.requests(section="Заявки МиниГена")
        self.assertTrue(asked, "раздел должен запрашиваться")
        self.assertNotIn(" ", asked[0])
        self.assertNotIn("Заявки", asked[0])
        self.assertIn("%D0%97%D0%B0%D1%8F%D0%B2%D0%BA%D0%B8", asked[0])


class CloneFolderTest(_TokenTestCase):
    """Дефолт `clone.setup` — папка в общем репозитории, без репозитория на GitHub."""

    def test_default_mode_is_shared(self) -> None:
        self.assertEqual(clone_mod.setup.__defaults__[-1], "shared")


if __name__ == "__main__":
    unittest.main()
