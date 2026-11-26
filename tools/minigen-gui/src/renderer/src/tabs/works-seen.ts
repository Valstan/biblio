/**
 * Какие наработки других копий эта копия уже видела — помним **на машине**.
 *
 * ⚠️ Зачем это нужно: окно показывает наработки других копий постоянно, а
 * владельцу интересны **новые** («сосед что-то прислал»), а не все подряд.
 * Показывать всё подряд — шум, а шум владелец не читает. Поэтому окно
 * запоминает, какие гены уже показывались, и уведомляет только о новых.
 *
 * Хранение — в localStorage окна: это машинная память, а не данные программы
 * (в пуле и настройках ничего лишнего не появляется).
 */

/** Ключ в localStorage окна. */
export const SEEN_KEY = 'minigen.works.seen.v1'

/** Прочитать список виденного (пусто — storage недоступен или данных нет). */
export function readSeen(): string[] {
  try {
    const raw = globalThis.localStorage?.getItem(SEEN_KEY) ?? ''
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === 'string')
      : []
  } catch {
    // localStorage может быть недоступен (окно без хранилища) или данные
    // испорчены — тогда считаем, что не видели ничего, и не ломаем окно.
    return []
  }
}

/** Запомнить список виденного (ошибка хранилища — не повод ломать окно). */
export function writeSeen(ids: readonly string[]): void {
  try {
    globalThis.localStorage?.setItem(SEEN_KEY, JSON.stringify(ids))
  } catch {
    /* хранилище недоступно — уведомления просто будут повторяться */
  }
}

/**
 * Дописать новые идентификаторы к списку виденного (без повторов).
 *
 * ⚠️ Порядок сохраняется: виденное — в порядке «как увидели», чтобы список
 * не рос бесконечно вглубь (берём последние 500 — этого хватает с запасом).
 */
export function mergeSeen(
  seen: readonly string[],
  ids: readonly string[]
): string[] {
  const out: string[] = []
  const known = new Set<string>()
  for (const item of [...seen, ...ids]) {
    const id = (item || '').trim()
    if (!id || known.has(id)) continue
    known.add(id)
    out.push(id)
  }
  return out.slice(-500)
}
