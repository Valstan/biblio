import type { Stats } from '@shared/types'

/** Класс цвета для вердикта сессии. */
export function verdictClass(verdict: string): string {
  switch (verdict) {
    case 'работает':
      return 'ok'
    case 'готова':
    case 'проба':
      return 'muted'
    case 'скомпактилась':
    case 'контекст заполнен':
    case 'брошена':
    case 'есть оборванные субагенты':
    case 'ход завис':
      return 'warn'
    case 'упала по сети':
    case 'вендор недоступен':
    case 'кончились очки':
      return 'bad'
    case 'ждёт владельца':
      return 'info'
    default:
      return ''
  }
}

/** Длительность в человекочитаемом виде: «3 дн 4 ч», «12 мин». */
export function fmtDuration(ms: number): string {
  if (!ms || ms < 0) return '—'
  const minutes = Math.floor(ms / 60000)
  if (minutes < 1) return 'меньше минуты'
  if (minutes < 60) return `${minutes} мин`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ч ${minutes % 60} мин`
  const days = Math.floor(hours / 24)
  return `${days} дн ${hours % 24} ч`
}

/** CSV статистики: разделитель «;» — Excel в русской локали открывает сразу. */
export function buildStatsCsv(stats: Stats): string {
  const header = 'область;событий;причины;последнее;запущена'
  const rows = stats.by_area.map((area) =>
    [
      area.slug,
      String(area.total),
      Object.entries(area.reasons)
        .map(([reason, count]) => `${reason}: ${count}`)
        .join(', '),
      area.last || '—',
      area.running ? 'да' : 'нет'
    ].join(';')
  )
  const summary = [
    `всего событий;${stats.restarts}`,
    `областей запущено;${stats.areas_running} из ${stats.areas_total}`,
    `период, дней;${stats.days}`
  ]
  return [header, ...rows, '', ...summary].join('\r\n')
}
