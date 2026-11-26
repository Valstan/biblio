/**
 * Логика вкладки «Хранилище промтов» — чистая, без React и без сети.
 *
 * Здесь то, что показывает окно: строки промтов и планов, состояние места
 * хранения (в базе «МиниГена» или на внешнем ресурсе), поиск, подходящие
 * области и тексты подтверждений. Всё это проверяется тестом
 * (`tests/prompts-view.test.ts`) — правило владельца: приёмка правилом, а
 * не «на глаз».
 *
 * ⚠️ Никаких выдуманных данных: чего нет — показываем «—», а не догадку.
 */

import type {
  PromptRecord,
  PromptStoreInfo,
  PromptTarget,
  PromptsSnapshot,
  PromptsView
} from '@shared/types'

/** Строка промта (или плана) для таблицы. */
export interface PromptRow {
  id: string
  project: string
  title: string
  kind: 'промт' | 'план'
  created: string
  auto: boolean
  archived: boolean
  preview: string
  /** полный текст — по нему ищет поиск (в таблице показан `preview`) */
  text: string
  /** чего не хватает в записи (пусто — запись полная) */
  missing: string[]
}

export type Tone = 'ok' | 'warn' | 'bad'

/** Место хранения — что окно показывает сверху вкладки. */
export interface StoreView {
  tone: Tone
  label: string
  detail: string
}

const DASH = '—'
/** Сколько символов текста показываем в строке таблицы. */
const PREVIEW_LIMIT = 160

/**
 * Строки промтов: вид (промт/план), раздел, дата, метка «авто» и превью
 * текста. Пустое поле — «—», а в `missing` — чего именно не хватает.
 */
export function promptRows(snapshot: PromptsSnapshot | null): PromptRow[] {
  if (!snapshot) return []
  const prompts = snapshot.prompts ?? []
  return prompts.map((prompt: PromptRecord) => {
    const project = (prompt.project || '').trim()
    const title = (prompt.title || '').trim()
    const body = (prompt.body || '').trim()
    const missing: string[] = []
    if (!title) missing.push('название')
    if (!project) missing.push('раздел')
    if (!body) missing.push('текст')
    return {
      id: prompt.id || DASH,
      project: project || DASH,
      title: title || DASH,
      kind: prompt.kind === 'plan' ? 'план' : 'промт',
      created: (prompt.createdAt || '').trim() || DASH,
      auto: prompt.auto === true,
      archived: prompt.archived === true,
      preview: body
        ? body.length > PREVIEW_LIMIT
          ? `${body.slice(0, PREVIEW_LIMIT)}…`
          : body
        : DASH,
      text: body,
      missing
    }
  })
}

/**
 * Место хранения — три честных состояния: спрашивали и работает, спросили и
 * не работает, не спросили вовсе. Пустое состояние — «неизвестно», а не
 * «работает».
 */
export function storeView(store: PromptStoreInfo | null): StoreView {
  if (!store) {
    return {
      tone: 'bad',
      label: 'хранилище не спросили',
      detail: 'мост не ответил — место хранения неизвестно'
    }
  }
  if (store.ok) {
    return {
      tone: 'ok',
      label: (store.label || '').trim() || DASH,
      detail: (store.location || '').trim() || 'в базе «МиниГена»'
    }
  }
  return {
    // Внешний ресурс без связи — предупреждение (связь может вернуться),
    // своя база без причины — ошибка.
    tone: store.kind === 'karman' ? 'warn' : 'bad',
    label: 'хранилище недоступно',
    detail: (store.reason || '').trim() || 'причина неизвестна'
  }
}

/**
 * Поиск по названию, разделу и **всему** тексту; регистр не важен. Пустой
 * запрос — показываем все строки.
 */
export function filterRows(rows: PromptRow[], query: string): PromptRow[] {
  const needle = (query || '').trim().toLowerCase()
  if (!needle) return rows
  return rows.filter(
    (row) =>
      row.title.toLowerCase().includes(needle) ||
      row.project.toLowerCase().includes(needle) ||
      row.text.toLowerCase().includes(needle) ||
      row.preview.toLowerCase().includes(needle)
  )
}

/**
 * Области, которым можно отдать этот промт: только те, чей проект совпадает с
 * разделом промта. Совпадение — без учёта регистра и **по обоим именам**
 * области: её слаг (`minigen`) и её раздел в библиотеке (`MiniGen`).
 * Несовпадение — пустой список: задание уехало бы не туда, это правило, а не
 * ошибка.
 */
export function targetsFor(row: PromptRow, targets: PromptTarget[]): PromptTarget[] {
  const section = (row.project || '').trim().toLowerCase()
  if (!section || section === DASH.toLowerCase()) return []
  return (targets ?? []).filter((target) => {
    const names = [(target.section || '').trim().toLowerCase(),
                   (target.slug || '').trim().toLowerCase()]
    return names.includes(section)
  })
}

/** Автораздача — только для промтов, помеченных владельцем. */
export function autoEligible(row: PromptRow): boolean {
  return row.auto === true
}

/**
 * Что скажет окно перед отправкой промта в область.
 *
 * Отправка **тратит ресурсы**: открывает новую сессию и очки аккаунта —
 * поэтому подтверждение обязательно, и в нём названы область и последствия.
 */
export function dispatchConfirmText(row: PromptRow, target: PromptTarget): string {
  const area = target.title ? `${target.slug} (${target.title})` : target.slug
  const busy = target.busy ? '\nВ области уже есть живая сессия.' : ''
  return (
    `Отправить промт «${row.title}» в область ${area}?\n\n` +
    `Раздел промта: ${row.project} — область работает в своём разделе.\n\n` +
    `Откроется новая сессия в области ${target.slug}, потратит очки аккаунта.${busy}` +
    '\n\nПродолжить?'
  )
}

/**
 * Что скажет окно перед архивированием.
 *
 * Архив в этой фиче означает «сделано» — промт перестаёт выдаваться, поэтому
 * подтверждение обязательно.
 */
export function archiveConfirmText(row: PromptRow): string {
  return (
    `Архивировать промт «${row.title}»?\n\n` +
    'Архив = сделано: он перестанет выдаваться.\n\n' +
    'Продолжить?'
  )
}

/**
 * Что скажет окно перед возвратом промта из архива.
 *
 * Архив — **не удаление**: заданный по ошибке промт должен вернуться в
 * оборот, поэтому и действие это, и подтверждение называют последствия.
 */
export function unarchiveConfirmText(row: PromptRow): string {
  return (
    `Вернуть промт «${row.title}» из архива?\n\n` +
    'Он снова попадёт в выдачу: живые промты уходят в область своего проекта.\n\n' +
    'Продолжить?'
  )
}

/** Подпись кнопки архива: архивировать или вернуть — по состоянию записи. */
export function archiveActionLabel(row: PromptRow): string {
  return row.archived ? 'Вернуть из архива' : 'В архив'
}

/**
 * Вид выдачи: живые, архивные или и те и другие.
 *
 * ⚠️ Архив виден **отдельно**: заданный по ошибке промт не должен просто
 * исчезнуть из оборота — его надо видеть и уметь вернуть.
 */
export function visibleRows(rows: PromptRow[], view: PromptsView): PromptRow[] {
  if (view === 'archived') return rows.filter((row) => row.archived)
  if (view === 'live') return rows.filter((row) => !row.archived)
  return rows
}

/** Подпись вида выдачи — чтобы окно не гадало, что показывает. */
export function viewLabel(view: PromptsView): string {
  if (view === 'archived') return 'архив'
  if (view === 'all') return 'живые и архив'
  return 'живые'
}

/**
 * Что скажет окно перед пометкой «раздавать автоматически» (или снятием
 * пометки): без живого оператора промт уйдёт в область своего проекта сам.
 */
export function markConfirmText(row: PromptRow, auto: boolean): string {
  if (!auto) {
    return (
      `Снять пометку «раздавать автоматически» с промта «${row.title}»?\n\n` +
      'Дальше он уйдёт в область своего проекта только по вашей команде.\n\n' +
      'Продолжить?'
    )
  }
  return (
    `Пометить промт «${row.title}» как «раздавать автоматически»?\n\n` +
    `Без живого оператора он уйдёт в область своего проекта (${row.project}) ` +
    'и потратит очки аккаунта.\n\n' +
    'Продолжить?'
  )
}

/**
 * Проверка черновика промта: список претензий (пусто — можно сохранять).
 *
 * Раздел — короткое имя латиницей без пробелов: по нему промт уходит в
 * область своего проекта.
 */
export function validateDraft(draft: { project: string; title: string; body: string }): string[] {
  const problems: string[] = []
  const project = (draft.project || '').trim()
  if (!project) {
    problems.push('нужен раздел проекта — короткое имя латиницей, без пробелов')
  } else if (!/^[A-Za-z0-9._-]+$/.test(project)) {
    problems.push('раздел: только латиница, цифры, точка, дефис и подчёркивание — без пробелов')
  }
  if (!(draft.title || '').trim()) {
    problems.push('нужен заголовок промта')
  }
  if (!(draft.body || '').trim()) {
    problems.push('нужен текст промта')
  }
  return problems
}
