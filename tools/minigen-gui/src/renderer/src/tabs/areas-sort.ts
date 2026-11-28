import type { Area } from '@shared/types'
import { DRIVER_VALUES, driverPosition } from './driver-view'

/**
 * Порядок списка областей (промт КАРМАНА #208).
 *
 * ⚠️ ПРОМТ КАРМАНА #208 (зачитка владельца 28.11): колонка **«Работает»
 * убрана целиком** — и заголовок, и ячейка «да/нет». Признак «работает» виден
 * теперь **по цвету всей строки**, а порядок задан **двумя блоками**:
 *
 * 1. **работающие агенты — сверху**, незапущенные — ниже. Это правило
 *    **встроено** в сортировку и не зависит от того, на какую шапку кликнул
 *    владелец: ни один незапущенный агент не окажется над работающим, сколько
 *    бы активен он ни был;
 * 2. **внутри блока — по активности**: агент, который чаще получает аккаунты
 *    и обновления аккаунтов из Кармана, выше. Активность приходит из моста
 *    (`area.activity`) — её считает автомат аккаунтов по журналу, а не
 *    человек на глаз.
 *
 * Клик по шапке по-прежнему сортирует **внутри** блоков (см. `sortAreas`).
 *
 * ⚠️ ПРОМТ КАРМАНА #182 (зачитка владельца 27.11): колонка «Режим» убрана
 * целиком, «Водитель» стал «Сессиями», бывшие «Сессии» — «Состоянием».
 * Внутренние ключи (`driver`, `sessions`) остались прежними: переименуются
 * **заголовки на экране**, а не словари.
 *
 * Правила:
 *
 * - **по умолчанию — по активности** (самые активные выше в своём блоке);
 * - клик по шапке — сортировка по этому столбцу, повторный клик — обратное
 *   направление;
 * - столбец «Действия» не сортируется (там кнопки);
 * - области без очков («—») уходят **в конец** в обоих направлениях: пустое
 *   значение не должно оказываться наверху и мешать видеть настоящие числа;
 * - равные значения по выбранному столбцу разводятся **по активности**
 *   (самые активные выше) — порядок внутри блока всегда отражает реальную
 *   активность.
 *
 * Вынесено в отдельный модуль, чтобы правила проверялись тестом, а не
 * глазами (как карта генов и шапка окна).
 */

export type AreaSortKey = 'area' | 'activity' | 'account' | 'points' | 'driver' | 'sessions'
export type AreaSortDir = 'asc' | 'desc'

export interface AreaSort {
  key: AreaSortKey
  dir: AreaSortDir
}

export interface AreaColumn {
  key: AreaSortKey
  label: string
  /** С какого направления начинают этот столбец. */
  first: AreaSortDir
}

/**
 * Столбцы таблицы — **ровно те, что на экране**.
 *
 * ⚠️ Колонки «Режим» (промт #182) и «Работает» (промт #208) убраны целиком.
 * Подсказка сортировки собирается из этого же списка — значит, она всегда
 * совпадает с экраном.
 */
export const AREA_COLUMNS: AreaColumn[] = [
  { key: 'area', label: 'Область', first: 'asc' },
  { key: 'account', label: 'Вход', first: 'asc' },
  { key: 'points', label: 'Очки', first: 'desc' },
  // «Водитель» на экране стал «Сессиями» (промт #182) — по словарю это всё
  // тот же выбор водителя сессий, ключ остался прежним.
  { key: 'driver', label: 'Сессии', first: 'asc' },
  // Бывшие «Сессии» (чипы состояний) стали «Состоянием».
  { key: 'sessions', label: 'Состояние', first: 'desc' }
]

/** Подпись активности — её нет на экране как столбца, но она видна в строке. */
export const ACTIVITY_LABEL = 'Активность'

/**
 * По умолчанию — **по активности** (промт КАРМАНА #208): работающие сверху,
 * а внутри блока — самые активные.
 *
 * ⚠️ Ключа `running` больше нет: колонки «Работает» нет, и сортировать по
 * ней нечем. Два блока (работающие сверху) задаёт сам `sortAreas`.
 */
export const DEFAULT_AREA_SORT: AreaSort = { key: 'activity', dir: 'desc' }

/** Что делает клик по шапке: тот же столбец — обратное направление. */
export function nextSort(current: AreaSort, key: AreaSortKey): AreaSort {
  if (current.key === key) {
    return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
  }
  const column = AREA_COLUMNS.find((item) => item.key === key)
  return { key, dir: column ? column.first : 'asc' }
}

/** Подпись шапки: «Вход ▼» (пусто — столбец не активен). */
export function sortLabel(sort: AreaSort): string {
  const column = AREA_COLUMNS.find((item) => item.key === sort.key)
  const label = column ? column.label : sort.key === 'activity' ? ACTIVITY_LABEL : sort.key
  return `${label} ${sort.dir === 'asc' ? '▲' : '▼'}`
}

/**
 * Список колонок для подсказки сортировки — **ровно то, что на экране**.
 *
 * Промт #182: «список колонок в подсказке совпадает с тем, что на экране» —
 * поэтому подсказка собирается из того же `AREA_COLUMNS`, а не из отдельного
 * списка (который однажды разошёлся бы с шапкой).
 */
export function sortColumnsHint(): string {
  return AREA_COLUMNS.map((column) => column.label).join(' · ')
}

function text(value: unknown): string {
  return String(value ?? '').trim().toLowerCase()
}

function pointsOf(area: Area): number | null {
  const value = area.points?.points_today
  return typeof value === 'number' ? value : null
}

/**
 * Активность области — сколько раз она получала аккаунты и обновления
 * аккаунтов из Кармана. Значение приходит из моста (`area.activity`); если
 * мост его не прислал — считаем нулём (а не «нет данных»).
 */
export function activityOf(area: Area): number {
  const total = area.activity?.total
  return typeof total === 'number' ? total : 0
}

/**
 * Ранг водителя для сортировки — в порядке самого переключателя: «Миниген»,
 * «Оператор». Кто водит на самом деле (по правилу ядра) — смотрим и на это:
 * сортировка должна отвечать на вопрос «кто сейчас водит», а не «что записано
 * в конфиге».
 *
 * ⚠️ «Внешнее управление» и «Оператор» — одно положение (управление внешнее),
 * ⇒ ранга у них общий.
 */
function driverRank(area: Area): number {
  const position = driverPosition(area)
  const index = (DRIVER_VALUES as readonly string[]).indexOf(position)
  return index < 0 ? DRIVER_VALUES.length : index
}

/** Сравнение по одному столбцу (без направления: «больше — после»). */
function compare(a: Area, b: Area, key: AreaSortKey): number {
  switch (key) {
    case 'area':
      return (
        text(a.title).localeCompare(text(b.title), 'ru') ||
        text(a.slug).localeCompare(text(b.slug), 'ru')
      )
    case 'activity':
      return activityOf(a) - activityOf(b)
    case 'account':
      return (
        text(a.account?.state).localeCompare(text(b.account?.state), 'ru') ||
        text(a.account?.email).localeCompare(text(b.account?.email), 'ru')
      )
    case 'driver':
      return driverRank(a) - driverRank(b)
    case 'sessions':
      return a.sessions.length - b.sessions.length
    default:
      return 0
  }
}

/** Сравнение по выбранному столбцу — с учётом направления и «пусто в конец». */
function compareBy(areas: Area, other: Area, key: AreaSortKey, sign: number): number {
  if (key === 'points') {
    const left = pointsOf(areas)
    const right = pointsOf(other)
    if (left === null || right === null) {
      // без очков — всегда в конце
      if (left === null && right === null) return 0
      return left === null ? 1 : -1
    }
    return sign * (left - right)
  }
  return sign * compare(areas, other, key)
}

/**
 * Отсортированный список областей (исходный массив не трогаем).
 *
 * Порядок (промт КАРМАНА #208):
 *
 * 1. **два блока**: работающие сверху, незапущенные ниже — **всегда**, что бы
 *    владелец ни выбрал в шапке;
 * 2. внутри блока — выбранный столбец (по умолчанию — активность);
 * 3. равные значения — по активности (самые активные выше);
 * 4. дальше — порядок исходного списка (сортировка устойчива).
 */
export function sortAreas(areas: Area[], sort: AreaSort): Area[] {
  const sign = sort.dir === 'asc' ? 1 : -1
  const indexed = areas.map((area, index) => ({ area, index }))
  indexed.sort((x, y) => {
    // 1) блоки: работающие ВЫШЕ — всегда
    const blockX = x.area.running ? 0 : 1
    const blockY = y.area.running ? 0 : 1
    if (blockX !== blockY) return blockX - blockY
    // 2) внутри блока — выбранный столбец
    const cmp = compareBy(x.area, y.area, sort.key, sign)
    if (cmp !== 0) return cmp
    // 3) равные значения — по активности: самые активные выше
    const act = activityOf(y.area) - activityOf(x.area)
    if (act !== 0) return act
    // 4) порядок исходного списка (устойчиво)
    return x.index - y.index
  })
  return indexed.map((item) => item.area)
}
