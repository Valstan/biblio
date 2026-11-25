import { describe, expect, it } from 'vitest'
import type { AgentsSnapshot, KarmanChannel } from '../src/shared/types'
import {
  agentRows,
  applyConfirmText,
  channelView,
  createConfirmText,
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
    expect(rows[0].area).toBe('p-ideas')
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
    expect(rows[0].area).toBe('p-sarafan')
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
    expect(apply).toContain('p-ideas')
    expect(apply).toContain('Продолжить?')
  })
})
