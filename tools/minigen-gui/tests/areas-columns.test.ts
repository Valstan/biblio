import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'
import { AREA_COLUMNS } from '../src/renderer/src/tabs/areas-sort'
import { MODE_BUTTONS } from '../src/renderer/src/tabs/areas-mode'
import { DRIVER_LABELS, DRIVER_VALUES } from '../src/renderer/src/tabs/driver-view'

/**
 * Сторож на переключатели в списке областей (промт КАРМАНА #177).
 *
 * Падает, если:
 * - колонка «Водитель» снова уедет в конец (мимо «Режима»);
 * - «Режим» станет одной кнопкой с меняющейся надписью;
 * - набор значений водителя разойдётся с ядром (окно и ядро — разные словари).
 *
 * Ядро читается прямо из репозитория (`minigen_core.py`) — так словарь
 * сверяется с тем, что реально поедет на машину.
 */

const src = join(__dirname, '../src')
const tabs = join(src, 'renderer', 'src', 'tabs')
const core = readFileSync(
  join(__dirname, '../../minimax-project-areas/minigen_core.py'),
  'utf8'
)
const tab = readFileSync(join(tabs, 'AreasTab.tsx'), 'utf8')
const styles = readFileSync(join(src, 'renderer', 'src', 'styles.css'), 'utf8')

/** Значения водителя в ядре — из исходника, а не из догадки. */
function coreDriverValues(): string[] {
  const names = core.match(/DRIVER_SWITCH_VALUES\s*=\s*\(([^)]*)\)/)
  const out: string[] = []
  for (const name of (names?.[1] ?? '').split(',')) {
    const key = name.trim()
    if (!key) continue
    const value = core.match(new RegExp(`^${key}\\s*=\\s*"([^"]+)"`, 'm'))
    if (value) out.push(value[1])
  }
  return out
}

/** Значения констант ядра (`DRIVER_MANAGER = "manager"`). */
function coreConstants(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of core.matchAll(/^(DRIVER_\w+)\s*=\s*"([^"]+)"/gm)) {
    out[m[1]] = m[2]
  }
  return out
}

/** Подписи водителя в ядре — из исходника. */
function coreDriverTitles(): Record<string, string> {
  const block = core.match(/DRIVER_TITLES\s*=\s*\{([\s\S]*?)\n\}/)
  const names = coreConstants()
  const out: Record<string, string> = {}
  for (const line of (block?.[1] ?? '').split('\n')) {
    const m = line.match(/^\s*(\w+):\s*"([^"]*)"/)
    if (!m) continue
    // в ядре ключ — имя константы («DRIVER_MANAGER»), а нам нужно значение
    out[names[m[1]] ?? m[1]] = m[2]
  }
  return out
}

describe('сторож на переключатели списка областей (#177)', () => {
  it('колонка «Водитель» стоит вплотную за «Режимом»', () => {
    const labels = AREA_COLUMNS.map((item) => item.label)
    expect(labels.indexOf('Водитель')).toBe(labels.indexOf('Режим') + 1)
    expect(labels.indexOf('Водитель')).toBeLessThan(labels.indexOf('Сессии'))
  })

  it('ячейки строки идут по тому же списку, что и шапки', () => {
    // Иначе колонка «Водитель» может снова уехать в конец таблицы.
    expect(tab).toContain('AREA_COLUMNS.map((column) => (')
  })

  it('«Режим» — две кнопки, обе подписи всегда на виду', () => {
    expect(MODE_BUTTONS.map((item) => item.label)).toEqual(['Автомат', 'Вручную'])
    expect(MODE_BUTTONS.length).toBe(2)
    expect(tab).toContain('MODE_BUTTONS.map')
    // Одна кнопка с меняющейся надписью — больше не годится.
    expect(tab).not.toContain("area.autoheal ? 'Автомат' : 'Вручную'")
  })

  it('колонка «Водитель» узкая, а строка «сейчас: …» её не раздувает', () => {
    expect(styles).toContain('td.col-driver')
    expect(styles).toContain('max-width')
    expect(styles).toContain('.driver-now')
    expect(styles).toContain('text-overflow: ellipsis')
  })

  it('в окне ровно три варианта водителя — и те же, что в ядре', () => {
    expect([...DRIVER_VALUES]).toHaveLength(3)
    expect([...DRIVER_VALUES]).toEqual(coreDriverValues())
  })

  it('слова водителя в окне и в ядре совпадают', () => {
    const titles = coreDriverTitles()
    for (const value of DRIVER_VALUES) {
      expect(DRIVER_LABELS[value]).toBe(titles[value])
    }
    expect(DRIVER_LABELS.manager).toBe('Внешнее управление')
    expect(DRIVER_LABELS.automaton).toBe('Управление МиниГеном')
    expect(DRIVER_LABELS.none).toBe('Оператор')
  })

  it('«по правилу» — не пункт переключателя (владелец просил три)', () => {
    expect([...DRIVER_VALUES]).not.toContain('auto')
    expect(Object.keys(DRIVER_LABELS)).not.toContain('auto')
  })
})
