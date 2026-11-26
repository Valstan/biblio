/**
 * «Черенки» — вкладка про **размножение** «МиниГена»: как поставить копию
 * другому человеку и как передать ему наработки (гены).
 *
 * ⚠️ Заказ владельца (26.11): «сделай в интерфейсе МиниГена ссылку на
 * инструкцию установки… не могу понять что мне отправить знакомому, чтобы он
 * у себя установил миниген тоже». Значит, вкладка должна отвечать на вопрос
 * «что отправить», а не просто показывать файл.
 *
 * Логика вкладки — **чистые функции** в этом модуле: их проверяет тест
 * (как `copy-view`, `update-view`, `gene-map`). Окно только рисует.
 */
import type { InstallGuide } from '@shared/types'

/** Что нужно знакомому на машине (то же, что в инструкке). */
export const REQUIREMENTS: string[] = [
  'Windows 10/11 x64',
  'Node.js 18+',
  'Python 3.10+',
  'Git и GitHub CLI (gh)',
  'свой вход в GitHub: один раз `gh auth login`'
]

/**
 * Что отправить знакомому — короткий список.
 *
 * ⚠️ Список собирается из **живой** инструкции (путь и адрес — оттуда), а не
 * выдумывается: инструкция может лежать в другом месте, и отправлять надо то,
 * что есть на машине.
 */
export function sendChecklist(guide: InstallGuide | null): string[] {
  if (!guide || !guide.ok) {
    return [
      'Инструкции на машине нет — сначала найдите её (кнопка ниже скажет, где искать).'
    ]
  }
  const items: string[] = []
  items.push(`Отправить инструкцию: ${guide.path}`)
  if (guide.repo) {
    items.push(`Сказать, откуда взять программу: ${guide.repo}`)
  }
  if (guide.installer) {
    items.push(`Установщик на этой машине: ${guide.installer} (он в папке исходников)`)
  }
  items.push('Сказать, что нужно на машине знакомого: ' + REQUIREMENTS.join(', '))
  items.push('Сказать, что делать у него: склонировать репозиторий и запустить install.ps1')
  return items
}

/** Один раздел инструкции: заголовок и строки под ним. */
export interface GuideSection {
  heading: string
  lines: string[]
}

/**
 * Разделы инструкции — чтобы показать её в окне **читаемо**.
 *
 * ⚠️ Разметку не разбираем полностью (это не markdown-рендерер): нам нужны
 * заголовки и строки под ними, чтобы человек прочитал инструкцию прямо в
 * окне, не открывая файл.
 */
export function guideSections(text: string): GuideSection[] {
  const out: GuideSection[] = []
  let current: GuideSection | null = null
  let inCode = false
  for (const raw of (text || '').split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (line.trim().startsWith('```')) {
      inCode = !inCode
      if (!current) current = { heading: '', lines: [] }
      current.lines.push(line)
      continue
    }
    if (!inCode && line.startsWith('## ')) {
      if (current) out.push(current)
      current = { heading: line.slice(3).trim(), lines: [] }
      continue
    }
    if (!current) {
      if (!line.trim() || line.startsWith('# ')) continue
      current = { heading: '', lines: [] }
    }
    current.lines.push(line)
  }
  if (current) out.push(current)
  return out
}

/**
 * Строка инструкции — **для чтения глазами**: без маркеров разметки.
 *
 * ⚠️ Разметку не разбираем полностью: окно показывает инструкцию как есть,
 * но без `**` и кавычек-кода — так её читать спокойнее.
 */
export function plainLine(line: string): string {
  return line.replace(/\*\*/g, '').replace(/`/g, '')
}

/** Шаг передачи генов: заголовок, что делает и чем кончается. */
export interface GeneStep {
  key: string
  title: string
  detail: string
  /** тон сообщения: '' — обычное, 'warn' — с оговоркой */
  tone: '' | 'warn'
}

/**
 * Как передать наработки другому человеку.
 *
 * ⚠️ Обмен через пул зависит от режима копии: в режиме «только локально» его
 * нет — и это сказано прямо, а не спрятано за пустым списком (правило
 * вкладки «Гены»).
 */
export function geneSteps(exchangeAvailable: boolean, exchangeWhy: string): GeneStep[] {
  const steps: GeneStep[] = [
    {
      key: 'publish',
      title: 'Опубликовать наработку в пул',
      detail:
        'Публикуется текущая правка (ветка + правка в коде). Её увидят другие ' +
        'копии и смогут внедрить — через ветку и PR, решение остаётся за владельцем.',
      tone: ''
    },
    {
      key: 'export',
      title: 'Выгрузить папку наработок',
      detail:
        'Папка с наработками — её можно отнести соседней копии на флешке. ' +
        'Работает и там, где пул недоступен.',
      tone: ''
    },
    {
      key: 'import',
      title: 'Внести чужую папку',
      detail: 'Внести папку, которую принёс сосед: наработки станут видны в пуле.',
      tone: ''
    },
    {
      key: 'pool',
      title: 'Взять чужую наработку',
      detail:
        'Вкладка «Гены»: карта копий и наработки других копий. Подходящие можно ' +
        'внедрить одной кнопкой.',
      tone: ''
    }
  ]
  if (!exchangeAvailable) {
    steps.push({
      key: 'note',
      title: 'Обмен через пул недоступен',
      detail:
        exchangeWhy ||
        'Копия работает локально: пула нет, обмен идёт папками (выгрузить/внести).',
      tone: 'warn'
    })
  }
  return steps
}

/** Что важно помнить про вторую копию — коротко и по делу. */
export const COPY_RULES: string[] = [
  'Вторая копия — это **отдельная машина**: программа занимает фиксированный порт ' +
    'и фиксированные папки, две копии на одной машине столкнутся.',
  'У каждой установки **своя родословная** (идентификатор `mg-…`) — копия видит себя ' +
    'отдельной веткой на карте.',
  'Репозиторий своей копии заводит **сама программа** — в аккаунте того, кто её ' +
    'установил. Вручную в GitHub ничего создавать не надо.',
  'Второй копии нужен **свой вход в GitHub** (`gh auth login`) — от имени ' +
    'знакомого.'
]
