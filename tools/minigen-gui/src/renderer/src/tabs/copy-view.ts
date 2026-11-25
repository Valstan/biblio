/**
 * Логика режимов копии и первого запуска — чистая, без React и без сети.
 *
 * Здесь то, что показывает окно «МиниГена» про копию: какой режим выбран,
 * доступен ли обмен наработками, что за вопрос первого запуска, какие чужие
 * наработки стоит посмотреть и какие тексты подтверждения видит владелец
 * (заказ владельца 25.11, промт #131).
 *
 * ⚠️ Никаких выдуманных данных: чего нет — «—», а неизвестное состояние
 * называется неизвестным, а не «работает». Мост отвечает не всегда (окно
 * открывается раньше моста), поэтому «нет ответа» — отдельный случай.
 */

import type {
  CloneStatus,
  CopyFirstRunSnapshot,
  CopyMode,
  CopyModeOption,
  CopyOffer,
  GeneSuggestions
} from '@shared/types'
import { formatDateTime } from '../time'

export type Tone = 'ok' | 'warn' | 'bad'

/** Строка состояния: тон, короткая подпись и подробности (почему так). */
export interface StatusView {
  tone: Tone
  label: string
  detail: string
}

/** Чужая наработка — одна строка таблицы подсказок. */
export interface SuggestionRow {
  gene: string
  title: string
  author: string
  why: string
  fits: boolean
  files: string[]
  created: string
}

const DASH = '—'

/**
 * Что значит каждый режим — словами владельца (промт #131).
 *
 * ⚠️ Смысл режимов задан заказом, а не придуман окном: окно обязано говорить
 * ровно это, иначе владелец поверит обещанию, которого нет.
 */
const MODE_MEANING: Partial<Record<CopyMode, string>> = {
  own: 'свой репозиторий на GitHub: ветки и PR — в общий репозиторий программы',
  shared: 'правки и PR идут прямо в общий репозиторий программы',
  local:
    'правки — в локальный репозиторий на этой машине: PR не открывается, ' +
    'обмен с другими копиями недоступен'
}

/** Что изменится при смене режима — то же для подтверждения. */
const MODE_CHANGE: Partial<Record<CopyMode, string>> = {
  own:
    'создастся репозиторий в вашем GitHub-аккаунте: правки и PR пойдут ' +
    'в общий репозиторий программы',
  shared:
    'правки и PR пойдут в общий репозиторий программы — копия правит программу ' +
    'там же, где она живёт',
  local:
    'правки пойдут в локальный репозиторий на этой машине, PR не откроется, ' +
    'обмен с копиями будет недоступен'
}

/**
 * Текущий режим копии.
 *
 * Нет ответа моста — это «неизвестно», а не «работает»: владелец должен видеть,
 * что состояние не спросили.
 */
export function modeView(status: CloneStatus | null): StatusView {
  if (!status) {
    return {
      tone: 'bad',
      label: 'режим неизвестен',
      detail: 'мост не ответил — режим неизвестен'
    }
  }
  const mode = (status.mode || '').trim()
  if (!mode) {
    return {
      tone: 'bad',
      label: 'режим неизвестен',
      detail: 'мост не ответил — режим неизвестен'
    }
  }
  const meaning = MODE_MEANING[mode as CopyMode]
  return {
    tone: 'ok',
    label: (status.mode_label || '').trim() || mode,
    detail: meaning ?? mode
  }
}

/**
 * Обмен наработками с другими копиями.
 *
 * ⚠️ В локальном режиме сети нет — это говорится прямо (тон «внимание» и
 * причина от моста), а не прячется за пустым списком.
 */
export function exchangeView(status: CloneStatus | null): StatusView {
  if (!status) {
    return {
      tone: 'bad',
      label: 'состояние неизвестно',
      detail: 'мост не ответил — состояние обмена неизвестно'
    }
  }
  const exchange = status.exchange
  if (!exchange) {
    return {
      tone: 'bad',
      label: 'состояние неизвестно',
      detail: 'мост не привёл состояние обмена'
    }
  }
  const why = (exchange.why || '').trim()
  if (exchange.available === true) {
    return {
      tone: 'ok',
      label: 'обмен доступен',
      detail: why || 'обмен идёт через пул наработок'
    }
  }
  return {
    tone: 'warn',
    label: 'обмен недоступен',
    detail: why || 'причина неизвестна'
  }
}

/**
 * Строки состояния копии для настроек: режим, обмен, локальный
 * репозиторий, родословная (идентификатор копии).
 */
export function modeRows(status: CloneStatus | null): StatusView[] {
  if (!status) {
    return [
      { tone: 'bad', label: 'Режим', detail: 'мост не ответил — режим неизвестен' },
      {
        tone: 'bad',
        label: 'Обмен наработками',
        detail: 'мост не ответил — состояние обмена неизвестно'
      },
      {
        tone: 'bad',
        label: 'Локальный репозиторий',
        detail: 'мост не ответил — путь неизвестен'
      },
      {
        tone: 'bad',
        label: 'Родословная',
        detail: 'мост не ответил — идентификатор копии неизвестен'
      }
    ]
  }

  const mode = modeView(status)
  const exchange = exchangeView(status)
  const local = status.local_repo
  const localPath = (local?.path || '').trim()
  const localParts: string[] = []
  if (localPath) localParts.push(localPath)
  if (local?.exists === true) {
    if (local.commit) localParts.push(`коммит ${local.commit}`)
    if (local.branch) localParts.push(`ветка ${local.branch}`)
  } else if (localPath) {
    localParts.push('репозитория нет')
  }
  const instance = (status.instance_id || '').trim()

  return [
    {
      tone: mode.tone,
      label: 'Режим',
      detail: mode.tone === 'ok' ? mode.label : mode.detail
    },
    {
      tone: exchange.tone,
      label: 'Обмен наработками',
      detail: `${exchange.label}: ${exchange.detail}`
    },
    {
      tone: local?.exists === true ? 'ok' : localPath ? 'warn' : 'bad',
      label: 'Локальный репозиторий',
      detail: localParts.length ? localParts.join(' · ') : DASH
    },
    {
      tone: instance ? 'ok' : 'warn',
      label: 'Родословная',
      detail: instance || DASH
    }
  ]
}

/**
 * Вопрос первого запуска: репозиторий, папка копии, что это даёт, что будет
 * при отказе и кто решает.
 *
 * ⚠️ `asked` — «вопрос уже задан», а не «снимок пришёл»: при отсутствии
 * снимка ответа нет вообще, и окно говорит «мост не ответил — вопрос не
 * задан», а не показывает пустой вопрос.
 */
export function firstRunView(snapshot: CopyFirstRunSnapshot | null): {
  asked: boolean
  title: string
  repository: string
  folder: string
  gives: string[]
  ifRefuse: string[]
  who: string
  modes: CopyModeOption[]
} {
  const offer = snapshot?.offer
  return {
    asked: snapshot?.first_run?.asked === true,
    title: (offer?.title || '').trim() || DASH,
    repository: (offer?.repository || '').trim() || DASH,
    folder: (offer?.folder || '').trim() || DASH,
    gives: (offer?.gives ?? []).map((item) => (item || '').trim()).filter(Boolean),
    ifRefuse: (offer?.if_refuse ?? []).map((item) => (item || '').trim()).filter(Boolean),
    who: (offer?.who || '').trim() || DASH,
    modes: offer?.modes ?? []
  }
}

/**
 * Подсказки наработок — строки таблицы: ген, название, автор, почему
 * подходит, файлы и дата. Пустое поле — «—», а не догадка.
 */
export function suggestionRows(data: GeneSuggestions | null): SuggestionRow[] {
  if (!data) return []
  return (data.suggestions ?? []).map((item) => ({
    gene: (item.id || '').trim() || DASH,
    title: (item.title || '').trim() || DASH,
    author: (item.author || '').trim() || DASH,
    why: (item.why || '').trim() || DASH,
    fits: item.fits === true,
    files: (item.files ?? []).filter(Boolean),
    created: formatDateTime(item.created_at) || DASH
  }))
}

/**
 * Подтверждение внедрения чужой наработки.
 *
 * ⚠️ Чужое попадает в программу только через ветку и PR, а решение — за
 * владельцем: окно обязано сказать это до кнопки, а не после.
 */
export function adoptConfirmText(row: SuggestionRow): string {
  const files = row.files.length ? row.files.join(', ') : DASH
  return (
    `Внедрить наработку «${row.title}» (ген ${row.gene}) от ${row.author}?\n\n` +
    `Файлы: ${files}\n` +
    `Почему подходит: ${row.why}\n\n` +
    'Внедрение идёт через ветку и PR в репозиторий программы: сначала ветка и ' +
    'проверки, потом PR.\n' +
    'Решение за вами: без вашего решения в программу ничего не попадёт.\n\n' +
    'Продолжить?'
  )
}

/**
 * Подтверждение смены режима: название режима и что именно изменится.
 */
export function modeConfirmText(mode: CopyModeOption): string {
  const label = (mode.label || '').trim() || (mode.id || DASH)
  const what =
    MODE_CHANGE[mode.id] || (mode.hint || '').trim() || 'изменение не описано мостом'
  return (
    `Перевести копию «МиниГена» в режим «${label}»?\n\n` +
    `${what}\n\n` +
    'Продолжить?'
  )
}

/**
 * Подтверждение ответа на вопрос первого запуска.
 *
 * Согласие — регистрация папки копии в репозитории программы; отказ — копия
 * остаётся локальной и программа при этом не ломается.
 */
export function answerConfirmText(agree: boolean, offer: CopyOffer): string {
  const repository = (offer?.repository || '').trim() || DASH
  const folder = (offer?.folder || '').trim() || DASH
  const who = (offer?.who || '').trim()
  const bullets = (items: string[]): string =>
    items.length ? items.map((item) => `• ${item}`).join('\n') : `• ${DASH}`

  if (agree) {
    const gives = (offer?.gives ?? []).map((item) => (item || '').trim()).filter(Boolean)
    const list = gives.length
      ? gives
      : [
          'правки программы и PR в общий репозиторий программы',
          'обмен наработками с другими копиями (пул генов)'
        ]
    return (
      'Зарегистрировать папку копии в репозитории программы?\n\n' +
      `Репозиторий: ${repository}\n` +
      `Папка копии: ${folder}\n\n` +
      'Что это даёт:\n' +
      `${bullets(list)}\n\n` +
      (who ? `Решение за вами: ${who}\n\n` : '') +
      'После согласия правки и PR идут в общий репозиторий программы, ' +
      'обмен наработками доступен.\n\n' +
      'Продолжить?'
    )
  }

  const ifRefuse = (offer?.if_refuse ?? []).map((item) => (item || '').trim()).filter(Boolean)
  const list = ifRefuse.length
    ? ifRefuse
    : ['программа работает как обычно, правки идут в локальный git-репозиторий']
  return (
    'Отказаться от регистрации папки копии в репозитории программы?\n\n' +
    `Репозиторий: ${repository}\n` +
    `Папка копии: ${folder}\n\n` +
    'Если отказаться:\n' +
    `${bullets(list)}\n\n` +
    'Отказ не ломает программу: правки идут в локальный репозиторий на этой ' +
    'машине, PR в общий репозиторий не открываются, обмен с другими копиями ' +
    'недоступен.\n\n' +
    'Продолжить?'
  )
}
