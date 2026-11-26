import { describe, expect, it } from 'vitest'
import { mergeSeen, readSeen, writeSeen } from '../src/renderer/src/tabs/works-seen'

/**
 * Память окна: какие наработки других копий копия уже видела.
 *
 * ⚠️ Проверяем то, от чего зависит честность уведомления: показывать одно и
 * то же второй раз — шум, а шум владелец не читает. И — не сломать окно, если
 * хранилище недоступно (тесты идут в node, где localStorage нет).
 */

describe('память виденных наработок', () => {
  it('новые дописываются, повторы не добавляются', () => {
    const merged = mergeSeen(['g-1'], ['g-2', 'g-1'])
    expect(merged).toEqual(['g-1', 'g-2'])
  })

  it('пустые и лишние пробелы не становятся записями', () => {
    expect(mergeSeen([], ['  ', ''])).toEqual([])
    expect(mergeSeen([], [' g-1 '])).toEqual(['g-1'])
  })

  it('список не растёт бесконечно: последние 500', () => {
    const many = Array.from({ length: 600 }, (_, index) => `g-${index}`)
    const merged = mergeSeen([], many)
    expect(merged).toHaveLength(500)
    // старые записи вытеснены, новые на месте
    expect(merged[merged.length - 1]).toBe('g-599')
  })

  it('без хранилища окно не падает: пусто вместо ошибки', () => {
    // в node localStorage нет — чтение и запись не должны бросать
    expect(readSeen()).toEqual([])
    expect(() => writeSeen(['g-1'])).not.toThrow()
  })
})
