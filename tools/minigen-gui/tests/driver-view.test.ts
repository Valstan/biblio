import { describe, expect, it } from 'vitest'
import {
  CORE_DRIVER_VALUES,
  DRIVER_BUTTONS,
  DRIVER_HINTS,
  DRIVER_LABELS,
  DRIVER_RULE,
  DRIVER_VALUES,
  activeDriverButton,
  driverExplicit,
  driverHint,
  driverLabel,
  driverPosition,
  driverValue
} from '../src/renderer/src/tabs/driver-view'

/**
 * Переключатель «водитель проекта» (промт КАРМАНА #174; слова и состав —
 * #177; **два** положения — заказ владельца 27.11).
 *
 * В каждый момент у проекта один водитель сессий. Положений переключателя
 * **два**: «Миниген» и «Оператор». «Внешнее управление» и «Оператор» — одно и
 * то же (управление внешнее; оператор или другой агент — без разницы).
 * «По правилу» — не пункт переключателя, а правило ядра для области, где
 * водитель ещё не выбран явно.
 */

function area(over: Record<string, unknown> = {}) {
  return over as Parameters<typeof driverValue>[0]
}

describe('переключатель «водитель проекта»', () => {
  it('положений ровно два — и это слова владельца, в его порядке', () => {
    expect([...DRIVER_VALUES]).toEqual(['automaton', 'none'])
    expect(DRIVER_LABELS.automaton).toBe('Миниген')
    expect(DRIVER_LABELS.none).toBe('Оператор')
    expect(DRIVER_BUTTONS.map((b) => b.label)).toEqual(['Миниген', 'Оператор'])
  })

  it('кнопки пишут те значения, которые понимает ядро', () => {
    for (const button of DRIVER_BUTTONS) {
      expect(CORE_DRIVER_VALUES as readonly string[]).toContain(button.value)
    }
  })

  it('«по правилу» — не пункт переключателя', () => {
    expect([...DRIVER_VALUES]).not.toContain(DRIVER_RULE)
    expect(DRIVER_RULE).toBe('auto')
    expect(Object.keys(DRIVER_LABELS)).not.toContain(DRIVER_RULE)
  })

  it('у каждого положения подсказка: кто водит и что делает', () => {
    expect(DRIVER_HINTS.automaton).toContain('нейросеть')
    expect(DRIVER_HINTS.automaton).toContain('архивирует')
    expect(DRIVER_HINTS.none).toContain('внешн')
    for (const value of DRIVER_VALUES) {
      expect(DRIVER_HINTS[value].length).toBeGreaterThan(30)
    }
  })

  it('«внешнее управление» и «Оператор» — одно положение', () => {
    expect(driverPosition(area({ driver: 'manager' }))).toBe('none')
    expect(driverPosition(area({ driver: 'none' }))).toBe('none')
    expect(driverLabel(area({ driver: 'manager' }))).toBe('Оператор')
    expect(driverLabel(area({ driver: 'none' }))).toBe('Оператор')
    expect(activeDriverButton(area({ driver: 'manager' })).label).toBe('Оператор')
  })

  it('выбранное значение читается как есть', () => {
    expect(driverValue(area({ driver: 'manager' }))).toBe('manager')
    expect(driverValue(area({ driver: 'none' }))).toBe('none')
    expect(driverValue(area({ driver: 'automaton' }))).toBe('automaton')
    expect(driverPosition(area({ driver: 'automaton' }))).toBe('automaton')
    expect(driverLabel(area({ driver: 'automaton' }))).toBe('Миниген')
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
    expect(driverPosition(byRule)).toBe('none')
    expect(driverLabel(byRule)).toBe('Оператор')
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
    expect(driverPosition(unknown)).toBe('')
    expect(driverLabel(unknown)).toBe('—')
    expect(driverHint(unknown)).toContain('ядро без правила водителя')
  })

  it('подсказка говорит и что значит значение, и кто водит сейчас', () => {
    const hint = driverHint(
      area({
        driver: 'none',
        driverNow: {
          driver: 'none',
          title: 'Оператор',
          why: 'переключатель проекта: Оператор',
          source: 'переключатель'
        }
      })
    )
    expect(hint).toContain(DRIVER_HINTS.none)
    expect(hint).toContain('Оператор')
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
