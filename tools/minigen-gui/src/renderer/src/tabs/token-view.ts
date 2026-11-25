/**
 * Логика токена копии — чистая, без React и без сети.
 *
 * Здесь то, что показывает окно про токен копии: состояние токена, строки
 * карточки «GitHub», заявки на токен, тексты подтверждений и результаты
 * действий (заказ владельца 25.11, промт #130).
 *
 * ⚠️ **Значение токена в переписку и в отчёты не ходит.** В заявках его нет
 * и быть не может, а выпущенный токен показывается один раз отдельным
 * блоком — здесь только маска. Поэтому ни одна функция этого файла не
 * возвращает значение выпущенного токена, кроме `tokenIssueView` — её вызов
 * читает окно один раз и сразу показывает маску.
 *
 * ⚠️ Никаких выдуманных данных: чего нет — «—», а «неизвестно» не
 * называется «работает». Мост отвечает не всегда (окно открывается раньше
 * моста), поэтому «нет ответа» — отдельный случай.
 *
 * Какое подтверждение к какому действию:
 * - `issueConfirmText` — кнопка «Выпустить токен» (по заявке);
 * - `requestConfirmText` — кнопка «Закрепить копию» (TOFU по заявке);
 * - `tokenSetConfirmText` — кнопка «Сохранить токен» (копия вводит значение,
 *   полученное от владельца).
 */

import type {
  CloneStatus,
  CopyTokenCheck,
  CopyTokenIssue,
  CopyTokenRequest,
  CopyTokenStatus
} from '@shared/types'
import { formatDateTime } from '../time'
import { modeView, type Tone } from './copy-view'

/** Строка таблицы: подпись и значение (готовый текст для окна). */
export interface TokenRow {
  label: string
  value: string
}

/** Строка заявки на токен — то, что видно владельцу в таблице заявок. */
export interface TokenRequestRow {
  id: string
  instance_id: string
  name: string
  /** кто прислал: имя и идентификатор копии */
  who: string
  /** «своя копия» / «чужая копия» — коротко, по родословной и подписи */
  relation: string
  /** подробность от моста: почему заявка своя или чужая */
  relation_detail: string
  /** состояние подписи заявки */
  signature: string
  folder: string
  /** когда пришла заявка */
  when: string
  /** отпечаток подписи копии */
  key_id: string
  tone: Tone
  archived: boolean
}

/** Состояние токена — чип и пояснение под ним. */
export interface TokenStateView {
  tone: Tone
  label: string
  detail: string
}

const DASH = '—'

/**
 * Состояние токена копии: есть ли он, подтверждён ли под свою папку.
 *
 * ⚠️ Токен другой копии — это не «токен есть, всё хорошо»: привязка не
 * сойдётся, и окно говорит об этом прямо.
 */
export function tokenStateView(status: CopyTokenStatus | null): TokenStateView {
  if (!status) {
    return {
      tone: 'bad',
      label: 'состояние неизвестно',
      detail: 'мост не ответил — состояние токена неизвестно'
    }
  }
  const reason = (status.reason || '').trim()
  if (!status.has_token) {
    return {
      tone: 'warn',
      label: 'токена нет',
      detail: reason || 'токена нет — запросите его у владельца'
    }
  }
  if (status.bound) {
    return {
      tone: 'ok',
      label: 'токен подтверждён',
      detail: reason || 'токен подтверждён под свою папку'
    }
  }
  return {
    tone: 'bad',
    label: 'токен не подтверждён',
    detail: reason || 'токен есть, но под свою папку он не подтверждён'
  }
}

/**
 * Строки карточки «GitHub»: режим работы копии, путь до репозитория, имя
 * репозитория копии (в режиме «свой репозиторий»), папка копии в общем
 * репозитории, где лежит токен (вне репозитория), отпечаток токена и признак
 * привязки.
 *
 * ⚠️ Пусто — «—», а не догадка: имени репозитория нет, если режим не
 * «свой» (тогда мост и не заводил его).
 */
export function tokenStatusRows(
  status: CopyTokenStatus | null,
  clone: CloneStatus | null = null
): TokenRow[] {
  const mode = modeView(clone)
  const own = (clone?.mode || '').trim() === 'own'
  const has = status?.has_token === true
  const path = (status?.path || '').trim()
  const folder = (status?.folder || '').trim()
  const tokenId = (status?.token_id || '').trim()

  return [
    { label: 'Токен', value: status ? (has ? 'есть' : 'нет') : DASH },
    {
      label: 'Режим работы копии',
      value: mode.tone === 'ok' ? mode.label : mode.detail
    },
    { label: 'Путь до репозитория', value: (clone?.clone_dir || '').trim() || DASH },
    {
      label: 'Репозиторий копии',
      value: own ? (clone?.copy_repo || '').trim() || DASH : DASH
    },
    {
      label: 'Папка копии в общем репозитории',
      value: folder || DASH
    },
    {
      label: 'Где лежит токен',
      value: path
        ? `${path} (вне репозитория)`
        : status && !has
          ? 'токена нет'
          : DASH
    },
    { label: 'Отпечаток токена', value: tokenId || DASH },
    {
      label: 'Привязка токена',
      value: status
        ? status.bound
          ? 'подтверждён под свою папку'
          : 'не подтверждён'
        : DASH
    }
  ]
}

/**
 * Когда пришла заявка: дату мост присылает строкой.
 *
 * ⚠️ Если дату разобрать не удалось — показываем как есть: выдуманная дата
 * хуже неразобранной строки.
 */
export function requestTime(value: string): string {
  const raw = (value || '').trim()
  if (!raw) return DASH
  const parsed = Date.parse(raw)
  if (Number.isNaN(parsed)) return raw
  return formatDateTime(parsed) || raw
}

/**
 * Строки заявок на токен: кто прислал, чья это копия, состояние подписи,
 * папка и время.
 *
 * ⚠️ Значения токена в заявке нет и быть не может — заявка несёт только
 * родословную и подпись.
 */
export function tokenRequestRows(requests: CopyTokenRequest[] | null): TokenRequestRow[] {
  if (!requests) return []
  return requests.map((request) => {
    const name = (request.name || '').trim()
    const instance = (request.instance_id || '').trim()
    const relation = (request.relation || '').trim()
    const lower = relation.toLowerCase()
    const short = lower.startsWith('своя')
      ? 'своя копия'
      : lower.startsWith('чужая')
        ? 'чужая копия'
        : DASH
    return {
      id: (request.id || '').trim() || DASH,
      instance_id: instance || DASH,
      name: name || DASH,
      who: name && instance ? `${name} (${instance})` : name || instance || DASH,
      relation: short,
      relation_detail: relation || DASH,
      signature: (request.signature_state || '').trim() || DASH,
      folder: (request.folder || '').trim() || DASH,
      when: requestTime(request.created),
      key_id: (request.key_id || '').trim() || DASH,
      // ⚠️ Чужая копия с несовпавшим отпечатком — возможная подделка: тон
      // «плохо», а не просто «чужая».
      tone: short === 'своя копия' ? 'ok' : /не совпал|подделк/i.test(relation) ? 'bad' : 'warn',
      archived: request.archived === true
    }
  })
}

/**
 * Маска значения токена: видны только последние четыре знака.
 *
 * ⚠️ Маска — для показа, а не для отчёта: значение токена в отчёты и в
 * переписку не попадает.
 */
export function maskToken(value: string): string {
  const secret = (value || '').trim()
  if (!secret) return ''
  const tail = secret.length > 4 ? secret.slice(-4) : ''
  return `${'•'.repeat(8)}${tail}`
}

/**
 * Результат выпуска токена: тон, текст и **само значение** — значение
 * показывается один раз, отдельным блоком, по умолчанию под маской.
 */
export function tokenIssueView(issue: CopyTokenIssue | null): {
  tone: Tone
  text: string
  token: string
} {
  if (!issue) {
    return { tone: 'bad', text: 'мост не ответил — токен не выпущен', token: '' }
  }
  if (issue.ok === false) {
    return {
      tone: 'bad',
      text: issue.error || issue.why || 'мост не выпустил токен',
      token: ''
    }
  }
  const token = (issue.token || '').trim()
  if (!token) {
    return {
      tone: 'warn',
      text: issue.why || 'токен выпущен, но значение мост не вернул — показать нечего',
      token: ''
    }
  }
  return {
    tone: 'ok',
    text: issue.why || `токен выпущен под папку ${issue.folder || DASH}`,
    token
  }
}

/**
 * Что сказать после отправки заявки на токен.
 *
 * ⚠️ Текст моста уже называет раздел и говорит, что ждём токен от
 * владельца; если моста нет — честное «ждём», а не «заявка принята».
 */
export function tokenRequestResultText(res: {
  ok?: boolean
  why?: string
  error?: string
}): string {
  if (res.ok === false) return res.error || 'заявка не ушла — мост её не принял'
  return res.why || 'заявка ушла — ждём токен от владельца'
}

/**
 * Что сказать после сохранения токена, полученного от владельца.
 *
 * ⚠️ Ответ моста различает «принят и подтверждён» и «сохранён, но привязка не
 * подтвердилась» — это разные вещи, и окно говорит о них разными словами.
 */
export function tokenSetResultText(res: {
  ok?: boolean
  why?: string
  error?: string
  bound?: boolean
}): string {
  if (res.ok === false) return res.error || 'мост не принял токен'
  if ((res.why || '').trim()) return (res.why || '').trim()
  if (res.bound === true) return 'токен принят и подтверждён под свою папку'
  if (res.bound === false) {
    return 'токен сохранён, но подтверждение под свою папку не прошло'
  }
  return 'токен сохранён'
}

/**
 * Результат проверки доступа: токен есть и выдан **под эту** папку — или нет.
 *
 * ⚠️ Чужая папка — это отказ, а не «доступ частичный»: токен другой копии
 * к этой папке не подходит.
 */
export function accessCheckText(check: CopyTokenCheck | null): {
  tone: Tone
  text: string
} {
  if (!check) {
    return { tone: 'bad', text: 'мост не ответил — доступ не проверяли' }
  }
  const reason = (check.reason || '').trim()
  if (check.ok) {
    return {
      tone: 'ok',
      text: reason || `доступ есть: токен подтверждён под папку ${check.folder || DASH}`
    }
  }
  return {
    tone: 'bad',
    text: reason || `доступа нет: токен не подтверждён под папку ${check.folder || DASH}`
  }
}

/**
 * Подтверждение выпуска токена по заявке: под папку заявки, значение покажут
 * один раз, отдать его копии.
 */
export function issueConfirmText(request: CopyTokenRequest): string {
  const name = (request.name || '').trim() || DASH
  const instance = (request.instance_id || '').trim() || DASH
  const folder = (request.folder || '').trim() || DASH
  return (
    `Выпустить токен для копии «${name}» (${instance})?\n\n` +
    `Папка копии в общем репозитории программы: ${folder}\n` +
    'Значение токена покажут один раз — передайте его копии.\n' +
    'Токен подтверждает право работать со своей папкой: на чужую папку он ' +
    'не подходит.\n' +
    'Общий репозиторий программы один, а токен выдаётся только на одну папку.\n\n' +
    'Продолжить?'
  )
}

/**
 * Подтверждение закрепления копии (TOFU): запоминаем отпечаток подписи, и
 * после этого заявки этой копии с другим отпечатком считаются подделкой.
 */
export function requestConfirmText(request: CopyTokenRequest): string {
  const name = (request.name || '').trim() || DASH
  const instance = (request.instance_id || '').trim() || DASH
  const folder = (request.folder || '').trim() || DASH
  const key = (request.key_id || '').trim() || DASH
  return (
    `Закрепить копию «${name}» (${instance})?\n\n` +
    `Заявка пришла на папку ${folder} в общем репозитории программы.\n` +
    `Отпечаток подписи: ${key}\n\n` +
    'Общий репозиторий программы один, поэтому отпечаток подписи закрепляют ' +
    'вручную: после этого заявки этой копии с другим отпечатком считаются ' +
    'подделкой, и токен такой заявке выпускать не стоит.\n\n' +
    'Продолжить?'
  )
}

/**
 * Подтверждение сохранения токена, полученного от владельца.
 *
 * ⚠️ Токен записывается **в стор на машине, вне репозитория**, и в чат его
 * вставлять не надо — об этом окно говорит до записи, а не после.
 */
export function tokenSetConfirmText(): string {
  return (
    'Сохранить токен, полученный от владельца?\n\n' +
    'Токен будет записан в стор на машине — вне репозитория.\n' +
    'Значение токена подтверждает право работать со своей папкой в общем ' +
    'репозитории программы; на чужую папку оно не действует.\n' +
    'Общий репозиторий программы один на всех копий, а токен — только на ' +
    'свою папку.\n' +
    'В чат токен вставлять не надо: в переписку и в отчёты его значение ' +
    'попадать не должно.\n\n' +
    'Продолжить?'
  )
}
