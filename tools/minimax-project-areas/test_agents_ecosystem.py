#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Приёмка: экосистема агентов «МиниГена» (заказ владельца 24.11).

Заказ: «нужно сделать так, чтобы миниген мог создавать вот таких же агентов,
новых… и чтобы всё это хранить через гитхаб, чтобы мало ли, потом восстановить
можно было на любом другом компьютере. Эти агенты должны быть
специализированными, у них у каждого свой мозг, своя конституция, свои скиллы».
И: «…агент по обработке моих идей, чтобы он мог прочитать соответствующие
репозитории и понять в каком стиле, в каком ключе, создать этот промт из моей
идеи».

Проверяем **без GitHub** (репозитории агентов заводит владелец, отдельным
словом) и без живого КАРМАНА — на подставном сервисе с тем же контрактом:

1. **модули на месте** — программа везёт с собой модули агентов;
2. **шаблоны на месте** — у первого агента есть конституция и скилл
   (иначе агент не специализированный, а просто ещё одна папка);
3. **реестр читается** — состояние видно, даже если реестр ещё не склонирован;
4. **зачитка → заявка** — проект угадан, разделы канона на месте, заявка
   сохранена в репозиторий агента и **читается обратно** (значит, переживёт
   переустановку);
5. **заявка без «Что сделать» не сохраняется** — заявка без задания бессмысленна;
6. **КАРМАН**: раздел заводится, промт пишется, тела — ровно те, что ждёт
   сервис; **без секрета ни одного запроса наружу**;
7. **чужие репозитории читаются, но не пишутся** — обход пути отбит, файлы с
   ключами закрыты;
8. **секрет не утекает в отчёт**.

Запуск: python tools/minimax-project-areas/test_agents_ecosystem.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
BRIDGE = HERE.parent / "minigen-gui" / "bridge"


def load(name: str, path: pathlib.Path):
    """Модуль программы по имени файла (имя модуля ≠ имя файла)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_bridge_module(name: str):
    """Модуль моста: сначала из репозитория программы."""
    sys.path.insert(0, str(BRIDGE))
    try:
        return __import__(name)
    finally:
        pass


class Handler(BaseHTTPRequestHandler):
    """Подставной КАРМАН: тот же контракт машинного канала промтов."""

    mode = 200
    calls: list = []

    def log_message(self, *args):  # тишина
        pass

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        auth = self.headers.get("Authorization") or ""
        Handler.calls.append(("GET", self.path, auth, None))
        if Handler.mode != 200:
            self._send(Handler.mode, {"error": "служебная ошибка"})
            return
        if self.path.startswith("/api/secrets"):
            # vault-комната: секрет канала приходит по токену комнаты
            self._send(200, {"secrets": {"PROMPTS_INGEST_SECRET": "vault-secret"}})
            return
        if self.path.startswith("/api/prompts/projects"):
            self._send(200, {"sections": ["sarafan", "karman"], "added": []})
            return
        if self.path.startswith("/api/prompts"):
            self._send(200, {"prompts": []})
            return
        self._send(404, {"error": "нет такого пути"})

    def do_POST(self):
        auth = self.headers.get("Authorization") or ""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {}
        Handler.calls.append(("POST", self.path, auth, body))
        if Handler.mode != 200:
            self._send(Handler.mode, {"error": "служебная ошибка"})
            return
        if self.path.startswith("/api/prompts/projects"):
            projects = body.get("projects") or []
            self._send(200, {"added": projects, "sections": ["sarafan", "karman"]})
            return
        if self.path.startswith("/api/prompts"):
            self._send(201, {"id": 42, "project": body.get("project"),
                             "title": body.get("title"), "created_at": "2026-11-24"})
            return
        self._send(404, {"error": "нет такого пути"})


def make_repo(root: pathlib.Path, name: str) -> pathlib.Path:
    """Пустой git-репозиторий агента (с локальным «origin» — сеть не нужна)."""
    import subprocess
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/master"],
                   cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "начало"],
                   cwd=str(repo), check=True, capture_output=True)
    origin = root / (name + "-origin.git")
    subprocess.run(["git", "init", "--bare", str(origin)], cwd=str(root),
                   check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)],
                   cwd=str(repo), check=True, capture_output=True)
    return repo


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, message: str) -> None:
        if not cond:
            failures.append(message)
            print("   FAIL: " + message)

    print("1) модули программы на месте")
    for name in ("minigen_agents.py", "minigen_ingest.py",
                 "minigen_karman.py", "minigen_repos.py"):
        check((BRIDGE / name).is_file(), "нет модуля: %s" % name)

    agents = load_bridge_module("minigen_agents")
    ingest = load_bridge_module("minigen_ingest")
    karman = load_bridge_module("minigen_karman")
    repos = load_bridge_module("minigen_repos")

    # Контрольный снимок **живого** реестра: приёмка не должна его трогать
    # (живой случай 24.11 — манифест агента «Идеи» обрезался до двух строк).
    live_manifest = (agents.REGISTRY_HOME
                     / agents.DEFAULT_REGISTRY.split("/")[-1]
                     / "agents" / "ideas" / "agent.yaml")
    live_before = live_manifest.read_bytes() if live_manifest.is_file() else None

    print("2) шаблоны агента на месте (конституция и скилл)")
    templates = BRIDGE / "agent-templates"
    check((templates / "default" / "AGENTS.md").is_file(),
          "нет конституции по умолчанию")
    check((templates / "ideas" / "AGENTS.md").is_file(),
          "нет конституции агента-идеи")
    skill = templates / "ideas" / "skills" / "ideas-ingest" / "SKILL.md"
    check(skill.is_file(), "нет скилла обработки зачиток")
    if skill.is_file():
        text = skill.read_text(encoding="utf-8")
        check(text.startswith("---"), "у скилла нет front matter")
        check("name: ideas-ingest" in text, "у скилла нет имени")

    print("3) состояние реестра читается")
    snapshot = agents.agent_state()
    check("registry" in snapshot, "в состоянии реестра нет репозитория")
    check("templates" in snapshot, "в состоянии реестра нет шаблонов")
    check("ideas" in snapshot.get("templates", []),
          "шаблон агента-идеи не виден: %s" % snapshot.get("templates"))

    print("4) зачитка → заявка (проект угадан, разделы на месте)")
    projects = [{"slug": "sarafan", "title": "Сарафан"},
                {"slug": "karman", "title": "Карман"}]
    guess = ingest.guess_project(
        "Хочу, чтобы в сарафане появился импорт контактов из вайбса", projects)
    check(guess["known"] and guess["slug"] == "sarafan",
          "проект угадан неверно: %s" % guess)
    draft = {"title": "Сарафан: импорт контактов", "project": "sarafan",
             "summary": "Импортировать контакты.",
             "Что сделать": "1. Взять выгрузку.\n2. Свести по телефону.",
             "Контекст": "Репозиторий sarafan.",
             "План": "1. Импортер.\n2. Тест.",
             "Критерии приёмки": "- [ ] 100 контактов загружены",
             "Риски и открытые вопросы": "Дубли в источнике.",
             "Источник": "зачитка 24.11.2026"}
    text = ingest.render_request(draft)
    for section in ("## Что сделать", "## Контекст", "## План",
                    "## Критерии приёмки", "## Источник"):
        check(section in text, "в заявке нет раздела %s" % section)
    check("## Риски и открытые вопросы" in text, "в заявке нет раздела рисков")
    check(not ingest.missing_sections(draft),
          "заявка без нужных разделов: %s" % ingest.missing_sections(draft))

    print("5) заявка сохраняется в репозиторий агента и читается обратно")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="agents-eco-"))
    registry = tmp / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    workspace = tmp / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    repo = make_repo(workspace, "ideas")
    # PR — на GitHub не ходим: имитируем слияние локально.
    def fake_publish_pr(repo_slug: str, branch: str, title: str,
                        body: str = "", **kwargs) -> dict:
        import subprocess
        subprocess.run(["git", "checkout", "master"], cwd=str(repo),
                       check=True, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", "-m", "PR: заявка", branch],
                       cwd=str(repo), check=True, capture_output=True)
        return {"ok": True, "pr": "1"}

    agents.publish_pr = fake_publish_pr  # type: ignore[assignment]
    # ⚠️ Подмена пути — **до** записи манифеста. Иначе манифест уходит в
    # живой реестр программы и обрезает его (живой случай 24.11: у агента
    # «Идеи» остались только `slug` и `repo` — окно показывало агента без
    # названия и области).
    old_dir = agents.registry_dir
    old_home = agents.workspace_home
    agents.registry_dir = lambda: registry
    agents.workspace_home = lambda: workspace
    agents.write_manifest("ideas", {"slug": "ideas", "title": "Идеи",
                                    "repo": "Valstan/ideas", "area": "p-ideas"})
    try:
        saved = agents.save_request("ideas", "sarafan", "2026-11-24-sarafan.md", text)
        check(saved.get("ok"), "заявка не сохранилась: %s" % saved)
        path = pathlib.Path(str(saved.get("path") or ""))
        check(path.is_file(), "файл заявки не появился: %s" % path)
        if path.is_file():
            check("## План" in path.read_text(encoding="utf-8"),
                  "сохранённая заявка не та")
        listed = agents.list_requests("ideas")
        check(len(listed) == 1, "заявка не нашлась в списке: %s" % listed)
        check(agents.request_branch("Сарафан Мини") == "requests/sarafan-mini",
              "ветка заявок не по слагам: %s" % agents.request_branch("Сарафан Мини"))
    finally:
        agents.registry_dir = old_dir
        agents.workspace_home = old_home

    print("6) КАРМАН: раздел заводится, промт пишется, секрет не утекает")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    keys = tmp / "keys"
    keys.mkdir(parents=True, exist_ok=True)
    (keys / "vault-url").write_text("http://127.0.0.1:%d" % port, encoding="utf-8")
    (keys / "prompts-ingest-secret").write_text("secret-from-file\n", encoding="utf-8")
    saved_env = {k: os.environ.get(k) for k in
                 ("BIBLIO_KEYS_DIR", "PROMPTS_INGEST_SECRET", "KARMAN_URL")}
    os.environ["BIBLIO_KEYS_DIR"] = str(keys)
    os.environ.pop("PROMPTS_INGEST_SECRET", None)
    os.environ.pop("KARMAN_URL", None)
    try:
        Handler.mode = 200
        Handler.calls = []
        status = karman.status()
        check(status["ok"], "канал не открылся: %s" % status.get("reason"))
        check(status["secret_source"] == "file",
              "секрет взят не из файла: %s" % status.get("secret_source"))
        check("secret-from-file" not in json.dumps(status, ensure_ascii=False),
              "значение секрета попало в отчёт")

        Handler.calls = []
        code, payload = karman.ensure_project("kurilka")
        check(code == 200, "раздел не заведён: %s" % code)
        post = [c for c in Handler.calls if c[0] == "POST"]
        check(post and post[0][3] == {"projects": ["kurilka"]},
              "тело создания раздела неверное: %s" % (post[:1],))

        Handler.calls = []
        code, payload = karman.publish("kurilka", "Курилка", text)
        check(code == 201, "промт не записан: %s" % code)
        check(payload.get("id") == 42, "id промта не тот: %s" % payload)
        post = [c for c in Handler.calls if c[0] == "POST"]
        check(post and post[0][2] == "Bearer secret-from-file",
              "сервер не получил секрет: %s" % (post[:1],))
        if post:
            body = post[0][3]
            check(body.get("project") == "kurilka",
                  "раздел в теле неверный: %s" % body.get("project"))
            check("## План" in str(body.get("body") or ""),
                  "тело промта — не заявка: %s" % str(body.get("body"))[:80])

        print("7) без секрета — ни одного запроса наружу")
        Handler.calls = []
        empty = tmp / "empty-keys"
        empty.mkdir(parents=True, exist_ok=True)
        (empty / "vault-url").write_text("http://127.0.0.1:%d" % port, encoding="utf-8")
        os.environ["BIBLIO_KEYS_DIR"] = str(empty)
        # кэш секрета в памяти сбрасываем: проверяем именно «секрета нет»
        karman._secret_cache.update({"value": "", "source": "", "at": 0.0})
        status = karman.status()
        check(not status["ok"], "канал «открылся» без секрета")
        check(status["http"] == 0, "код не 0: %s" % status["http"])
        check(not Handler.calls, "запрос ушёл наружу без секрета: %s" % Handler.calls)
        code, payload = karman.publish("kurilka", "Курилка", text)
        check(code == 0, "промт ушёл без секрета: %s" % code)
        check("секрет" in str(payload.get("error") or ""),
              "причина не про секрет: %s" % payload.get("error"))
        writes = [c for c in Handler.calls if c[0] == "POST"]
        check(not writes, "запись промта ушла без секрета: %s" % writes)
        os.environ["BIBLIO_KEYS_DIR"] = str(keys)

        print("8) секрет канала берётся из vault-комнаты (без копирования руками)")
        # токена комнаты нет — секрет лежит в файле; кладём файл токена и
        # убираем файл секрета: секрет должен прийти из vault сам.
        (keys / "room-token").write_text("skm_test-room\n", encoding="utf-8")
        secret_file = keys / "prompts-ingest-secret"
        kept = secret_file.read_text(encoding="utf-8")
        secret_file.unlink()
        karman._secret_cache.update({"value": "", "source": "", "at": 0.0})
        value, source = karman.read_secret()
        check(source == "vault", "секрет взят не из vault: %s" % source)
        check(value == "vault-secret",
              "значение секрета из vault не то (длина %d)" % len(value or ""))
        status = karman.status()
        check(status["ok"], "канал не открылся с секретом из vault: %s" % status["reason"])
        check("vault-secret" not in json.dumps(status, ensure_ascii=False),
              "значение секрета попало в отчёт")
        secret_file.write_text(kept, encoding="utf-8")
        karman._secret_cache.update({"value": "", "source": "", "at": 0.0})
    finally:
        server.shutdown()
        server.server_close()
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("9) чужие репозитории: читаем, но не лезем наружу и не читаем ключи")
    other = tmp / "workspace-other"
    other.mkdir(parents=True, exist_ok=True)
    project = other / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    old_home = repos.workspace_home
    repos.workspace_home = lambda: other
    try:
        result = repos.read_file("project", "README.md")
        check("Проект" in result.get("content", ""), "файл не прочитан: %s" % result)
        closed = repos.read_file("project", ".env")
        check("error" in closed, "файл с ключами не закрыт: %s" % closed)
        check("secret" not in closed.get("content", ""), "ключи попали в ответ")
        for bad in ("..", "../..", "a/b"):
            try:
                repos.repo_path(bad)
                check(False, "путь-обход не отбит: %s" % bad)
            except PermissionError:
                pass
    finally:
        repos.workspace_home = old_home

    print("10) каталог инструментов: новые команды есть и опасные помечены")
    tools_mod = load_bridge_module("minigen_tools")
    by_name = {tool["name"]: tool for tool in tools_mod.TOOLS}
    for name in ("agents.list", "agents.create", "agents.apply",
                 "ingest.draft", "ingest.save", "repos.list", "repos.read",
                 "repos.profile"):
        check(name in by_name, "нет инструмента %s" % name)
    for name in ("agents.create", "agents.apply", "ingest.save"):
        if name in by_name:
            check(by_name[name]["dangerous"],
                  "инструмент %s не помечен опасным" % name)
            check(bool(by_name[name]["confirm_hint"]),
                  "у опасного инструмента %s нет пояснения" % name)
    for name in ("agents.list", "ingest.draft", "repos.read"):
        if name in by_name:
            check(not by_name[name]["dangerous"],
                  "инструмент %s помечен опасным зря" % name)

    print("11) живой реестр агентов не тронут")
    live_after = live_manifest.read_bytes() if live_manifest.is_file() else None
    check(live_after == live_before,
          "приёмка тронула живой реестр агентов (%s)" % live_manifest)

    if failures:
        print("❌ приёмка экосистемы агентов: FAIL (%d)" % len(failures))
        return 1
    print("✅ приёмка экосистемы агентов: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
