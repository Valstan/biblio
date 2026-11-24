import { describe, expect, it } from 'vitest'
import type { Area } from '../src/shared/types'
import {
  AREA_COLUMNS,
  DEFAULT_AREA_SORT,
  nextSort,
  sortAreas,
  sortLabel
} from '../src/renderer/src/tabs/areas-sort'

/**
 * Сортировка списка областей по клику на шапку столбца (заказ владельца
 * 24.11): «Область · Работает · Вход · Очки · Режим · Сессии · Действия».
 *
 * По умолчанию — по статусу «Работает», работающие сверху.
 */

function area(over: Partial<Area> = {}): Area {
  return {
    slug: 'a',
    title: 'Область',
    folder: '',
    karman: '',
    autoheal: false,
    dir: 'p-a',
    running: false,
    exists: true,
    account: {},
    sessions: [],
    ...over
  } as Area
}

const areas: Area[] = [
  area({ slug: 'brain', title: 'Мозг', running: true, account: { state: 'есть вход' } }),
  area({ slug: 'sarafan', title: 'Сарафан', running: false, account: { state: 'нет входа' } }),
  area({ slug: 'karman', title: 'Карман', running: true, account: { state: 'есть вход' } })
]

describe('сортировка областей', () => {
  it('по умолчанию — по статусу «Работает», работающие сверху', () => {
    expect(DEFAULT_AREA_SORT).toEqual({ key: 'running', dir: 'desc' })
    expect(sortAreas(areas, DEFAULT_AREA_SORT).map((item) => item.slug)).toEqual([
      'brain',
      'karman',
      'sarafan'
    ])
  })

  it('клик по шапке — сортировка по этому столбцу', () => {
    const sort = nextSort(DEFAULT_AREA_SORT, 'area')
    expect(sort).toEqual({ key: 'area', dir: 'asc' })
    expect(sortAreas(areas, sort).map((item) => item.title)).toEqual([
      'Карман',
      'Мозг',
      'Сарафан'
    ])
  })

  it('повторный клик по той же шапке меняет направление', () => {
    const once = nextSort(DEFAULT_AREA_SORT, 'area')
    const twice = nextSort(once, 'area')
    expect(twice).toEqual({ key: 'area', dir: 'desc' })
    expect(sortAreas(areas, twice).map((item) => item.title)).toEqual([
      'Сарафан',
      'Мозг',
      'Карман'
    ])
  })

  it('по очкам — числами, а не строками', () => {
    const withPoints = [
      area({ slug: 'a', points: { points_today: 9 } }),
      area({ slug: 'b', points: { points_today: 100 } }),
      area({ slug: 'c', points: { points_today: 20 } })
    ]
    const sort = nextSort(DEFAULT_AREA_SORT, 'points')
    expect(sortAreas(withPoints, sort).map((item) => item.slug)).toEqual(['b', 'c', 'a'])
    const back = nextSort(sort, 'points')
    expect(sortAreas(withPoints, back).map((item) => item.slug)).toEqual(['a', 'c', 'b'])
  })

  it('области без очков уходят в конец в обоих направлениях', () => {
    const withPoints = [
      area({ slug: 'a', points: { points_today: 5 } }),
      area({ slug: 'empty' })
    ]
    expect(sortAreas(withPoints, { key: 'points', dir: 'desc' }).map((i) => i.slug)).toEqual([
      'a',
      'empty'
    ])
    expect(sortAreas(withPoints, { key: 'points', dir: 'asc' }).map((i) => i.slug)).toEqual([
      'a',
      'empty'
    ])
  })

  it('по входу — по состоянию входа', () => {
    const sort = nextSort(DEFAULT_AREA_SORT, 'account')
    expect(sortAreas(areas, sort).map((item) => item.slug)).toEqual([
      'brain',
      'karman',
      'sarafan'
    ])
  })

  it('по режиму — «Автомат» сверху', () => {
    const mixed = [
      area({ slug: 'ручной', autoheal: false }),
      area({ slug: 'автомат', autoheal: true })
    ]
    const sort = nextSort(DEFAULT_AREA_SORT, 'mode')
    expect(sortAreas(mixed, sort).map((item) => item.slug)).toEqual(['автомат', 'ручной'])
  })

  it('по сессиям — по их количеству', () => {
    const mixed = [
      area({ slug: 'одна', sessions: [{ session_id: '1' } as never] }),
      area({ slug: 'три', sessions: [1, 2, 3].map((n) => ({ session_id: String(n) } as never)) })
    ]
    const sort = nextSort(DEFAULT_AREA_SORT, 'sessions')
    expect(sortAreas(mixed, sort).map((item) => item.slug)).toEqual(['три', 'одна'])
  })

  it('исходный массив не трогаем', () => {
    const copy = [...areas]
    sortAreas(areas, { key: 'area', dir: 'asc' })
    expect(areas).toEqual(copy)
  })

  it('подпись шапки показывает столбец и направление', () => {
    expect(sortLabel(DEFAULT_AREA_SORT)).toBe('Работает ▼')
    expect(sortLabel({ key: 'area', dir: 'asc' })).toBe('Область ▲')
  })

  it('столбцы — все, кроме «Действия»', () => {
    expect(AREA_COLUMNS.map((item) => item.label)).toEqual([
      'Область',
      'Работает',
      'Вход',
      'Очки',
      'Режим',
      'Сессии'
    ])
  })
})
