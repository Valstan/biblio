import { describe, expect, it } from 'vitest'
import type { GeneMap } from '../src/shared/types'
import { describeEdge, layoutMap, selfSummary } from '../src/renderer/src/tabs/gene-map'

/**
 * Карта родословной (заказ владельца 24.11): вкладка «Гены» рисует паутину
 * копий и генов. Раскладка — чистая функция, и она проверяется здесь.
 */

function map(): GeneMap {
  return {
    self: {
      instance: 'mg-b',
      generation: 2,
      born_at: 5000,
      fork_commit: 'bbb2222',
      parent: { instance: 'mg-a', gene: 'gene-mg-a-002', commit: 'ccc3333' },
      repo: 'minigen-b',
      upstream: 'Valstan/biblio'
    },
    copies: [
      {
        instance: 'mg-a',
        generation: 1,
        born_at: 1000,
        fork_commit: 'aaa1111',
        parent: {},
        genes_made: ['gene-mg-a-001', 'gene-mg-a-002'],
        genes_taken: [],
        taken_by: ['mg-b'],
        repo: 'minigen-a',
        upstream: 'Valstan/biblio',
        is_self: false
      },
      {
        instance: 'mg-b',
        generation: 2,
        born_at: 5000,
        fork_commit: 'bbb2222',
        parent: { instance: 'mg-a', gene: 'gene-mg-a-002', commit: 'ccc3333' },
        genes_made: [],
        genes_taken: ['gene-mg-a-002'],
        taken_by: [],
        repo: 'minigen-b',
        upstream: 'Valstan/biblio',
        is_self: true
      }
    ],
    genes: [
      {
        id: 'gene-mg-a-001',
        author: 'mg-a',
        title: 'Старый ген',
        summary: '',
        base_commit: 'aaa1111',
        created_at: 2000,
        state: 'retracted',
        taken_by: [],
        replaced_by: 'gene-mg-a-002',
        why_retracted: 'заменён на gene-mg-a-002'
      },
      {
        id: 'gene-mg-a-002',
        author: 'mg-a',
        title: 'Раскатка чистит бандлы',
        summary: '',
        base_commit: 'ccc3333',
        created_at: 3000,
        state: 'adopted',
        taken_by: ['mg-b'],
        replaced_by: '',
        why_retracted: ''
      }
    ],
    edges: [
      { from: 'mg-a', to: 'gene-mg-a-002', kind: 'made' },
      { from: 'gene-mg-a-002', to: 'mg-b', kind: 'took' },
      { from: 'mg-a', to: 'mg-b', kind: 'born' },
      { from: 'gene-mg-a-001', to: 'gene-mg-a-002', kind: 'replaced' }
    ],
    stats: { copies: 2, genes: 2, links: 4, events: 6, made: 2, took: 1 },
    pool: 'C:/pool',
    pool_ready: true,
    note: 'карта'
  }
}

describe('карта родословной', () => {
  it('все копии и гены попали на карту', () => {
    const layout = layoutMap(map())
    const ids = layout.nodes.map((node) => node.id)
    expect(ids).toContain('mg-a')
    expect(ids).toContain('mg-b')
    expect(ids).toContain('gene-mg-a-001')
    expect(ids).toContain('gene-mg-a-002')
  })

  it('колонка — поколение: копия второго поколения правее первой', () => {
    const layout = layoutMap(map())
    const first = layout.nodes.find((node) => node.id === 'mg-a')
    const second = layout.nodes.find((node) => node.id === 'mg-b')
    expect(first && second).toBeTruthy()
    expect(second!.column).toBeGreaterThan(first!.column)
    expect(second!.x).toBeGreaterThan(first!.x)
  })

  it('своя копия помечена', () => {
    const layout = layoutMap(map())
    const myself = layout.nodes.find((node) => node.id === 'mg-b')
    expect(myself?.isSelf).toBe(true)
    expect(layout.nodes.find((node) => node.id === 'mg-a')?.isSelf).toBe(false)
  })

  it('связи на месте и с подписями', () => {
    const layout = layoutMap(map())
    expect(layout.links.length).toBe(4)
    const took = layout.links.find((link) => link.kind === 'took')
    expect(took?.label).toContain('взяла')
  })

  it('связь с неизвестным узлом не рисуется', () => {
    const broken = map()
    broken.edges.push({ from: 'нет-такого', to: 'mg-a', kind: 'made' })
    const layout = layoutMap(broken)
    expect(layout.links.length).toBe(4)
  })

  it('описание связи по-человечески', () => {
    expect(describeEdge({ from: 'mg-a', to: 'gene-mg-a-002', kind: 'made' }, map())).toContain(
      'создала'
    )
    expect(describeEdge({ from: 'gene-mg-a-002', to: 'mg-b', kind: 'took' }, map())).toContain(
      'взяла'
    )
    expect(describeEdge({ from: 'mg-a', to: 'mg-b', kind: 'born' }, map())).toContain('родилась')
  })

  it('сводка про себя — с числами', () => {
    const summary = selfSummary(map()).join(' | ')
    expect(summary).toContain('mg-b')
    expect(summary).toContain('создала генов: 0')
    expect(summary).toContain('взяла генов: 1')
    expect(summary).toContain('у неё взяли генов: 0')
  })
})
