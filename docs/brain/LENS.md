# Линза проекта «МиниГен» — карта и связи

> Вход в систему мозга проекта — [`README.md`](README.md).
>
> **Живой документ.** Правило жизни линзы записано **в правилах проекта**:
> [`AGENTS.md`](../../AGENTS.md), раздел «Система мозга проекта» —
> *изменился проект — обнови линзу и реестр*. Линза не отменяет
> [`ARCHITECTURE.md`](../../tools/minigen-gui/docs/ARCHITECTURE.md): там — устройство
> программы до строки, здесь — **карта целиком** (модули, связи, приём и разбор)
> и ответ на вопрос «что это за проект и как в нём работают».
>
> Проверка: `python tools/minimax-project-areas/test_brain_system.py` — линза не
> должна расходиться с кодом (пути из линзы обязаны существовать).

## Что это за проект

Программа владельца «МиниГен» — **одно окно вместо браузера и веб-панели**.
Она ведёт рабочие области агентов, сторожит сеть и сессии, помогает агенту
чинить код и выращивает наработки (гены) от других копий. Репозиторий `biblio` —
склад старых утилит владельца, в котором эта программа живёт и развивается.

## Слои и модули

### Слой 1 — ядро (Python, боевой код)

Живёт в `C:\Users\valstan\bin` (боевая копия); в репозитории — **эталон**.
Правило слоёв: то, что умеет ядро, GUI не переписывает — GUI спрашивает и
показывает.

| модуль | путь | что даёт |
|---|---|---|
| Ядро | `tools/minimax-project-areas/minigen_core.py` | журнал, состояние на диске, конфиг областей, чтение базы рантайма, запуск `mcode` |
| Лечащий | `tools/minimax-project-areas/minimax-autheal.py` | отказы аккаунтов (вердикты T1–T4), переустановка входа, перезапуск области |
| Сторож сессий | `tools/minimax-project-areas/minimax-sessionwatch.py` | классификация сессий, «Продолжай», ротация, закрытие |
| Сторож сети | `tools/minimax-project-areas/minimax-netwatch.py` | проба путей и вендора, каскад восстановления сети |
| Вход и очки | `tools/minimax-project-areas/minimax-switch.py` | вход области, очки, список областей, «новый вход (токен)» |
| Очки и пороги | `tools/minimax-project-areas/minimax-points.py` | остаток очков с сайта, учёт расхода, пороги, проба аккаунта |
| Сторож «осиротевших» | `tools/minimax-project-areas/minimax-orphans.py` | сессии без хозяина |
| Лечение ниток | `tools/minimax-project-areas/minigen_thread.py` | разбор и лечение ниток обрыва |
| Время | `tools/minimax-project-areas/minigen_time.py` | московское время (UTC+3) |
| Разовая помощь | `tools/minimax-project-areas/safe_heal.py` | лечение области по команде |
| Ночные карусели | `tools/minimax-project-areas/carousel/` | ночные проходы по сессиям проектов |
| Раскатка | `tools/minimax-project-areas/minigen_rollout.py` | доставка программы на боевую машину (**только по слову владельца**) |

### Слой 2 — мост (Python, `tools/minigen-gui/bridge/`)

| модуль | путь | что даёт |
|---|---|---|
| Мост | `tools/minigen-gui/bridge/minigen_bridge.py` | протокол JSON-lines, реестр команд, действия, чат |
| Состояние | `tools/minigen-gui/bridge/minigen_state.py` | сбор состояния из источников в одно, статистика, настройки, **счётчик активности области** (по журналу автомата аккаунтов) |
| Удаление агента | `tools/minigen-gui/bridge/minigen_delete.py` | удаление агента целиком: проверка синхронизации с GitHub → план → подтверждение → чистка |
| Хранилище | `tools/minigen-gui/bridge/minigen_db.py` | локальная база «МиниГена»: универсальный контейнер записей, выгрузка/импорт |
| Агент | `tools/minigen-gui/bridge/minigen_agent.py` | клиент модели (Anthropic-протокол), агент-цикл, подтверждения |
| Реестр агентов | `tools/minigen-gui/bridge/minigen_agents.py` | агенты проекта: репозиторий, область, заявки |
| **Совет в программе** | `tools/minigen-gui/bridge/minigen_council.py` | совет специалистов: секретарь → специалисты → судья → исполнители |
| Роли совета | `tools/minigen-gui/bridge/council/registry.json` | реестр ролей программы + брифы в `tools/minigen-gui/bridge/council/roles/` |
| Инструменты | `tools/minigen-gui/bridge/minigen_tools.py` | каталог инструментов встроенной нейросети |
| Рабочий стол кода | `tools/minigen-gui/bridge/minigen_code.py` | чтение кода, правка в отдельном worktree, проверки, PR, коммит |
| Гены | `tools/minigen-gui/bridge/minigen_genes.py` | пул наработок: публикация, внедрение, откат, рейтинг, «не ставить» |
| Родословная | `tools/minigen-gui/bridge/minigen_lineage.py` | кто на чём сделан, свой коммит, отметки отката |
| Карта | `tools/minigen-gui/bridge/minigen_map.py` | карта родословной и пула |
| Демоны | `tools/minigen-gui/bridge/minigen_daemons.py` | определение демонов, старт/стоп/рестарт |
| Надзиратель | `tools/minigen-gui/bridge/minigen_lock.py` | ровно один надзиратель демонов (системная блокировка файла) |
| Установка | `tools/minigen-gui/bridge/minigen_install.py` | установка программы и ярлыков |
| Обновление | `tools/minigen-gui/bridge/minigen_update.py` | обновление программы «из окна» |
| КАРМАН | `tools/minigen-gui/bridge/minigen_karman.py` | канал промтов раздела `MiniGen` |
| Промты | `tools/minigen-gui/bridge/minigen_prompts.py` | промты и планы, место хранения |
| Приём | `tools/minimax-project-areas/minigen_karman_prompts.py` | клиент раздела КАРМАНА `MiniGen` (list/--done) |
| Файлы | `tools/minigen-gui/bridge/minigen_files.py` | ридер файлов оператора с потолками |
| Ошибки | `tools/minigen-gui/bridge/minigen_errors.py` | критические ошибки программы → задача управляющему |
| Лечение | `tools/minigen-gui/bridge/minigen_treatment.py` | лечение сессий и ниток на стороне моста |
| Копии | `tools/minigen-gui/bridge/minigen_clone.py` | вторая копия проекта |
| Передача наработок | `tools/minigen-gui/bridge/minigen_copy.py` | передача генов между копиями |
| API и вендор | `tools/minigen-gui/bridge/minigen_api.py`, `tools/minigen-gui/bridge/minigen_provider.py` | локальный API и разговор с моделью |
| Токен | `tools/minigen-gui/bridge/minigen_token.py` | хранение и проверка токена области |
| Репозитории | `tools/minigen-gui/bridge/minigen_repos.py` | работа с репозиториями агентов |
| История чата | `tools/minigen-gui/bridge/minigen_chat_store.py` | видимая история чата на диске машины |
| Конституции агентов | `tools/minigen-gui/bridge/agent-templates/` | конституция и скиллы агентов (два набора: default и ideas) |

### Слой 3 — окно (Electron + TypeScript + React, `tools/minigen-gui/src/`)

| модуль | путь | что даёт |
|---|---|---|
| Процесс главный | `tools/minigen-gui/src/main/index.ts` | окно, IPC, запуск моста, дымовой режим |
| Мост из окна | `tools/minigen-gui/src/main/bridge.ts` | дочерний процесс Python, JSON-lines |
| Дымовой режим | `tools/minigen-gui/src/main/smoke.ts` | снимки вкладок для приёмки глазами |
| Мост в окно | `tools/minigen-gui/src/preload/index.ts` | `contextBridge`: `call`, `openExternal`, `saveText`, `pickFiles` |
| Оболочка | `tools/minigen-gui/src/renderer/src/App.tsx` | вкладки, опрос состояния, обработка ошибок |
| Вкладки | `tools/minigen-gui/src/renderer/src/tabs/` | Области · Статистика · Сеть · Чат · Журнал · Гены · Агенты · Промты · Обновление · Настройки |
| Контракт | `tools/minigen-gui/src/shared/types.ts` | общий контракт данных окна и моста |

### Слой 4 — встроенная нейросеть

| модуль | путь | что даёт |
|---|---|---|
| Знание программы о себе | `tools/minigen-gui/docs/knowledge-pack.md` | системный промпт встроенной нейросети |
| Совет | `tools/minigen-gui/bridge/council/roles/` | брифы ролей совета (супер-программист, дизайнер, UX, код, проверка, презентация) |

## Связи (как слои соединены)

```
владелец
   │  КАРМАН, раздел MiniGen
   ▼
клиент раздела ──────────► агент-цикл (слой 4) ──► мост (слой 2) ──► ядро (слой 1)
                                   │                                        │
                                   │  совет в программе                    │ сторожит
                                   ▼                                        ▼
                            окно (слой 3) ◄──── state.snapshot ────────────┘
```

Четыре главных потока:

1. **Наблюдение** (раз в 5 с): окно → `state.snapshot` → мост → ядро → снимок →
   окно.
2. **Действие**: кнопка → `minigen:call` → мост → команда → функция ядра →
   журнал → ответ в окно.
3. **Задача** (главный поток проекта): КАРМАН → `minigen_karman_prompts.py list`
   → агент → **правка в своём worktree** → ветка → PR → проверки → merge →
   раскатка (за владельцем).
4. **Совет в программе**: агент-цикл → `minigen_council.py` → роли из
   `bridge/council/registry.json` → решение → исполнители.

## Где живёт мозг проекта

| часть | путь | что в неё пишут |
|---|---|---|
| Правила | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.claude/commands/`, `.opencode/commands/` | постулаты, границы, исполнительные памятки |
| Карта | `docs/brain/LENS.md`, `tools/minigen-gui/docs/ARCHITECTURE.md` | где что лежит |
| Реестр ролей | `docs/brain/registry.json`, `docs/brain/COUNCIL.md` | кто какую роль играет |
| Передачи смены | `docs/SESSION_HANDOFF.md` | разбор задачи: кто позвал, что решили, что сделали, что показал стенд |
| Письма в мозг | `mailbox/to-brain/` | переносимое правило для всей экосистемы |
| Скиллы | `tools/minigen-gui/bridge/agent-templates/ideas/skills/` | повторяемая процедура |
| Знание программы | `tools/minigen-gui/docs/knowledge-pack.md` | что программа «знает» о себе и о проекте |
| Приёмочные отчёты | `tools/minigen-gui/docs/LIVE-CHECK-REPORT.md`, `tools/minigen-gui/docs/SELFDEBUG-REPORT.md` | что показала живая проверка |

## Как устроен приём и разбор новых заданий

Коротко (подробно — в [INTAKE.md](INTAKE.md), роли — в [COUNCIL.md](COUNCIL.md)):

```
новый промт
   ▼
координатор: читает опыт проекта → пишет план → выбирает маршрут и состав
   │
   ├─ простая задача ──────────────► программист ──► критик ──► тестер
   │
   └─ сложная/спорная ─► разведчик ─► совет (≤3 голоса) ─► программист ──► критик ──► тестер
                                   │
                                   ▼
                       разбор в docs/SESSION_HANDOFF.md
                       + письмо в mailbox/to-brain/ (если есть переносимое правило)
                       + скилл (если появилась повторяемая процедура)
```

Правило приёма одно: **новое задание не разбирается заново с нуля** — сначала
поднимается накопленный опыт проекта (`SESSION_HANDOFF`, письма в мозг, скиллы,
knowledge-pack), и только потом принимается решение о маршруте и составе.

## Правило жизни линзы

> **Изменился проект — обнови линзу и реестр.**

Записано в правилах проекта: [`AGENTS.md`](../../AGENTS.md), раздел «Система
мозга проекта». Что считается изменением проекта:

- новый модуль программы или новая связь между модулями;
- новая команда моста или новое действие окна;
- новое место хранения знаний (письма, скиллы, приёмки);
- новая роль в совете или смена потолка совета;
- переезд программы, ядра или стенда.

Что делать при таком изменении:

1. обновить таблицу модулей в этом файле (путь → что даёт);
2. обновить [`registry.json`](registry.json), если изменились роли или пределы;
3. прогнать `python tools/minimax-project-areas/test_brain_system.py` — линза
   обязана совпадать с кодом;
4. вписать изменение в разбор задачи в `docs/SESSION_HANDOFF.md`.

**Проверка расхождения** (как в приёмке): путь, названный в линзе, обязан
существовать; роль, названная в линзе или реестре, обязана быть описана в
[`COUNCIL.md`](COUNCIL.md); роль совета программы, на которую ссылается реестр,
обязана существовать в `tools/minigen-gui/bridge/council/registry.json`.
