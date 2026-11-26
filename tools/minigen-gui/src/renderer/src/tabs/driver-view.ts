import type { Area } from '@shared/types'

/**
 * Кто водит сессиями проекта (промт КАРМАНА #174) — словами для окна.
 *
 * Правило «один водитель на проект»: в каждый момент у проекта один водитель
 * сессий — либо «Управляющий» (карусель или сам владелец), либо автомат
 * управления сессиями, либо никто. Переключатель — в настройках программы
 * (по области/проекту); правило по умолчанию: если в области работает
 * Управляющий, водит он, а автомат только наблюдает.
 *
 * Вынесено в отдельный модуль, чтобы правила проверялись тестом, а не
 * глазами (как сортировка областей и карта генов).
 */

/** Значения переключателя — как в ядре (`minigen_core.DRIVERS`). */
export const DRIVER_VALUES = ['auto', 'manager', 'automaton', 'none'] as const

export type DriverValue = (typeof DRIVER_VALUES)[number]

/** Человеческое имя значения переключателя. */
export const DRIVER_LABELS: Record<DriverValue, string> = {
  auto: 'по правилу',
  manager: 'Управляющий',
  automaton: 'автомат',
  none: 'никто'
}

/** Что значит каждое значение — словами (подсказка в окне). */
export const DRIVER_HINTS: Record<DriverValue, string> = {
  auto: 'Если в области работает Управляющий — водит он (автомат только наблюдает), иначе — автомат',
  manager: 'Автомат в этой области не действует, но пишет в отчёт, кто водитель и почему',
  automaton: 'Автомат управления сессиями продлевает, завершает и поднимает новые сессии',
  none: 'Никто не вмешивается в сессии: автомат только наблюдает'
}

/** Значение переключателя области (пустое — «по правилу»). */
export function driverValue(area: Pick<Area, 'driver'>): DriverValue {
  const raw = String(area.driver ?? '').trim().toLowerCase()
  return (DRIVER_VALUES as readonly string[]).includes(raw)
    ? (raw as DriverValue)
    : 'auto'
}

/** Подпись значения переключателя. */
export function driverLabel(area: Pick<Area, 'driver'>): string {
  return DRIVER_LABELS[driverValue(area)]
}

/**
 * Подсказка к переключателю: что значит выбранное значение **и** кто водит
 * сессиями сейчас (и почему).
 */
export function driverHint(area: Pick<Area, 'driver' | 'driverNow'>): string {
  const base = DRIVER_HINTS[driverValue(area)]
  const now = area.driverNow
  if (!now?.title) return base
  const why = now.why ? ` — ${now.why}` : ''
  return `${base}. Сейчас водит: ${now.title}${why}`
}
