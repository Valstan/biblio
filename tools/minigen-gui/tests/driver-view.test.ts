import { describe, expect, it } from 'vitest'
import type { Area } from '../src/shared/types'
import {
  DRIVER_HINTS,
  DRIVER_LABELS,
  DRIVER_VALUES,
  driverHint,
  driverLabel,
  driverValue
} from '../src/renderer/src/tabs/driver-view'

/**
 * Переключатель «водитель проекта» (промт КАРМАНА #174).
 *
 * В каждый момент у проекта один водитель сессий: либо «Управляющий», либо
 * автомат управления сессиями, либо никто. Значение «по правилу» — то самое
 * правило по умолчанию: если в области работает Управляющий, водит он
 * (автомат только наблюдает), иначе — автомат.
 */

function area(over: Partial<Area> = {}): Area {
  return {
    slug: 'a',
    title: 'Область',
    folder: '',
    karman: '',
    autoheal: true,
    dir: 'p-a',
    running: true,
    exists: true,
    account: {},
    sessions: [],
    ...over
  } as Area
}

describe('переключатель «водитель проекта»', () => {
  it('значения — как в ядре', () => {
    expect([...DRIVER_VALUES]).toEqual(['auto', 'manager', 'automaton', 'none'])
    expect(DRIVER_LABELS.manager).toBe('Управляющий')
    expect(DRIVER_LABELS.automaton).toBe('автомат')
    expect(DRIVER_LABELS.none).toBe('никто')
    expect(DRIVER_LABELS.auto).toBe('по правилу')
  })

  it('пустое значение — «по правилу»', () => {
    expect(driverValue(area())).toBe('auto')
    expect(driverValue(area({ driver: '' }))).toBe('auto')
    expect(driverLabel(area({ driver: '' }))).toBe('по правилу')
  })

  it('незнакомое значение — «по правилу», а не выдумка', () => {
    expect(driverValue(area({ driver: 'кто-то' }))).toBe('auto')
  })

  it('выбранное значение читается как есть', () => {
    expect(driverValue(area({ driver: 'manager' }))).toBe('manager')
    expect(driverValue(area({ driver: 'none' }))).toBe('none')
    expect(driverLabel(area({ driver: 'manager' }))).toBe('Управляющий')
  })

  it('подсказка говорит и что значит значение, и кто водит сейчас', () => {
    const hint = driverHint(
      area({
        driver: 'manager',
        driverNow: {
          driver: 'manager',
          title: 'Управляющий',
          why: 'переключатель проекта: Управляющий',
          source: 'переключатель'
        }
      })
    )
    expect(hint).toContain(DRIVER_HINTS.manager)
    expect(hint).toContain('Управляющий')
    expect(hint).toContain('переключатель проекта')
  })

  it('без водителя подсказка не выдумывает', () => {
    const hint = driverHint(area({ driver: 'auto' }))
    expect(hint).toContain(DRIVER_HINTS.auto)
    expect(hint).not.toContain('Сейчас водит')
  })
})
