import { describe, expect, it } from 'vitest'
import type { AgentsSnapshot, Area, KarmanChannel } from '../src/shared/types'
import {
  agentRows,
  applyConfirmText,
  channelView,
  createConfirmText,
  mergedRows,
  registrySummary,
  registryWarning,
  validateDraft
} from '../src/renderer/src/tabs/agents-view'
import { TABS } from '../src/renderer/src/tabs/tabs'

/**
 * Вкладка «Агенты» в окне «МиниГена» (заявка владельца от 24.11, раздел
 * КАРМАНА «MiniGen»).
 *
 * Проверяем то, что владелец увидит: вкладка **есть** в окне, агент
 * «Идеи» показан с репозиторием и областью, состояние канала КАРМАНА
 * честное (пустое состояние — «неизвестно», а не «жив»), а опасные
 * действия спрашивают подтверждение.
 */

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

function channel(over: Partial<KarmanChannel> = {}): KarmanChannel {
  return {
    has_url: true,
    has_secret: true,
    secret_source: 'vault',
    url_source: 'file',
    room_token_source: 'file',
    http: 200,
    ok: true,
    sections: ['MiniGen', 'biblio'],
    reason: 'канал живой, разделов: 2 (секрет из vault)',
    ...over
  }
}

describe('вкладка «Агенты»', () => {
  it('вкладка есть в окне', () => {
    expect(TABS.map((item) => item.label)).toContain('Агенты')
    expect(TABS.map((item) => item.id)).toContain('agents')
  })

  it('показывает агента «Идеи» с репозиторием и областью', () => {
    const rows = agentRows(snapshot())
    expect(rows).toHaveLength(1)
    expect(rows[0].title).toBe('Идеи')
    expect(rows[0].slug).toBe('ideas')
    expect(rows[0].repo).toBe('Valstan/ideas')
    // ⚠️ Имя области — **без** `p-`: каталог данных называется `p-ideas`,
    // а в списке — имя. Живой случай 28.11: манифест лежал с `area: p-ideas`,
    // и области `p-ideas` не существовало — агент и область не сошлись.
    expect(rows[0].area).toBe('ideas')
    expect(rows[0].role).toContain('зачиток')
    expect(rows[0].requests).toBe(1)
    expect(rows[0].missing).toEqual([])
  })

  it('неизвестное поле манифеста — «—», а не выдумка', () => {
    const rows = agentRows(
      snapshot({ agents: [{ slug: 'sarafan', repo: 'Valstan/sarafan' }] })
    )
    expect(rows[0].title).toBe('sarafan')
    expect(rows[0].role).toBe('—')
    expect(rows[0].area).toBe('sarafan')
    expect(rows[0].missing).toEqual(['название', 'роль', 'область'])
  })

  it('пустой реестр — пустой список, а не ошибка', () => {
    expect(agentRows(snapshot({ agents: [] }))).toEqual([])
    expect(agentRows(null)).toEqual([])
  })

  it('состояние канала: живой, не настроен и неизвестный — разные', () => {
    const alive = channelView(channel())
    expect(alive.tone).toBe('ok')
    expect(alive.label).toBe('канал живой')
    expect(alive.detail).toContain('vault')
    expect(alive.sections).toContain('MiniGen')

    const closed = channelView(
      channel({ ok: false, has_secret: false, http: 0, reason: 'секрет канала не задан' })
    )
    expect(closed.tone).toBe('bad')
    expect(closed.label).toBe('канал не настроен')
    expect(closed.detail).toContain('секрет')

    const unknown = channelView(null)
    expect(unknown.tone).toBe('bad')
    expect(unknown.label).toBe('канал не спросили')
  })

  it('реестр: сводка и предупреждение о расхождении с GitHub', () => {
    expect(registrySummary(snapshot())).toContain('Valstan/minigen-agents')
    expect(registrySummary(snapshot({ agents: [] }))).toContain('0 агентов')
    expect(registryWarning(snapshot())).toBe('')

    const dirty = snapshot({
      registry_state: {
        registry: 'Valstan/minigen-agents',
        path: 'C:/agents/minigen-agents',
        cloned: true,
        dirty: true,
        files: ['agents/ideas/agent.yaml'],
        why: 'локальная копия реестра отличается от GitHub'
      }
    })
    expect(registryWarning(dirty)).toContain('отличается от GitHub')
  })

  it('новый агент: проверка slug и обязательные поля', () => {
    expect(validateDraft({ slug: 'ideas', title: 'Идеи', role: 'зачитки' })).toEqual([])
    expect(validateDraft({ slug: '', title: 'Идеи', role: 'зачитки' })[0]).toContain('slug')
    expect(validateDraft({ slug: 'Идеи', title: 'Идеи', role: 'зачитки' })[0]).toContain('slug')
    expect(validateDraft({ slug: 'ideas', title: '', role: 'зачитки' })[0]).toContain('название')
    expect(validateDraft({ slug: 'ideas', title: 'Идеи', role: '' })[0]).toContain('роль')
  })

  it('опасные действия называют последствия и просят подтверждения', () => {
    const create = createConfirmText({ slug: 'ideas', title: 'Идеи', role: 'зачитки' })
    expect(create).toContain('Valstan/ideas')
    expect(create).toContain('репозиторий')
    expect(create).toContain('Продолжить?')

    const row = agentRows(snapshot())[0]
    const apply = applyConfirmText(row)
    expect(apply).toContain('Valstan/ideas')
    expect(apply).toContain('ideas')
    expect(apply).toContain('Продолжить?')
  })
})

/**
 * Объединённый список «Агенты» (заказ владельца 28.11: «Области» и
 * «Агенты» — одно и то же).
 *
 * Живой случай, который всё это чинит: в окне было две таблицы, и агент,
 * созданный в «Агентах», **не появлялся** в «Рабочих областях». Список
 * один — строка это область, а агент её содержание.
 */
function area(over: Partial<Area> = {}): Area {
  return {
    slug: 'ideas',
    title: 'Идеи',
    folder: 'D:/valstan/REPO/ideas',
    karman: 'ideas',
    autoheal: true,
    dir: 'C:/Users/valstan/.minimax-areas/p-ideas',
    running: true,
    exists: true,
    account: { state: 'есть вход' },
    sessions: [],
    ...over
  }
}

describe('объединённый список «Агенты»', () => {
  it('агент и его область — одна строка', () => {
    const rows = mergedRows([area()], snapshot())
    expect(rows).toHaveLength(1)
    expect(rows[0].area).toBe('ideas')
    expect(rows[0].agent?.title).toBe('Идеи')
    expect(rows[0].live?.slug).toBe('ideas')
    expect(rows[0].areaMissing).toBe(false)
    expect(rows[0].requests).toBe(1)
  })

  it('манифест со старым `p-ideas` сошёлся с областью `ideas`', () => {
    // Мост отдаёт имя области без префикса, но старый манифест может ещё
    // лежать с `p-ideas` — и тогда агент и область не сошлись бы.
    const rows = mergedRows([area()], snapshot())
    expect(rows[0].agent).not.toBeNull()
    expect(rows[0].areaMissing).toBe(false)
  })

  it('агент без области — строка с пометкой «область не заведена»', () => {
    const rows = mergedRows([], snapshot())
    expect(rows).toHaveLength(1)
    expect(rows[0].area).toBe('ideas')
    expect(rows[0].live).toBeNull()
    expect(rows[0].areaMissing).toBe(true)
    expect(rows[0].agent?.title).toBe('Идеи')
  })

  it('область без агента — тоже строка (обычная рабочая область)', () => {
    const rows = mergedRows([area({ slug: 'sarafan', title: 'Сарафан' })], snapshot())
    expect(rows).toHaveLength(2)
    const sarafan = rows.find((row) => row.area === 'sarafan')
    expect(sarafan?.agent).toBeNull()
    expect(sarafan?.live?.title).toBe('Сарафан')
    expect(sarafan?.areaMissing).toBe(false)
  })

  it('пустой реестр и пустые области — пустой список, а не ошибка', () => {
    expect(mergedRows([], snapshot({ agents: [] }))).toEqual([])
    expect(mergedRows([], null)).toEqual([])
  })
})
