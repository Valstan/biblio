import { describe, expect, it } from 'vitest'
import { buildStatsCsv, fmtDuration, verdictClass } from '../src/renderer/src/format'
import type { Stats } from '../src/shared/types'

const stats: Stats = {
  at: 0,
  days: 7,
  restarts: 12,
  network_events: 3,
  by_reason: { 'компактизация контекста': 9 },
  by_kind: { ротация: 9 },
  by_area: [
    { slug: 'brain2', total: 9, reasons: { 'компактизация контекста': 9 }, last: '2026-11-23 10:00:00', running: true },
    { slug: '—', total: 3, reasons: { 'ручной перезапуск': 3 }, last: '', running: false }
  ],
  events: [],
  areas_total: 19,
  areas_running: 6
}

describe('verdictClass', () => {
  it('различает состояния сессии', () => {
    expect(verdictClass('работает')).toBe('ok')
    expect(verdictClass('упала по сети')).toBe('bad')
    expect(verdictClass('контекст заполнен')).toBe('warn')
    expect(verdictClass('готова')).toBe('muted')
    expect(verdictClass('непонятно')).toBe('')
  })
})

describe('fmtDuration', () => {
  it('переводит миллисекунды в человеческий вид', () => {
    expect(fmtDuration(0)).toBe('—')
    expect(fmtDuration(30_000)).toBe('меньше минуты')
    expect(fmtDuration(12 * 60_000)).toBe('12 мин')
    expect(fmtDuration(90 * 60_000)).toBe('1 ч 30 мин')
    expect(fmtDuration(50 * 3600_000)).toBe('2 дн 2 ч')
  })
})

describe('buildStatsCsv', () => {
  it('даёт заголовок и строки по областям', () => {
    const csv = buildStatsCsv(stats)
    const lines = csv.split('\r\n')
    expect(lines[0]).toBe('область;событий;причины;последнее;запущена')
    expect(lines[1]).toContain('brain2')
    expect(lines[1]).toContain('компактизация контекста: 9')
    expect(lines[1].endsWith('да')).toBe(true)
  })

  it('добавляет сводку в конец', () => {
    const csv = buildStatsCsv(stats)
    expect(csv).toContain('всего событий;12')
    expect(csv).toContain('областей запущено;6 из 19')
  })
})
