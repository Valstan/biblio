import { describe, expect, it } from 'vitest'
import {
  DRIVER_HINTS,
  DRIVER_LABELS,
  DRIVER_RULE,
  DRIVER_VALUES,
  driverExplicit,
  driverHint,
  driverLabel,
  driverValue
} from '../src/renderer/src/tabs/driver-view'

/**
 * Переключатель «водитель проекта» (промт КАРМАНА #174; слова и состав —
 * #177).
 *
 * В каждый момент у проекта один водитель сессий. В переключателе **ровно
 * три** варианта: «Внешнее управление», «Управление МиниГеном», «Оператор».
 * «По правилу» — не пункт меню, а правило ядра для области, где водитель
 * ещё не выбран явно.
 */

function area(over: Record<string, unknown> = {}) {
  return over as Parameters<typeof driverValue>[0]
}

describe('переключатель «водитель проекта»', () => {
  it('вариантов ровно три — и это слова владельца', () => {
    expect([...DRIVER_VALUES]).toEqual(['manager', 'automaton', 'none'])
    expect(DRIVER_LABELS.manager).toBe('Внешнее управление')
    expect(DRIVER_LABELS.automaton).toBe('Управление МиниГеном')
    expect(DRIVER_LABELS.none).toBe('Оператор')
  })

  it('«по правилу» — не пункт переключателя', () => {
    expect([...DRIVER_VALUES]).not.toContain(DRIVER_RULE)
    expect(DRIVER_RULE).toBe('auto')
    expect(Object.keys(DRIVER_LABELS)).not.toContain(DRIVER_RULE)
  })

  it('у каждого варианта подсказка: кто водит и что делает', () => {
    expect(DRIVER_HINTS.manager).toContain('внешний агент')
    expect(DRIVER_HINTS.manager).toContain('аккаунты')
    expect(DRIVER_HINTS.automaton).toContain('нейросеть')
    expect(DRIVER_HINTS.automaton).toContain('архивирует')
    expect(DRIVER_HINTS.none).toContain('оператор')
    for (const value of DRIVER_VALUES) {
      expect(DRIVER_HINTS[value].length).toBeGreaterThan(30)
    }
  })

  it('выбранное значение читается как есть', () => {
    expect(driverValue(area({ driver: 'manager' }))).toBe('manager')
    expect(driverValue(area({ driver: 'none' }))).toBe('none')
    expect(driverValue(area({ driver: 'automaton' }))).toBe('automaton')
    expect(driverLabel(area({ driver: 'manager' }))).toBe('Внешнее управление')
  })

  it('выбор явный — по нему и водит', () => {
    expect(driverExplicit(area({ driver: 'manager' }))).toBe(true)
    expect(driverExplicit(area({ driver: '' }))).toBe(false)
    expect(driverExplicit(area({ driver: DRIVER_RULE }))).toBe(false)
  })

  it('без явного выбора водителя определяет ядро (правило)', () => {
    const byRule = area({
      driver: '',
      driverNow: {
        driver: 'manager',
        title: 'Внешнее управление',
        why: 'по правилу: в области работает сессия Управляющего (abc123)',
        source: 'правило'
      }
    })
    expect(driverValue(byRule)).toBe('manager')
    expect(driverLabel(byRule)).toBe('Внешнее управление')
    expect(driverExplicit(byRule)).toBe(false)
  })

  it('ядро без правила водителя — честное «—», а не выдумка', () => {
    const unknown = area({
      driver: '',
      driverNow: {
        driver: '',
        title: '—',
        why: 'ядро без правила водителя (обновите демоны)',
        source: ''
      }
    })
    expect(driverValue(unknown)).toBe('')
    expect(driverLabel(unknown)).toBe('—')
    expect(driverHint(unknown)).toContain('ядро без правила водителя')
  })

  it('подсказка говорит и что значит значение, и кто водит сейчас', () => {
    const hint = driverHint(
      area({
        driver: 'manager',
        driverNow: {
          driver: 'manager',
          title: 'Внешнее управление',
          why: 'переключатель проекта: Внешнее управление',
          source: 'переключатель'
        }
      })
    )
    expect(hint).toContain(DRIVER_HINTS.manager)
    expect(hint).toContain('Внешнее управление')
    expect(hint).toContain('переключатель проекта')
  })

  it('правило видно в подсказке отдельно', () => {
    const hint = driverHint(
      area({
        driver: 'auto',
        driverNow: {
          driver: 'automaton',
          title: 'Управление МиниГеном',
          why: 'по правилу: сессии Управляющего в области нет',
          source: 'правило'
        }
      })
    )
    expect(hint).toContain('по правилу')
    expect(hint).toContain('Управление МиниГеном')
  })

  it('без водителя подсказка не выдумывает', () => {
    const hint = driverHint(area({ driver: 'automaton' }))
    expect(hint).toContain(DRIVER_HINTS.automaton)
    expect(hint).not.toContain('Сейчас водит')
  })
})
