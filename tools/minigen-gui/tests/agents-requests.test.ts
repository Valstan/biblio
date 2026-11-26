import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'
import type { AgentsSnapshot } from '../src/shared/types'
import {
  requestSections,
  sectionsInUse,
  sectionsOf
} from '../src/renderer/src/tabs/agents-requests'

/**
 * «Куда уходят заявки» — вкладка «Агенты» в окне «МиниГена» (заявка
 * владельца от 24.11, раздел КАРМАНА «MiniGen», промт #122).
 *
 * Заявка агента лежит в его репозитории по пути `requests/<раздел>/<файл>`.
 * Окно должно называть этот раздел: иначе видно лишь «сколько заявок», а
 * куда они ушли — по догадке. Разделы канала — про все разделы, а не про
 * заявки агента, поэтому они не заменяют этот ответ.
 */

const src = join(__dirname, '../src')

function snapshot(over: Partial<AgentsSnapshot> = {}): AgentsSnapshot {
  return {
    registry: 'Valstan/minigen-agents',
    registry_path: 'C:/agents/minigen-agents',
    registry_cloned: true,
    workspace: 'D:/valstan/REPO',
    templates: ['default', 'ideas'],
    agents: [
      {
        slug: 'ideas',
        title: 'Идеи',
        role: 'обработка зачиток владельца в заявки',
        repo: 'Valstan/ideas',
        area: 'p-ideas',
        skills: ['ideas-ingest'],
        requests: [{ path: 'requests/MiniGen/2026-11-24.md', project: 'MiniGen', size: 812 }]
      }
    ],
    ...over
  }
}

describe('куда уходят заявки агентов', () => {
  it('заявка агента «Идеи» ушла в раздел MiniGen', () => {
    const byAgent = requestSections(snapshot())
    expect(sectionsOf(byAgent, 'ideas')).toEqual(['MiniGen'])
  })

  it('несколько заявок — несколько разделов, без повторов', () => {
    const byAgent = requestSections(
      snapshot({
        agents: [
          {
            slug: 'ideas',
            requests: [
              { path: 'requests/MiniGen/2026-11-24.md', project: 'MiniGen', size: 1 },
              { path: 'requests/MiniGen/2026-11-25.md', project: 'MiniGen', size: 2 },
              { path: 'requests/biblio/2026-11-25.md', project: 'biblio', size: 3 }
            ]
          }
        ]
      })
    )
    expect(sectionsOf(byAgent, 'ideas')).toEqual(['MiniGen', 'biblio'])
  })

  it('агент без заявок — пустой список, а не выдуманный раздел', () => {
    const byAgent = requestSections(snapshot({ agents: [{ slug: 'sarafan' }] }))
    expect(sectionsOf(byAgent, 'sarafan')).toEqual([])
    expect(sectionsOf(byAgent, 'нет-такого-агента')).toEqual([])
  })

  it('пустой реестр — пустые разделы, а не ошибка', () => {
    expect(requestSections(snapshot({ agents: [] }))).toEqual({})
    expect(requestSections(null)).toEqual({})
    expect(sectionsInUse({})).toEqual([])
  })

  it('разделы, куда ушли заявки, — по алфавиту и без повторов', () => {
    const byAgent = requestSections(
      snapshot({
        agents: [
          {
            slug: 'ideas',
            requests: [{ path: 'requests/MiniGen/a.md', project: 'MiniGen', size: 1 }]
          },
          {
            slug: 'sarafan',
            requests: [{ path: 'requests/biblio/b.md', project: 'biblio', size: 2 }]
          }
        ]
      })
    )
    expect(sectionsInUse(byAgent)).toEqual(['MiniGen', 'biblio'])
  })
})

describe('вкладка «Агенты» показывает раздел заявок', () => {
  it('в ячейке заявок назван раздел', () => {
    const tab = readFileSync(join(src, 'renderer', 'src', 'tabs', 'AgentsTab.tsx'), 'utf8')
    expect(tab).toContain('раздел: ')
    expect(tab).toContain('requestCell')
  })

  it('в карточке канала сказано, куда ушли заявки', () => {
    const tab = readFileSync(join(src, 'renderer', 'src', 'tabs', 'AgentsTab.tsx'), 'utf8')
    expect(tab).toContain('Заявки агентов ушли в разделы')
  })
})
