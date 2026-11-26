/**
 * Локальная база «МиниГена» — что окно показывает о базе (промт #92).
 *
 * Здесь чистая логика: без React и без сети. Окно должно говорить ровно то,
 * что сообщил мост, и не придумывать ничего: чужой вид данных не «пропал» —
 * он лежит в неразобранном, и про это сказано прямо.
 *
 * ⚠️ Решение по спорным записям принимает оператор, а не программа: окно
 * показывает вопрос и варианты, но само ничего не перезаписывает.
 */

import type { DbImportPlan, DbStatus } from '@shared/types'

export type Tone = 'ok' | 'warn' | 'bad'

/** Строка состояния базы: подпись, значение и тон (для цвета чипа). */
export interface DbStatusRow {
  label: string
  value: string
  tone: Tone
}

/** Решение оператора по спорной записи. */
export type DbDecision = 'file' | 'keep' | 'copy'

const DASH = '—'

/** Как решение выглядит для человека. */
export const DB_DECISIONS: { id: DbDecision; label: string }[] = [
  { id: 'file', label: 'взять из файла' },
  { id: 'keep', label: 'оставить своё' },
  { id: 'copy', label: 'положить рядом отдельной записью' }
]

/** Подпись решения (для окна и для подтверждения). */
export function dbDecisionLabel(value: string): string {
  const found = DB_DECISIONS.find((item) => item.id === value)
  return found ? found.label : DASH
}

/**
 * Строки состояния базы для карточки в настройках.
 *
 * Нет ответа моста — это «неизвестно», а не «пусто»: владелец должен видеть,
 * что состояние не спросили.
 */
export function dbStatusRows(status: DbStatus | null): DbStatusRow[] {
  if (!status) {
    return [
      { label: 'Файл базы', value: 'мост не ответил — путь неизвестен', tone: 'bad' },
      { label: 'Записей', value: 'мост не ответил — неизвестно', tone: 'bad' }
    ]
  }
  const counts = status.counts_by_kind ?? {}
  const titles = status.kind_titles ?? {}
  const kinds = Object.keys(counts)
    .filter((kind) => kind !== 'unparsed')
    .map((kind) => `${titles[kind] || kind} — ${counts[kind]}`)
  const rows: DbStatusRow[] = [
    { label: 'Файл базы', value: status.path || DASH, tone: 'ok' },
    {
      label: 'Версия схемы',
      value: String(status.schema_version ?? DASH),
      tone: 'ok'
    },
    {
      label: 'Записей',
      value: String(status.total ?? 0),
      tone: 'ok'
    },
    {
      label: 'Виды данных',
      value: kinds.length ? kinds.join(', ') : 'пока пусто',
      tone: kinds.length ? 'ok' : 'warn'
    }
  ]
  if ((status.unparsed ?? 0) > 0) {
    rows.push({
      label: 'Неразобранное',
      value:
        `${status.unparsed} — чужие или будущие виды данных; эта версия ` +
        'программы их не знает, но они сохранены и уйдут в следующей выгрузке',
      tone: 'warn'
    })
  }
  return rows
}

/**
 * Что покажет импорт — одной строкой, до того как владелец нажмёт кнопку.
 *
 * ⚠️ Числа — из плана моста, а не придуманные: что не спросили, то и не
 * показано.
 */
export function dbPlanSummary(plan: DbImportPlan | null): string {
  if (!plan) return ''
  const counts = plan.counts ?? {}
  const parts: string[] = []
  if (counts.insert) parts.push(`перенесётся новых: ${counts.insert}`)
  if (counts.same) parts.push(`совпадёт с вашей базой: ${counts.same}`)
  if (counts.conflict) parts.push(`спорных: ${counts.conflict}`)
  if ((plan.unknown_kinds ?? []).length) {
    parts.push(`незнакомых видов: ${plan.unknown_kinds.join(', ')}`)
  }
  const version = plan.version ?? {}
  if (version.note) parts.push(version.note)
  if (plan.checksums_ok === false) {
    parts.push('⚠️ контрольные суммы не сошлись — файл правили по дороге')
  }
  return parts.join('; ')
}

/**
 * Вопросы оператору — из плана моста.
 *
 * ⚠️ Это ровно те вопросы, которые мост задал по спорным данным: окно их
 * показывает, но ответ принимает человек.
 */
export function dbQuestions(plan: DbImportPlan | null): string[] {
  return (plan?.questions ?? []).map((item) => (item || '').trim()).filter(Boolean)
}
