import type { GeneMap, GeneMapCopy, GeneMapEdge, GeneMapGene } from '@shared/types'

/**
 * Раскладка карты родословной — граф, который рисует вкладка «Гены».
 *
 * ⚠️ Заказ владельца (24.11): «карта родословной, прямо в программе отдельная
 * вкладка "Гены", где будет рисоваться вся карта всех версий Минигена, которые
 * существуют в интернете, кто связан друг с другом по цепочке».
 *
 * Раскладка простая и честная: **колонка — поколение**. Слева корень (программа,
 * от которой форкнулись), правее — копии следующих поколений, между ними — гены
 * (наработки), которые эти копии создавали или брали. Никакой силы раскладки:
 * порядок внутри колонки — по времени, чтобы карта читалась сверху вниз.
 *
 * Раскладка вынесена в чистую функцию — её можно проверить тестом (окно
 * рисует то, что вернула она).
 */

export interface MapNode {
  id: string
  kind: 'copy' | 'gene' | 'commit'
  label: string
  title: string
  column: number
  row: number
  x: number
  y: number
  isSelf: boolean
  state: string
  generation: number
}

export interface MapLink {
  from: string
  to: string
  kind: string
  label: string
}

export interface MapLayout {
  nodes: MapNode[]
  links: MapLink[]
  width: number
  height: number
}

const COLUMN_WIDTH = 240
const ROW_HEIGHT = 96
const NODE_WIDTH = 190
const NODE_HEIGHT = 54

/** Человеческое описание связи — для подписи на линии и для списка. */
export function describeEdge(edge: GeneMapEdge, map: GeneMap): string {
  const copies = new Map<string, GeneMapCopy>(map.copies.map((item) => [item.instance, item]))
  const genes = new Map<string, GeneMapGene>(map.genes.map((item) => [item.id, item]))
  const name = (id: string): string => {
    if (copies.has(id)) return id
    if (genes.has(id)) return genes.get(id)?.title || id
    return id
  }
  switch (edge.kind) {
    case 'made':
      return `${name(edge.from)} создала ${name(edge.to)}`
    case 'took':
      return `${name(edge.from)} взяла у ${name(edge.to)}`
    case 'born':
      return `${name(edge.from)} родилась от ${name(edge.to)}`
    case 'forked':
      return `${name(edge.from)} — форк версии ${edge.to}`
    case 'replaced':
      return `${name(edge.from)} заменена на ${name(edge.to)}`
    default:
      return `${name(edge.from)} — ${edge.kind} — ${name(edge.to)}`
  }
}

/**
 * Разложить карту по колонкам-поколениям.
 *
 * - колонка 0 — корень: версия программы, от которой берут форк;
 * - копия стоит в колонке своего поколения;
 * - ген стоит в колонке копии-автора (чуть правее неё — так видно, что ген
 *   «вырос» из этой копии).
 */
export function layoutMap(map: GeneMap): MapLayout {
  const nodes: MapNode[] = []
  const columnRows = new Map<number, number>()

  const place = (column: number): number => {
    const row = columnRows.get(column) ?? 0
    columnRows.set(column, row + 1)
    return row
  }

  const copyOf = new Map<string, GeneMapCopy>(map.copies.map((item) => [item.instance, item]))
  const geneOf = new Map<string, GeneMapGene>(map.genes.map((item) => [item.id, item]))

  // 1) корень: версия, от которой форкнулась самая ранняя копия
  const rootCopies = map.copies.filter((item) => item.fork_commit)
  const rootCommit = rootCopies.length
    ? rootCopies.reduce((oldest, item) => (item.born_at && item.born_at < oldest.born_at ? item : oldest)).fork_commit
    : ''
  if (rootCommit) {
    const row = place(0)
    nodes.push({
      id: rootCommit,
      kind: 'commit',
      label: rootCommit,
      title: 'Программа-основа (от этой версии форки)',
      column: 0,
      row,
      x: 20,
      y: 20 + row * ROW_HEIGHT,
      isSelf: false,
      state: '',
      generation: 0
    })
  }

  // 2) копии — по колонкам поколения
  for (const copy of map.copies) {
    const column = Math.max(1, copy.generation || 1)
    const row = place(column)
    nodes.push({
      id: copy.instance,
      kind: 'copy',
      label: copy.instance,
      title: `${copy.instance} · поколение ${copy.generation} · ${
        copy.genes_made.length
      } создала, ${copy.genes_taken.length} взяла, у неё взяли ${copy.taken_by.length}`,
      column,
      row,
      x: 20 + column * COLUMN_WIDTH,
      y: 20 + row * ROW_HEIGHT,
      isSelf: copy.is_self,
      state: '',
      generation: copy.generation
    })
  }

  // 3) гены — рядом с копией-автором
  for (const gene of map.genes) {
    const author = geneOf.get(gene.id)?.author || gene.author
    const authorCopy = copyOf.get(author)
    const column = Math.max(1, authorCopy?.generation || 1)
    const row = place(column)
    nodes.push({
      id: gene.id,
      kind: 'gene',
      label: gene.id,
      title: `${gene.title || gene.id} — ${gene.state}`,
      column,
      row,
      x: 20 + column * COLUMN_WIDTH,
      y: 20 + row * ROW_HEIGHT,
      isSelf: false,
      state: gene.state,
      generation: authorCopy?.generation || 1
    })
  }

  // 4) связи — только между известными узлами
  const known = new Set(nodes.map((node) => node.id))
  const links: MapLink[] = []
  for (const edge of map.edges) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue
    links.push({
      from: edge.from,
      to: edge.to,
      kind: edge.kind,
      label: describeEdge(edge, map)
    })
  }

  const columns = columnRows.size
  const rows = Math.max(1, ...Array.from(columnRows.values()))
  return {
    nodes,
    links,
    width: 40 + columns * COLUMN_WIDTH,
    height: 40 + rows * ROW_HEIGHT
  }
}

/** Сводка «про себя» — одной строкой (карточка в окне). */
export function selfSummary(map: GeneMap): string[] {
  const copy = map.copies.find((item) => item.is_self)
  if (!copy) return ['копия не заведена']
  const out: string[] = [
    `копия: ${copy.instance}`,
    `поколение: ${copy.generation}`,
    `создала генов: ${copy.genes_made.length}`,
    `взяла генов: ${copy.genes_taken.length}`,
    `у неё взяли генов: ${copy.taken_by.length}`
  ]
  if (copy.fork_commit) out.push(`форк от версии: ${copy.fork_commit}`)
  if (copy.repo) out.push(`репозиторий копии: ${copy.repo}`)
  return out
}

/** Размер узла — чтобы линия обрывалась на границе прямоугольника. */
export const NODE_SIZE = { width: NODE_WIDTH, height: NODE_HEIGHT }
