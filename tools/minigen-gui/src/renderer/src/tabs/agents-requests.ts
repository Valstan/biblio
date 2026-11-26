/**
 * Куда уходят заявки агентов — раздел(ы) КАРМАНА.
 *
 * Заявка агента лежит в его репозитории по пути `requests/<раздел>/<файл>`:
 * `<раздел>` — это проект (раздел библиотеки промтов), из которого заявка
 * пришла. Окно показывает это прямо: у агента — сколько заявок и в какие
 * разделы они ушли. Иначе «куда уходят заявки» приходилось бы угадывать по
 * списку разделов канала (а он — про все разделы, не про заявки агента).
 *
 * Логика чистая, без React и без сети — её проверяет тест
 * (`tests/agents-requests.test.ts`), как и остальную вкладку «Агенты».
 */

import type { AgentsSnapshot } from '@shared/types'

/** Разделы, куда ушли заявки агента: по слагу агента. */
export type SectionsByAgent = Record<string, string[]>

/**
 * Разделы, куда ушли заявки каждого агента.
 *
 * Агент без заявок (или агента нет вовсе) — пустой список, а не
 * выдуманный раздел: окно показывает «—», и это правда.
 */
export function requestSections(snapshot: AgentsSnapshot | null): SectionsByAgent {
  const out: SectionsByAgent = {}
  for (const agent of snapshot?.agents ?? []) {
    const slug = (agent.slug || '').trim()
    if (!slug) continue
    const sections: string[] = []
    for (const request of agent.requests ?? []) {
      const section = (request.project || '').trim()
      if (section && !sections.includes(section)) sections.push(section)
    }
    out[slug] = sections
  }
  return out
}

/**
 * Все разделы, куда ушли заявки (по алфавиту) — для карточки канала.
 *
 * Это «куда уходят заявки» по факту, а не список всех разделов канала:
 * разделов у канала может быть двадцать, а заявки агентов лежат в двух.
 */
export function sectionsInUse(byAgent: SectionsByAgent): string[] {
  const all = new Set<string>()
  for (const sections of Object.values(byAgent)) {
    for (const section of sections) all.add(section)
  }
  return [...all].sort()
}

/** Разделы одного агента (пусто — заявок не было). */
export function sectionsOf(byAgent: SectionsByAgent, slug: string): string[] {
  return byAgent[slug] ?? []
}
