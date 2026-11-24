/**
 * Время в окне программы — **везде Москва, Россия (UTC+3)**.
 *
 * Заказ владельца (24.11): «возьми за основу время по Москве и явно об этом
 * показывай в программе, что она время везде считает по часовому поясу Москва,
 * Россия».
 *
 * ⚠️ Почему зона задана явно, а не «как у машины»: программа может стоять на
 * любой машине, а время у неё должно быть одно и то же — московское. Иначе
 * журнал, статистика и «установлено в 14:32» показывали бы разное время в
 * зависимости от того, где запущена.
 *
 * Москва с 2014 года не переходит на летнее время ⇒ UTC+3 круглый год.
 */
export const TZ = 'Europe/Moscow'

/** Как это называется в интерфейсе — показываем явно, по заказу владельца. */
export const TZ_LABEL = 'Москва, Россия (UTC+3)'

/**
 * Метка времени к миллисекундам.
 *
 * ⚠️ Разные источники присылают разное: раскатка пишет `installed_at` в
 * **секундах**, журнал и шаги обновления — в **миллисекундах**. Значения
 * меньше 1e12 — секунды (это 2001 год в секундах и раньше), большие —
 * миллисекунды.
 */
export function toMs(value: number | null | undefined): number {
  if (!value) return 0
  return value < 1e12 ? value * 1000 : value
}

/** Дата и время по Москве: «24.11.2026, 14:32:05». */
export function formatDateTime(value: number | null | undefined): string {
  const ms = toMs(value)
  if (!ms) return ''
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('ru-RU', { timeZone: TZ })
}

/** Только время по Москве: «14:32:05». */
export function formatTime(value: number | null | undefined): string {
  const ms = toMs(value)
  if (!ms) return ''
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('ru-RU', { timeZone: TZ })
}

/** Только дата по Москве: «24.11.2026». */
export function formatDate(value: number | null | undefined): string {
  const ms = toMs(value)
  if (!ms) return ''
  const date = new Date(ms)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('ru-RU', { timeZone: TZ })
}
