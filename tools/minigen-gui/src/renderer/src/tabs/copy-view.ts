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
  GeneRating,
  GeneSuggestions
} from '@shared/types'
import { formatDateTime } from '../time'

export type Tone = 'ok' | 'warn' | 'bad'

/** Шкала оценок: звёздочки от 1 до 10 (заказ владельца 28.11). */
export const STAR_MAX = 10

/** Строка состояния: тон, короткая подпись и подробности (почему так). */
export interface StatusView {
  tone: Tone
  label: string
  detail: string
}

/**
 * Рейтинг гена — то, что видит владелец **до** решения «ставить или нет».
 *
 * ⚠️ Показываем **и** среднюю оценку, **и** число голосов: у одного гена
 * голосов может быть много, у другого — один, и решение без числа голосов
 * было бы половиной правды (промт КАРМАНА #187).
 */
export interface RatingView {
  /** средняя оценка с одним знаком: «8,3» (пусто — голосов нет) */
  score: string
  /** число голосов */
  votes: number
  /** «★★★★☆» — десять позиций, закрашено по средней */
  stars: string
  /** текст для окна: «8,3 ★★★★☆ — голосов: 5» */
  text: string
  /** моя оценка (0 — не голосовал) */
  mine: number
  /** есть ли голоса вообще */
  has: boolean
}

/** Рейтинг гена для окна: пусто, ноль и «нет данных» — разные вещи. */
export function ratingView(rating: GeneRating | null | undefined): RatingView {
  const votes = Math.max(0, Math.trunc(Number(rating?.votes ?? 0)))
  const average = Number(rating?.average ?? 0)
  const mine = Math.trunc(Number(rating?.my_vote ?? 0))
  if (!votes) {
    return { score: DASH, votes: 0, stars: '', text: 'оценок нет', mine: 0, has: false }
  }
  const filled = Math.min(STAR_MAX, Math.max(0, Math.round(average)))
  const stars = '★'.repeat(filled) + '☆'.repeat(STAR_MAX - filled)
  const score = average.toFixed(1).replace('.', ',')
  return {
    score,
    votes,
    stars,
    text: `${score} ${stars} — голосов: ${votes}`,
    mine,
    has: true
  }
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
  /** рейтинг гена: голоса и средняя оценка */
  rating: RatingView
  /** трогает ли ген внешний вид программы */
  touchesAppearance: boolean
  /** какие файлы трогают внешний вид */
  appearanceFiles: string[]
  /** откатывали ли этот ген в другой копии */
  rolledBackElsewhere: boolean
}

/**
 * Ген, который эта копия не ставит (откатывала или пометила «не ставить»).
 *
 * ⚠️ Такие гены **не попадают** в список чужих наработок — они видны
 * отдельной строкой с пометкой (промт КАРМАНА #187).
 */
export interface NotWantedRow {
  gene: string
  title: string
  author: string
  /** почему не ставим */
  why: string
  /** откатывали ли (а не просто пометили) */
  rolledBack: boolean
  rating: RatingView
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
    created: formatDateTime(item.created_at) || DASH,
    rating: ratingView(item.rating),
    touchesAppearance: item.touches_appearance === true,
    appearanceFiles: (item.appearance_files ?? []).filter(Boolean),
    rolledBackElsewhere: item.rolled_back_elsewhere === true
  }))
}

/**
 * Строки «не ставим»: откатанные и помеченные «не ставить».
 *
 * ⚠️ Отдельным списком, а не внутри чужих наработок: хозяйка копии не
 * должна снова предлагать то, что уже откатывали.
 */
export function notWantedRows(data: GeneSuggestions | null): NotWantedRow[] {
  if (!data) return []
  return (data.not_wanted ?? []).map((item) => {
    const why = (item.why || '').trim() || DASH
    return {
      gene: (item.id || '').trim() || DASH,
      title: (item.title || '').trim() || DASH,
      author: (item.author || '').trim() || DASH,
      why,
      rolledBack: why.includes('откатывали'),
      rating: ratingView(item.rating),
      created: formatDateTime(item.created_at) || DASH
    }
  })
}

/**
 * Подтверждение внедрения чужой наработки.
 *
 * ⚠️ Чужое попадает в программу только через ветку и PR, а решение — за
 * владельцем: окно обязано сказать это до кнопки, а не после. И отдельно —
 * **рейтинг** и **«меняет внешний вид»**: по решению владельца он должен
 * видеть их **до** кнопки «Внедрить» (промт КАРМАНА #187).
 */
export function adoptConfirmText(row: SuggestionRow): string {
  const files = row.files.length ? row.files.join(', ') : DASH
  const rating = row.rating.has ? `Рейтинг: ${row.rating.text}\n` : 'Рейтинг: оценок нет\n'
  const appearance = row.touchesAppearance
    ? `\n⚠️ Ген меняет внешний вид программы (${row.appearanceFiles.join(', ') || DASH}).` +
      ' Без вашего явного решения я его не внедрю.\n'
    : ''
  const elsewhere = row.rolledBackElsewhere
    ? '\nЭтот ген в другой копии уже откатывали — посмотрите внимательно.\n'
    : ''
  return (
    `Внедрить наработку «${row.title}» (ген ${row.gene}) от ${row.author}?\n\n` +
    `Файлы: ${files}\n` +
    `Почему подходит: ${row.why}\n` +
    rating +
    appearance +
    elsewhere +
    '\nВнедрение идёт через ветку и PR в репозиторий программы: сначала ветка и ' +
    'проверки, потом PR.\n' +
    'Решение за вами: без вашего решения в программу ничего не попадёт.\n\n' +
    'Продолжить?'
  )
}

/**
 * Подтверждение отката гена.
 *
 * ⚠️ Откат меняет код программы ⇒ решение остаётся за владельцем, и окно
 * говорит прямо, что произойдёт: состояние вернётся к тому, что было до
 * внедрения, а ген будет помечен «не ставить».
 */
export function rollbackConfirmText(gene: string, title?: string): string {
  const name = (title || '').trim() || (gene || '').trim() || DASH
  return (
    `Откатить ген «${name}»?\n\n` +
    'Откат отменит коммит гена в ветке, где он стоит, и вернёт состояние ' +
    'программы к тому, что было до внедрения.\n' +
    'Ген будет помечен «не ставить» и больше не предложится.\n\n' +
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

/**
 * Режим «папка в общем репозитории» — как его предлагает окно, когда обмен
 * недоступен из-за локального режима.
 *
 * ⚠️ Значение — по заказу владельца 25.11 (промт #131), а не выдумка окна:
 * это тот же режим, что и в настройках, только поданный как предложение.
 */
export const SHARED_MODE_OPTION: CopyModeOption = {
  id: 'shared',
  label: 'Папка в общем репозитории',
  hint: 'Правки и PR идут прямо в общий репозиторий программы. Обмен наработками доступен.'
}

/**
 * Что окно предлагает, когда обмен наработками недоступен из-за локального
 * режима (план промта #131, пункт 6: «предлагать включить режим с
 * репозиторием»).
 *
 * ⚠️ Предлагать надо **тогда, когда это правда**: локальный режим — единственный
 * случай, где недоступность обмена можно снять сменой режима. Если обмен не
 * доступен по другой причине (пул не взят), смена режима не поможет — значит,
 * и предлагать нечего.
 */
export function localOfferView(status: CloneStatus | null): {
  local: boolean
  text: string
} {
  const local = (status?.mode || '').trim() === 'local'
  if (!local) {
    return { local: false, text: '' }
  }
  const why = (status?.exchange?.why || '').trim()
  return {
    local: true,
    text:
      'Обмен с другими копиями недоступен: локальный режим, сети нет. ' +
      'Можно перевести копию в режим «папка в общем репозитории» — тогда ' +
      'обмен и PR заработают.' +
      (why ? ` Почему сейчас нет: ${why}` : '')
  }
}

/**
 * Новые наработки других копий — сколько пришло с прошлого раза.
 *
 * ⚠️ «Новые» — те, которых копия ещё не видела (список виденного окно
 * помнит на машине). Это честный признак «появились у соседей», а не
 * «все наработки вообще»: показывать всё подряд — шум, а владелец шум не
 * читает.
 */
export interface NewWorksView {
  /** сколько новых наработок */
  count: number
  /** их идентификаторы (для показа) */
  genes: string[]
  /** сколько из них совпадает с текущей работой копии */
  fits: number
  /** текст уведомления */
  text: string
}

/**
 * Сколько новых наработок — по-русски: «найдена 1 новая наработка»,
 * «найдено 2 новые наработки», «найдено 5 новых наработок».
 *
 * ⚠️ Форму стоит делать правильно: окно говорит с владельцем, и «найдено 1
 * наработка» — неграмотно. Правило — как в `chat-view` (1 / 2-4 / 5+).
 */
export function newWorksPhrase(count: number): string {
  if (count === 1) return 'найдена 1 новая наработка'
  const mod100 = count % 100
  const mod10 = count % 10
  if (mod100 >= 11 && mod100 <= 14) return `найдено ${count} новых наработок`
  if (mod10 >= 2 && mod10 <= 4) return `найдено ${count} новые наработки`
  return `найдено ${count} новых наработок`
}

/** «1 совпадает» / «2 совпадают» — та же форма, но про совпадение. */
function fitsPhrase(count: number): string {
  const mod100 = count % 100
  const mod10 = count % 10
  if (mod100 >= 11 && mod100 <= 14) return `${count} совпадают`
  if (mod10 === 1) return `${count} совпадает`
  return `${count} совпадают`
}

export function newWorksView(
  data: GeneSuggestions | null,
  seen: readonly string[]
): NewWorksView {
  const known = new Set(
    (seen ?? []).map((item) => (item || '').trim()).filter(Boolean)
  )
  const items = data?.suggestions ?? []
  const fresh = items.filter((item) => {
    const id = (item?.id || '').trim()
    return Boolean(id) && !known.has(id)
  })
  const fits = fresh.filter((item) => item.fits === true).length
  const count = fresh.length
  const text = count
    ? `${newWorksPhrase(count)} от других копий` +
      (fits ? ` (${fitsPhrase(fits)} с вашей работой)` : '') +
      ' — показать?'
    : ''
  return {
    count,
    genes: fresh.map((item) => (item.id || '').trim()),
    fits,
    text
  }
}
