---
description: Старт сессии biblio — синхра только своего репо + проверка канала from-brain + чтение SESSION_HANDOFF
---

Выполни старт сессии **biblio** строго по шагам. Порядок жёсткий: **сначала синхронизация
своего репо, потом чтение session-памяти** (pool #032). Чужие репозитории, включая
`../brain_matrica`, **не синхронизировать вообще**: никаких `fetch`/`pull`/`checkout` —
только чтение (мандат владельца 2026-08-04).

1. **Sync СВОЙ репо — единственная синхронизация:** `git fetch`; если working tree чист
   и есть отставание — `git checkout master && git pull --ff-only`. Незакоммиченное /
   не-ff — сообщи, не форсируй.
2. **Входящие от brain (канал может отсутствовать — это норма):**
   - Локально: если существует каталог `../brain_matrica/mailboxes/biblio/from-brain/` —
     прочитай `*.md` в его корне (НЕ `DRAFTS/`, НЕ `ARCHIVE/`); read-only, не pull'ить.
   - GitHub `main` Мозга, без clone/fetch/pull:
     `gh api "repos/Valstan/brain_matrica/contents/mailboxes/biblio/from-brain?ref=main" --jq '.[] | select(.type=="file") | .name'`
     (ответ 404 = канал не открыт, продолжай молча).
   - Набор писем = объединение каналов.
3. **Доложи** сводку писем (или «канал from-brain не открыт») ДО чтения handoff.
4. **Прочитай** `docs/SESSION_HANDOFF.md`. Если `Updated:` старше 30 дней — пометь
   «может быть неактуально» (biblio — склад, длинные паузы здесь норма).
5. **Сводка master:** `git log --oneline -5` и `git status`.
6. Кратко предложи следующий шаг из handoff или спроси владельца, что делаем.

Не начинай правки до завершения шагов 1–4.
