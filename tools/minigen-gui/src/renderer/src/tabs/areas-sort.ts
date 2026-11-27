import type { Area } from '@shared/types'
import { DRIVER_VALUES, driverPosition } from './driver-view'

/**
 * Сортировка списка областей по клику на шапку столбца (заказ владельца
 * 24.11).
 *
 * ⚠️ ПРОМТ КАРМАНА #182 (зачитка владельца 27.11): колонки списка областей
 * перестроены — ручного режима в «МиниГене» больше нет:
 *
 * - колонка **«Режим» убрана целиком**: автомат аккаунтов всегда включён,
 *   переключателя («Автомат / Вручную») не осталось, сортировать по ней
 *   нечем;
 * - **«Водитель» → «Сессии»**: выбор водителя (кто водит сессиями) остался
 *   тем же — поменялся только заголовок на экране;
 * - **«Сессии» → «Состояние»**: там чипы состояний сессий (вердикт и
 *   заполнение) — имя по делу.
 *
 * ⚠️ Внутренние ключи (`driver`, `sessions`) остались прежними: переименуются
 * **заголовки на экране**, а не словари (промт #182, п. 7). В мосте, в отчёте
 * о лечении и в журнале водитель остаётся водителем.
 *
 * Правила:
 *
 * - **по умолчанию — по статусу «Работает»**, работающие сверху: владелец
 *   открывает список, чтобы увидеть, что живо, а что нет;
 * - клик по шапке — сортировка по этому столбцу, повторный клик — обратное
 *   направление;
 * - столбец «Действия» не сортируется (там кнопки);
 * - области без очков («—») уходят **в конец** в обоих направлениях: пустое
 *   значение не должно оказываться наверху и мешать видеть настоящие числа.
 *
 * Вынесено в отдельный модуль, чтобы правила проверялись тестом, а не
 * глазами (как карта генов и шапка окна).
 */

export type AreaSortKey = 'area' | 'running' | 'account' | 'points' | 'driver' | 'sessions'
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

export const AREA_COLUMNS: AreaColumn[] = [
  { key: 'area', label: 'Область', first: 'asc' },
  { key: 'running', label: 'Работает', first: 'desc' },
  { key: 'account', label: 'Вход', first: 'asc' },
  { key: 'points', label: 'Очки', first: 'desc' },
  // «Водитель» на экране стал «Сессиями» (промт #182) — по словарю это всё
  // тот же выбор водителя сессий, ключ остался прежним.
  { key: 'driver', label: 'Сессии', first: 'asc' },
  // Бывшие «Сессии» (чипы состояний) стали «Состоянием».
  { key: 'sessions', label: 'Состояние', first: 'desc' }
]

/** По умолчанию — «Работает», работающие сверху. */
export const DEFAULT_AREA_SORT: AreaSort = { key: 'running', dir: 'desc' }

/** Что делает клик по шапке: тот же столбец — обратное направление. */
export function nextSort(current: AreaSort, key: AreaSortKey): AreaSort {
  if (current.key === key) {
    return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
  }
  const column = AREA_COLUMNS.find((item) => item.key === key)
  return { key, dir: column ? column.first : 'asc' }
}

/** Подпись шапки: «Работает ▼» (пусто — столбец не активен). */
export function sortLabel(sort: AreaSort): string {
  const column = AREA_COLUMNS.find((item) => item.key === sort.key)
  const label = column ? column.label : sort.key
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

function compare(a: Area, b: Area, key: AreaSortKey): number {
  switch (key) {
    case 'area':
      return (
        text(a.title).localeCompare(text(b.title), 'ru') ||
        text(a.slug).localeCompare(text(b.slug), 'ru')
      )
    case 'running':
      return Number(a.running) - Number(b.running)
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

/** Отсортированный список областей (исходный массив не трогаем). */
export function sortAreas(areas: Area[], sort: AreaSort): Area[] {
  const sign = sort.dir === 'asc' ? 1 : -1
  return [...areas].sort((a, b) => {
    if (sort.key === 'points') {
      const left = pointsOf(a)
      const right = pointsOf(b)
      if (left === null || right === null) {
        // без очков — всегда в конце
        if (left === null && right === null) return 0
        return left === null ? 1 : -1
      }
      return sign * (left - right)
    }
    return sign * compare(a, b, sort.key)
  })
}
