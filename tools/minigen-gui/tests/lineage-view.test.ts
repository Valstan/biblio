import { describe, expect, it } from 'vitest'
import type { LineageInfo } from '../src/shared/types'
import { lineageRows, lineageShort } from '../src/renderer/src/tabs/lineage-view'

/**
 * По версии программы должно быть видно, чья это родня (заказ владельца
 * 24.11): кто копия, от какой версии форк, кто родитель по гену, сколько генов
 * принято.
 */

function lineage(): LineageInfo {
  return {
    instance: {
      instance_id: 'mg-693f8fdd',
      family: 'minigen',
      generation: 2,
      birth: 1795494651396,
      fork_of: { commit: '29729fa', installed_at: 1795494104 },
      parent: { instance: 'mg-693f8fdd', gene: 'gene-mg-693f8fdd-002', commit: 'cc788f9' },
      genes: [{ gene: 'gene-mg-693f8fdd-002', from: 'mg-693f8fdd', commit: 'cc788f9' }],
      copy_repo: 'minigen-693f8fdd',
      upstream: 'Valstan/biblio'
    },
    history: []
  }
}

describe('родословная копии', () => {
  it('показывает, кто копия и какого поколения', () => {
    const rows = lineageRows(lineage())
    const text = rows.map((row) => `${row.label}: ${row.value}`).join(' | ')
    expect(text).toContain('Копия: mg-693f8fdd')
    expect(text).toContain('Поколение: 2')
    expect(text).toContain('Семейство: minigen')
  })

  it('показывает точку рождения — форк от версии', () => {
    const rows = lineageRows(lineage())
    const fork = rows.find((row) => row.label === 'Форк от версии')
    expect(fork?.value).toContain('29729fa')
  })

  it('показывает родителя по гену и сколько генов принято', () => {
    const rows = lineageRows(lineage())
    const parent = rows.find((row) => row.label === 'Родитель по гену')
    expect(parent?.value).toContain('gene-mg-693f8fdd-002')
    const took = rows.find((row) => row.label === 'Генов принято')
    expect(took?.value).toBe('1')
  })

  it('показывает репозиторий копии и источник', () => {
    const rows = lineageRows(lineage())
    const text = rows.map((row) => row.value).join(' | ')
    expect(text).toContain('minigen-693f8fdd')
    expect(text).toContain('Valstan/biblio')
  })

  it('без родословной — пусто, а не ошибка', () => {
    expect(lineageRows(null)).toEqual([])
    expect(lineageShort(null)).toBe('')
  })

  it('короткая строка для версии', () => {
    expect(lineageShort(lineage())).toBe('mg-693f8fdd · поколение 2')
  })
})
