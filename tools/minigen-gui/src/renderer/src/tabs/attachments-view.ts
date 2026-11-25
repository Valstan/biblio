/**
 * Логика вложений к чату «Управляющего» — чистая, без React и без сети.
 *
 * Здесь то, что видит владелец: размер файла по-человечески («12 КБ»), вид
 * файла («картинка», «документ», «текст») и подпись вложения целиком; сборка
 * списка вложений без дублей и разбор списка файлов из перетаскивания.
 *
 * Проверяется тестом (`tests/attachments-view.test.ts`): правило владельца —
 * приёмка правилом, а не «на глаз».
 *
 * ⚠️ Окно **не читает диск**: содержимое файла читает мост (промт #90).
 * Здесь только путь, имя и размер — то, что знает оператор.
 */

import type { ChatAttachment } from '@shared/types'

/**
 * Список файлов из окна: `FileList` перетаскивания или обычный массив.
 *
 * Тип свой — чтобы файл считался и без DOM-библиотеки: тесты живут в
 * node-окружении, где `FileList` не объявлен.
 */
export interface FileListLike {
  readonly length: number
  readonly [index: number]: File
}

/** Вид файла, который окно понимает само (по расширению). */
export type AttachmentKind = 'text' | 'image' | 'document' | 'unknown'

/** Картинки — расширения, которые мост отдаёт модели картинкой. */
const IMAGE_EXT = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'])

/** Документы — расширения, из которых мост вытаскивает текст. */
const DOC_EXT = new Set([
  '.xlsx', '.xlsm', '.xltx', '.docx', '.docm', '.dotx', '.pptx', '.pptm',
  '.potx', '.odt', '.ods', '.odp', '.odg', '.pdf', '.rtf', '.doc', '.xls', '.ppt'
])

/** Текст и код — расширения, которые мост читает как есть. */
const TEXT_EXT = new Set([
  '.txt', '.md', '.markdown', '.rst', '.csv', '.tsv', '.json', '.jsonl',
  '.xml', '.yaml', '.yml', '.ini', '.cfg', '.conf', '.toml', '.log', '.htm',
  '.html', '.css', '.scss', '.js', '.jsx', '.mjs', '.ts', '.tsx', '.py',
  '.rb', '.go', '.rs', '.java', '.c', '.h', '.cpp', '.cs', '.php', '.sh',
  '.bash', '.ps1', '.bat', '.cmd', '.sql', '.diff', '.patch', '.srt', '.vtt',
  '.tex', '.env'
])

/**
 * Вид файла по расширению — как его понимает мост.
 *
 * ⚠️ Это **подсказка окна**: читает файл мост и в истории лежит его настоящий
 * вид. Неизвестное расширение — «неизвестно», а не выдуманный вид.
 */
export function detectKind(name: string): AttachmentKind {
  const suffix = extensionOf(name)
  if (IMAGE_EXT.has(suffix)) return 'image'
  if (DOC_EXT.has(suffix)) return 'document'
  if (TEXT_EXT.has(suffix)) return 'text'
  return 'unknown'
}

/**
 * Размер по-человечески: «0 Б», «512 Б», «2 КБ», «5 МБ».
 *
 * Целые значения — без дробной части («2 КБ», а не «2.0 КБ»).
 */
export function formatSize(bytes: number): string {
  const value = Number.isFinite(bytes) && bytes > 0 ? bytes : 0
  if (value < 1024) return `${Math.round(value)} Б`
  const kb = value / 1024
  if (kb < 1024) return `${pretty(kb)} КБ`
  const mb = kb / 1024
  if (mb < 1024) return `${pretty(mb)} МБ`
  return `${pretty(mb / 1024)} ГБ`
}

/** Подпись вида файла: «текст», «картинка», «документ» — иначе «файл». */
export function attachmentKindLabel(kind: string): string {
  switch ((kind ?? '').trim()) {
    case 'text':
      return 'текст'
    case 'image':
      return 'картинка'
    case 'document':
      return 'документ'
    default:
      return 'файл'
  }
}

/**
 * Подпись вложения целиком: «схема.png — картинка, 12 КБ».
 *
 * ⚠️ Вид показываем **только если он известен** (задан и не «unknown»):
 * иначе — только имя и размер. Правило одно и для свежих реплик, и для
 * восстановленных из истории.
 */
export function attachmentLabel(attachment: ChatAttachment): string {
  const name = (attachment?.name ?? '').trim() || 'файл'
  const size = formatSize(attachment?.size ?? 0)
  const kind = (attachment?.kind ?? '').trim()
  if (!kind || kind === 'unknown') return `${name} — ${size}`
  return `${name} — ${attachmentKindLabel(kind)}, ${size}`
}

/**
 * Дописать вложения к списку: без дублей (по пути на машине), порядок
 * прежний, новые — в конце.
 */
export function mergeAttachments(
  current: ChatAttachment[],
  added: ChatAttachment[]
): ChatAttachment[] {
  const out = [...(current ?? [])]
  const seen = new Set(out.map(attachmentKey))
  for (const item of added ?? []) {
    if (!item) continue
    const key = attachmentKey(item)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }
  return out
}

/**
 * Видимая часть вложения: имя, вид и размер — **без пути** на машине.
 *
 * Путь нужен мосту при отправке, но в переписке (и в файле истории) ему
 * не место.
 */
export function visibleAttachments(list: ChatAttachment[]): ChatAttachment[] {
  return (list ?? []).map((item) => ({
    name: item.name,
    kind: item.kind,
    size: item.size
  }))
}

/**
 * Файлы из перетаскивания — обычным массивом.
 *
 * `null`/`undefined` — пустой массив (перетаскивания не было).
 */
export function collectFiles(list: FileListLike | null | undefined): File[] {
  if (!list) return []
  const out: File[] = []
  const length = typeof list.length === 'number' ? list.length : 0
  for (let index = 0; index < length; index += 1) {
    const file = list[index]
    if (file) out.push(file)
  }
  return out
}

/** Ключ файла: путь на машине (пути нет — имя). */
function attachmentKey(item: ChatAttachment): string {
  const path = (item?.path ?? '').trim()
  return path || (item?.name ?? '').trim()
}

/** Расширение файла в нижнем регистре (с точкой; '' — расширения нет). */
function extensionOf(name: string): string {
  const base = (name ?? '').trim().split(/[\\/]/).pop() ?? ''
  const dot = base.lastIndexOf('.')
  if (dot <= 0) return ''
  return base.slice(dot).toLowerCase()
}

/** Число с одной десятой — но без дробной части, если она нулевая. */
function pretty(value: number): string {
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}
