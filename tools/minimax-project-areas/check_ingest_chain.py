"""Живая проверка: заявка → репозиторий агента → раздел КАРМАНА.

Запуск: python tools/minimax-project-areas/check_ingest_chain.py
(пишет настоящую заявку в репозиторий агента «Идеи» и публикует её
в раздел КАРМАНА — тем же кодом, что и команда `ingest.save`.)
"""
from __future__ import annotations

import json
import pathlib
import sys

BRIDGE = pathlib.Path(__file__).resolve().parent.parent / "minigen-gui" / "bridge"
sys.path.insert(0, str(BRIDGE))

import minigen_bridge as bridge   # noqa: E402

BODY = """# МиниГен: вкладка «Агенты» в окне

**Проект:** MiniGen
**Суть:** Показать агентов в окне программы и дать заводить их оттуда.

## Что сделать

1. Добавить в окно программы вкладку «Агенты»: список агентов реестра (slug,
   название, роль, репозиторий, область, число заявок) и действие
   «Восстановить» (`agents.apply`).
2. Показывать состояние канала КАРМАНА (жив или не настроен) и раздел, куда
   уходят заявки.
3. Не дублировать логику программы: окно спрашивает мост командами
   `agents.list` и `agents.state`, как и остальные вкладки.

## Контекст

- Реестр агентов: `Valstan/minigen-agents` (приватный), манифест
  `agents/<slug>/agent.yaml`.
- Первый агент: «Идеи» — репозиторий `Valstan/ideas`, область `p-ideas`,
  скилл `ideas-ingest`.
- Команды моста уже есть: `agents.list`, `agents.state`, `agents.create`,
  `agents.apply`, `ingest.draft`, `ingest.save`, `repos.list`, `repos.read`,
  `repos.profile`.

## План

1. Вкладка «Агенты» (`src/renderer/src/tabs/AgentsTab.tsx`) и пункт в списке
   вкладок окна.
2. Данные — существующим вызовом моста (`agents.list`), типы — в
   `src/shared/types.ts`.
3. Приёмка: тест окна проверяет, что вкладка открывается и показывает
   агента «Идеи».

## Критерии приёмки

- [ ] Вкладка «Агенты» открывается и показывает агента «Идеи» с репозиторием
      и областью.
- [ ] `npm run typecheck` и `npm test` — зелёные.

## Риски и открытые вопросы

- Создание агента из окна — опасное действие (создаёт репозиторий на
  GitHub), поэтому только с подтверждением владельца.
- Агентов может быть много: список берётся из локального клона реестра,
  сети окну не нужно.

## Источник

Заявка собрана агентом «Идеи» 24.11.2026 (живая проверка цепочки
заявка → репозиторий агента → раздел КАРМАНА).
"""


class Fake:
    """Подмена Bridge: нужны только методы самой команды."""


def main() -> int:
    # настоящие методы Bridge, но без тяжёлой инициализации (демоны, надзиратель)
    fake = bridge.Bridge.__new__(bridge.Bridge)
    result = bridge.Bridge.cmd_ingest_save(fake, {
        "agent": "ideas",
        "title": "МиниГен: вкладка «Агенты» в окне",
        "project": "MiniGen",
        "summary": "Показать агентов в окне программы и дать заводить их оттуда.",
        "body": BODY,
    })
    printable = {k: v for k, v in result.items() if k != "text"}
    print(json.dumps(printable, ensure_ascii=False, indent=1))
    if result.get("error"):
        return 1
    karman = result.get("karman") or {}
    if not karman.get("published"):
        print("❌ в КАРМАН не опубликовано: %s" % karman.get("reason"))
        return 1
    print("✅ заявка сохранена в репозиторий агента и опубликована в раздел "
          "КАРМАНА (id %s)" % karman.get("id"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
