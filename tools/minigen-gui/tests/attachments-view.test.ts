import { describe, expect, it } from 'vitest'
import type { ChatAttachment } from '../src/shared/types'
import {
  attachmentKindLabel,
  attachmentLabel,
  collectFiles,
  detectKind,
  formatSize,
  mergeAttachments,
  visibleAttachments
} from '../src/renderer/src/tabs/attachments-view'

/**
 * Логика вложений к чату «Управляющего» (промт #90, заказ владельца 24.11).
 *
 * Проверяем то, что видит владелец: размер по-человечески, вид файла и
 * подпись вложения целиком; список вложений без дублей; разбор файлов из
 * перетаскивания.
 */

function attachment(over: Partial<ChatAttachment> = {}): ChatAttachment {
  return {
    name: 'файл.txt',
    kind: 'text',
    size: 1024,
    ...over
  }
}

describe('размер файла по-человечески', () => {
  it('пустой файл — «0 Б»', () => {
    expect(formatSize(0)).toBe('0 Б')
  })

  it('мелкий файл — в байтах', () => {
    expect(formatSize(512)).toBe('512 Б')
  })

  it('килобайты — целыми, без дробной части', () => {
    expect(formatSize(2048)).toBe('2 КБ')
  })

  it('мегабайты — целыми, без дробной части', () => {
    expect(formatSize(5 * 1024 * 1024)).toBe('5 МБ')
  })

  it('некруглый размер — с одной десятой', () => {
    expect(formatSize(1536)).toBe('1.5 КБ')
  })
})

describe('подпись вида файла', () => {
  it('известные виды — по-русски', () => {
    expect(attachmentKindLabel('text')).toBe('текст')
    expect(attachmentKindLabel('image')).toBe('картинка')
    expect(attachmentKindLabel('document')).toBe('документ')
  })

  it('неизвестный и пустой вид — «файл», а не выдумка', () => {
    expect(attachmentKindLabel('unknown')).toBe('файл')
    expect(attachmentKindLabel('')).toBe('файл')
    expect(attachmentKindLabel('video')).toBe('файл')
  })
})

describe('подпись вложения', () => {
  it('известный вид — с ним: «схема.png — картинка, 12 КБ»', () => {
    const label = attachmentLabel({
      name: 'схема.png',
      kind: 'image',
      size: 12 * 1024
    })
    expect(label).toBe('схема.png — картинка, 12 КБ')
  })

  it('неизвестный вид — только имя и размер', () => {
    expect(attachmentLabel({ name: 'схема.png', kind: 'unknown', size: 12 * 1024 })).toBe(
      'схема.png — 12 КБ'
    )
    expect(attachmentLabel({ name: 'схема.png', kind: '', size: 12 * 1024 })).toBe(
      'схема.png — 12 КБ'
    )
  })
})

describe('слияние списка вложений', () => {
  it('дубль по пути не повторяется', () => {
    const current = [attachment({ name: 'a.txt', path: 'C:\\files\\a.txt' })]
    const added = [attachment({ name: 'a.txt', path: 'C:\\files\\a.txt' })]
    const merged = mergeAttachments(current, added)
    expect(merged).toHaveLength(1)
    expect(merged[0].path).toBe('C:\\files\\a.txt')
  })

  it('порядок сохранён: прежние — первы, новые — после', () => {
    const current = [
      attachment({ name: 'a.txt', path: 'C:\\files\\a.txt' }),
      attachment({ name: 'b.txt', path: 'C:\\files\\b.txt' })
    ]
    const added = [
      attachment({ name: 'c.txt', path: 'C:\\files\\c.txt' }),
      attachment({ name: 'a.txt', path: 'C:\\files\\a.txt' })
    ]
    const merged = mergeAttachments(current, added)
    expect(merged.map((item) => item.name)).toEqual(['a.txt', 'b.txt', 'c.txt'])
  })

  it('пустой список ничего не меняет', () => {
    const current = [attachment({ name: 'a.txt', path: 'C:\\files\\a.txt' })]
    expect(mergeAttachments(current, [])).toEqual(current)
    expect(mergeAttachments([], [])).toEqual([])
  })
})

describe('разбор списка файлов', () => {
  it('нет перетаскивания — пустой массив, а не ошибка', () => {
    expect(collectFiles(null)).toEqual([])
    expect(collectFiles(undefined)).toEqual([])
  })

  it('список файлов — обычным массивом', () => {
    const first = new File(['a'], 'a.txt')
    const second = new File(['bb'], 'b.png')
    const files = collectFiles({ length: 2, 0: first, 1: second })
    expect(files).toHaveLength(2)
    expect(files[0].name).toBe('a.txt')
    expect(files[1].name).toBe('b.png')
  })

  it('пустой список — пустой массив', () => {
    expect(collectFiles({ length: 0 })).toEqual([])
  })
})

describe('вид файла по расширению', () => {
  it('известные расширения — как их понимает мост', () => {
    expect(detectKind('схема.png')).toBe('image')
    expect(detectKind('отчёт.DOCX')).toBe('document')
    expect(detectKind('заметка.txt')).toBe('text')
  })

  it('неизвестное расширение — «неизвестно», а не выдумка', () => {
    expect(detectKind('файл.bin')).toBe('unknown')
    expect(detectKind('без_расширения')).toBe('unknown')
  })
})

describe('видимая часть вложения', () => {
  it('путь на машине в переписку не попадает', () => {
    const visible = visibleAttachments([
      attachment({ name: 'a.txt', path: 'C:\\files\\a.txt', size: 10 })
    ])
    expect(visible).toHaveLength(1)
    expect(visible[0].path).toBeUndefined()
    expect(visible[0].name).toBe('a.txt')
    expect(visible[0].size).toBe(10)
  })
})
