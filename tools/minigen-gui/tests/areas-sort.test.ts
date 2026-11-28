import { describe, expect, it } from 'vitest'
import type { Area } from '../src/shared/types'
import {
  ACTIVITY_LABEL,
  AREA_COLUMNS,
  DEFAULT_AREA_SORT,
  activityOf,
  nextSort,
  sortAreas,
  sortColumnsHint,
  sortLabel
} from '../src/renderer/src/tabs/areas-sort'

/**
 * Порядок списка областей (промт КАРМАНА #208).
 *
 * ⚠️ Промт КАРМАНА #208 (зачитка владельца 28.11): колонка **«Работает»
 * убрана целиком** — признак «работает» виден теперь по цвету всей строки.
 * Порядок — **два блока** (работающие сверху) и **активность** внутри блока.
 *
 * ⚠️ Промт КАРМАНА #182 (27.11): колонка «Режим» убрана, «Водитель» стал
 * «Сессиями», бывшие «Сессии» — «Состоянием».
 */

function area(over: Partial<Area> = {}): Area {
  return {
    slug: 'a',
    title: 'Область',
    folder: '',
    karman: '',
    autoheal: true,
    dir: 'p-a',
    running: false,
    exists: true,
    account: {},
    sessions: [],
    activity: { granted: 0, updated: 0, total: 0 },
    ...over
  } as Area
}

const areas: Area[] = [
  area({
    slug: 'brain',
    title: 'Мозг',
    running: true,
    account: { state: 'есть вход' },
    activity: { granted: 3, updated: 3, total: 6 }
  }),
  area({
    slug: 'sarafan',
    title: 'Сарафан',
    running: false,
    account: { state: 'нет входа' },
    activity: { granted: 1, updated: 1, total: 2 }
  }),
  area({
    slug: 'karman',
    title: 'Карман',
    running: true,
    account: { state: 'есть вход' },
    activity: { granted: 1, updated: 1, total: 2 }
  })
]

describe('сортировка областей', () => {
  it('колонки — ровно те, что на экране: «Работает» среди них нет', () => {
    // ⚠️ Промт КАРМАНА #208: колонка «Работает» убрана целиком — и заголовок,
    // и ячейка «да/нет». Значит, её нет и в списке столбцов.
    expect(AREA_COLUMNS.map((item) => item.label)).toEqual([
      'Область',
      'Вход',
      'Очки',
      'Сессии',
      'Состояние'
    ])
    expect(AREA_COLUMNS.map((item) => item.label)).not.toContain('Работает')
    expect(AREA_COLUMNS.some((item) => String(item.key) === 'running')).toBe(false)
  })

  it('подсказка сортировки перечисляет ровно те столбцы, что на экране', () => {
    expect(sortColumnsHint()).toBe(
      AREA_COLUMNS.map((item) => item.label).join(' · ')
    )
    expect(sortColumnsHint()).toBe('Область · Вход · Очки · Сессии · Состояние')
    // убранной колонки в подсказке тоже нет — иначе она перечисляла бы то,
    // чего на экране нет
    expect(sortColumnsHint()).not.toContain('Работает')
  })

  it('по умолчанию — по активности, самые активные выше', () => {
    expect(DEFAULT_AREA_SORT).toEqual({ key: 'activity', dir: 'desc' })
    expect(sortLabel(DEFAULT_AREA_SORT)).toBe(`${ACTIVITY_LABEL} ▼`)
    expect(sortAreas(areas, DEFAULT_AREA_SORT).map((item) => item.slug)).toEqual([
      'brain',
      'karman',
      'sarafan'
    ])
  })

  it('два блока: работающие выше, независимо от активности', () => {
    // «Карман» не запущен, но активен; «Мозг» запущен и активен. Работающие
    // всё равно выше.
    const mixed = [
      area({ slug: 'не-работает-но-активен', running: false, activity: { granted: 9, updated: 9, total: 18 } }),
      area({ slug: 'работает-но-тише', running: true, activity: { granted: 1, updated: 1, total: 2 } })
    ]
    expect(sortAreas(mixed, DEFAULT_AREA_SORT).map((item) => item.slug)).toEqual([
      'работает-но-тише',
      'не-работает-но-активен'
    ])
  })

  it('блок «работающие сверху» не теряется от клика по шапке', () => {
    const mixed = [
      area({ slug: 'тихий-не-работает', running: false, activity: { granted: 0, updated: 0, total: 0 } }),
      area({ slug: 'громкий-работает', running: true, activity: { granted: 5, updated: 5, total: 10 } })
    ]
    // кликнули «Область» — по названию; но блок остался: работающий выше
    const byName = nextSort(DEFAULT_AREA_SORT, 'area')
    expect(sortAreas(mixed, byName).map((item) => item.slug)).toEqual([
      'громкий-работает',
      'тихий-не-работает'
    ])
    // и по очкам — тоже
    const byPoints = nextSort(DEFAULT_AREA_SORT, 'points')
    expect(sortAreas(mixed, byPoints).map((item) => item.slug)).toEqual([
      'громкий-работает',
      'тихий-не-работает'
    ])
  })

  it('внутри блока — по активности: два агента с разной частотой', () => {
    const mixed = [
      area({ slug: 'редкий', running: true, activity: { granted: 1, updated: 0, total: 1 } }),
      area({ slug: 'частый', running: true, activity: { granted: 7, updated: 4, total: 11 } })
    ]
    expect(sortAreas(mixed, DEFAULT_AREA_SORT).map((item) => item.slug)).toEqual([
      'частый',
      'редкий'
    ])
  })

  it('счётчик активности виден в состоянии и считается как сумма', () => {
    expect(activityOf(area({ activity: { granted: 2, updated: 3, total: 5 } }))).toBe(5)
    // без счётчика — ноль, а не «нет данных»
    expect(activityOf(area())).toBe(0)
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
    // ⚠️ Направление меняется **внутри блоков**: работающие остаются выше
    // (Мозг, Карман), и только внутри своего блока порядок по названию
    // переворачивается. Блок «работающие сверху» не переворачивается.
    expect(sortAreas(areas, twice).map((item) => item.title)).toEqual([
      'Мозг',
      'Карман',
      'Сарафан'
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

  it('по «Состоянию» — по числу сессий', () => {
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
    expect(sortLabel({ key: 'area', dir: 'asc' })).toBe('Область ▲')
    // переименованные колонки (#182)
    expect(sortLabel({ key: 'driver', dir: 'asc' })).toBe('Сессии ▲')
    expect(sortLabel({ key: 'sessions', dir: 'desc' })).toBe('Состояние ▼')
  })

  it('сортировки по «Режиму» больше нет', () => {
    const labels = AREA_COLUMNS.map((item) => item.label)
    expect(labels).not.toContain('Режим')
    // `String(...)` — проверка остаётся рабочей и после того, как ключ «mode»
    // исчез из типа `AreaSortKey`.
    expect(AREA_COLUMNS.some((item) => String(item.key) === 'mode')).toBe(false)
  })

  it('«Сессии» (бывшая «Водитель») стоит перед «Состоянием»', () => {
    const labels = AREA_COLUMNS.map((item) => item.label)
    expect(labels.indexOf('Сессии')).toBe(labels.indexOf('Очки') + 1)
    expect(labels.indexOf('Сессии')).toBeLessThan(labels.indexOf('Состояние'))
  })

  it('по водителю (колонка «Сессии») — в порядке самого переключателя', () => {
    // ⚠️ Заказ владельца 27.11: положений переключателя **два** — «Миниген» и
    // «Оператор», а «Внешнее управление» и «Оператор» — одно и то же положение
    // (управление внешнее; оператор или другой агент — без разницы). Значит
    // «внешний» и «оператор» стоят рядом, а первым идёт «Миниген».
    const mixed = [
      area({ slug: 'оператор', driver: 'none' }),
      area({ slug: 'внешний', driver: 'manager' }),
      area({ slug: 'миниген', driver: 'automaton' })
    ]
    const sort = nextSort(DEFAULT_AREA_SORT, 'driver')
    expect(sort).toEqual({ key: 'driver', dir: 'asc' })
    expect(sortAreas(mixed, sort).map((item) => item.slug)).toEqual([
      'миниген',
      'оператор',
      'внешний'
    ])
    const back = nextSort(sort, 'driver')
    // «оператор» и «внешний» — одно положение ⇒ их порядок между собой не
    // меняется (сортировка устойчива): остаётся как во входном списке.
    expect(sortAreas(mixed, back).map((item) => item.slug)).toEqual([
      'оператор',
      'внешний',
      'миниген'
    ])
  })

  it('по водителю смотрим на того, кто водит фактически (правило ядра)', () => {
    const mixed = [
      area({
        slug: 'по-правилу-внешний',
        driver: '',
        driverNow: {
          driver: 'manager',
          title: 'Внешнее управление',
          why: 'по правилу: в области работает сессия Управляющего',
          source: 'правило'
        }
      }),
      area({ slug: 'явно-оператор', driver: 'none' })
    ]
    const sort = nextSort(DEFAULT_AREA_SORT, 'driver')
    expect(sortAreas(mixed, sort).map((item) => item.slug)).toEqual([
      'по-правилу-внешний',
      'явно-оператор'
    ])
  })
})
