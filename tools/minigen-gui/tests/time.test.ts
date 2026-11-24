import { describe, expect, it } from 'vitest'
import { TZ, TZ_LABEL, formatDate, formatDateTime, formatTime, toMs } from '../src/renderer/src/time'

/**
 * Время в программе — везде Москва, Россия (UTC+3) (заказ владельца 24.11).
 *
 * Проверяем, что окно показывает **московское** время независимо от зоны
 * машины, на которой запущено, и что секунды/миллисекунды не путаются.
 */

describe('время программы', () => {
  it('зона объявлена явно', () => {
    expect(TZ).toBe('Europe/Moscow')
    expect(TZ_LABEL).toContain('Москва')
    expect(TZ_LABEL).toContain('UTC+3')
  })

  it('секунды и миллисекунды приводятся к одним', () => {
    expect(toMs(1_700_000_000)).toBe(1_700_000_000_000)
    expect(toMs(1_700_000_000_000)).toBe(1_700_000_000_000)
    expect(toMs(0)).toBe(0)
    expect(toMs(undefined)).toBe(0)
  })

  it('показывает московское время, а не время машины', () => {
    // 09:45:53 UTC ⇒ 12:45:53 в Москве (UTC+3, без летнего времени)
    const text = formatTime(Date.UTC(2026, 10, 24, 9, 45, 53))
    expect(text).toContain('12:45')
    expect(text).toContain('53')
  })

  it('дата и время — тоже по Москве', () => {
    const text = formatDateTime(Date.UTC(2026, 10, 24, 9, 45, 53))
    expect(text).toContain('2026')
    expect(text).toContain('12:45')
    expect(text).not.toContain('09:45')
  })

  it('только дата — по Москве', () => {
    // 24.11 23:30 UTC ⇒ в Москве уже 25.11 02:30 ⇒ дата 25.11
    const text = formatDate(Date.UTC(2026, 10, 24, 23, 30))
    expect(text).toContain('25')
    expect(text).toContain('2026')
  })

  it('пусто — пустая строка, а не Invalid Date', () => {
    expect(formatDateTime(0)).toBe('')
    expect(formatDateTime(undefined)).toBe('')
    expect(formatTime(null)).toBe('')
    expect(formatDate(0)).toBe('')
  })
})
