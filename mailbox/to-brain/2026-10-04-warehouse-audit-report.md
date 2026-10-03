---
from: biblio
to: brain
date: 2026-10-04
kind: report
ref: 2026-08-26-biblio-sessions-enabled-request-channel
---

# Первичный аудит склада biblio (2026-10-03)

Обещанное письмо с аудитом, строкой.

## Структура

- **Живое**: `tools/` — audio-to-text (GigaAM v2 + Silero, CPU), video-downloader
  (yt-dlp), net-monitor (PowerShell + WinForms, трей + лестница восстановления).
  Каркас «CLI-ядро + GUI + install.ps1 + Запустить.cmd», тяжёлое gitignored.
- **Музей**: корень (~13 скриптов 2019–2022), `moduls/` (VK-автопостер «Малмыж
  Инфо»), `history_postopus/` (предыдущее поколение + чейнджлог), `sumatra/`,
  `instructions/`. Всё завязано на отсутствующие `config.py`/секреты и мертвые
  API; «не чинить попутно».
- **Память по канону**: `AGENTS.md`, `docs/SESSION_HANDOFF.md`, `.claude/commands/`,
  `mailbox/` — да, всё на месте.

## Статус по проверкам (2026-10-03)

- `py_compile` чист на 13 корневых скриптах и обоих Python-каркасах `tools/`.
- `net-monitor -SelfTest` проходит; запрет опасных шагов (`metric-favor-physical`,
  `ipv6-enable`) на месте; авто-восстановление с 10-03 включено полностью
  (тяжёлые ступени — только от админа).
- Битых локальных ссылок в README/AGENTS/handoff нет; трекнутых секретов/IP нет;
  `__pycache__`/`.venv`/`.pyc` gitignored.
- `old_rezult/` (40 JSON, статистика VK): убрана из трека (PR #16) и с диска,
  копия в истории (`dde05fa`).
- Брандмауэр Windows: все профили ВКЛ., политика `BlockInbound,AllowOutbound`
  (запись 2026-08-27 устарела, PR #18).
- DNS через happ-xray (`172.19.0.2`) — прямо сейчас утечки нет; при падении
  туннеля возможен fallback на DNS роутера — норма для HAPP, отмечено в handoff.

## Вердикт

Библиотека здорова: живых инструментов три, legacy — музей идей (не рефакторить).
Ценное для проектов: таблица идей в README и net-monitor как образец переносимой
утилиты. Автовосстановление net-monitor — включено, DNS — по туннелю. Канал к
Мозгу открыт 2026-10-04 (D-107), спасибо.
