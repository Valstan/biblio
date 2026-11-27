import { existsSync, readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'
import { AREA_COLUMNS, sortColumnsHint } from '../src/renderer/src/tabs/areas-sort'
import { DRIVER_BUTTONS, DRIVER_LABELS, DRIVER_VALUES } from '../src/renderer/src/tabs/driver-view'

/**
 * Сторож на переключатели в списке областей (промт КАРМАНА #177; два
 * положения водителя — заказ владельца 27.11; **колонки переименованы и
 * «Режим» убран** — промт КАРМАНА #182).
 *
 * Падает, если:
 * - колонка «Режим» вернётся (или вернётся её модуль с кнопками);
 * - появится путь, отключающий автомат аккаунтов из окна;
 * - колонка «Сессии» (бывшая «Водитель») или «Состояние» (бывшие «Сессии»)
 *   переименуется обратно или съедет из шапки;
 * - «Водитель» снова станет выпадающим меню или разойдётся с ядром (окно и
 *   ядро — разные словари).
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
const api = readFileSync(join(src, 'renderer', 'src', 'api.ts'), 'utf8')
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

describe('сторож на переключатели списка областей (#177, #182)', () => {
  it('колонки — ровно те, что на экране: «Режима» среди них нет', () => {
    expect(AREA_COLUMNS.map((item) => item.label)).toEqual([
      'Область',
      'Работает',
      'Вход',
      'Очки',
      'Сессии',
      'Состояние'
    ])
    expect(AREA_COLUMNS.map((item) => item.label)).not.toContain('Режим')
  })

  it('в окне нет ни колонки «Режим», ни её модуля с кнопками', () => {
    // `String(...)` — чтобы проверка осталась рабочей и после того, как ключ
    // «mode» исчез из типа (иначе TypeScript считает сравнение ошибкой).
    expect(AREA_COLUMNS.some((item) => String(item.key) === 'mode')).toBe(false)
    expect(existsSync(join(tabs, 'areas-mode.ts'))).toBe(false)
    expect(tab).not.toContain('MODE_BUTTONS')
    expect(tab).not.toContain('areas-mode')
    expect(styles).not.toContain('mode-switch')
    expect(styles).not.toContain('mode-btn')
  })

  it('из окна нельзя отключить автомат аккаунтов', () => {
    // Ручного режима больше нет (промт #182): ни кнопки, ни вызова моста.
    expect(api).not.toContain('setAutoheal')
    expect(api).not.toContain('areas.set_autoheal')
    expect(tab).not.toContain('setAutoheal')
  })

  it('колонка «Сессии» (бывшая «Водитель») стоит перед «Состоянием»', () => {
    const labels = AREA_COLUMNS.map((item) => item.label)
    expect(labels.indexOf('Сессии')).toBe(labels.indexOf('Очки') + 1)
    expect(labels.indexOf('Сессии')).toBeLessThan(labels.indexOf('Состояние'))
    // прежние имена на экране больше не появляются
    expect(labels).not.toContain('Водитель')
  })

  it('ячейки строки идут по тому же списку, что и шапки', () => {
    // Иначе колонка «Сессии» может снова уехать в конец таблицы.
    expect(tab).toContain('AREA_COLUMNS.map((column) => (')
  })

  it('подсказка сортировки перечисляет ровно те столбцы, что на экране', () => {
    expect(sortColumnsHint()).toBe(
      AREA_COLUMNS.map((item) => item.label).join(' · ')
    )
    expect(sortColumnsHint()).not.toContain('Режим')
    expect(tab).toContain('sortColumnsHint()')
  })

  it('«Новый вход» — запасной ручной путь, и это сказано в подсказке', () => {
    expect(tab).toContain('Новый вход')
    expect(tab).toContain('Запасной ручной путь')
  })

  it('колонка водителя узкая, а строка «сейчас: …» её не раздувает', () => {
    expect(styles).toContain('td.col-driver')
    expect(styles).toContain('max-width')
    expect(styles).toContain('.driver-now')
    expect(styles).toContain('text-overflow: ellipsis')
  })

  it('«Водитель» — тоже две кнопки, а не выпадающее меню (заказ владельца 27.11)', () => {
    expect(DRIVER_BUTTONS.map((item) => item.label)).toEqual(['Миниген', 'Оператор'])
    expect(DRIVER_BUTTONS.length).toBe(2)
    expect(tab).toContain('DRIVER_BUTTONS.map')
    expect(tab).toContain('driver-btn')
    // Выпадающее меню водителя больше не используется.
    expect(tab).not.toContain('driver-select')
    expect(styles).toContain('.driver-switch')
  })

  it('положения переключателя — те, что понимает ядро', () => {
    expect([...DRIVER_VALUES]).toHaveLength(2)
    for (const value of DRIVER_VALUES) {
      expect(coreDriverValues()).toContain(value)
    }
  })

  it('слова переключателя — владельческие, в его порядке', () => {
    expect(DRIVER_LABELS.automaton).toBe('Миниген')
    expect(DRIVER_LABELS.none).toBe('Оператор')
    // «Внешнее управление» и «Оператор» — одно положение (внешнее управление),
    // и в ядре словарь остался прежним.
    expect(coreDriverTitles().manager).toBe('Внешнее управление')
  })

  it('«по правилу» — не пункт переключателя (владелец просил три)', () => {
    expect([...DRIVER_VALUES]).not.toContain('auto')
    expect(Object.keys(DRIVER_LABELS)).not.toContain('auto')
  })

  it('в отчёте и в журнале водитель остаётся водителем (в мосте, не в окне)', () => {
    // Промт #182, п. 7: переименовываются заголовки колонок на экране; в мосте,
    // в отчёте о лечении и в журнале слово «водитель» остаётся.
    const watcher = readFileSync(
      join(__dirname, '../../minimax-project-areas/minimax-sessionwatch.py'),
      'utf8'
    )
    expect(watcher).toContain('водитель — %s')
    expect(core).toContain('водитель сессий проекта')
  })
})
