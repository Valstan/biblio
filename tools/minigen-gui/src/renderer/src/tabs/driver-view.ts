/**
 * Кто водит сессиями проекта (промт КАРМАНА #174, #177; переключатель — #177).
 *
 * Правило «один водитель на проект»: в каждый момент у проекта один водитель
 * сессий. Переключатель — в списке областей программы «МиниГен» (по
 * области/проекту).
 *
 * ⚠️ ПРОМТ КАРМАНА #177 (зачитка владельца 27.11): в переключателе слова —
 * владельческие, и «по правилу» **уходит из переключателя**: четвёртого пункта
 * там нет (владелец просил без него). В ядре «по правилу» остаётся **правилом
 * по умолчанию** для области, где водитель ещё не выбран явно: «если в области
 * работает Управляющий, водит он, иначе — МиниГен».
 *
 * ⚠️ ЗАКАЗ ВЛАДЕЛЬЦА 27.11 (позже в тот же день): переключатель — **две
 * небольшие кнопки**, как у «Режима» (не выпадающее меню), и **два** положения:
 *
 * - **Миниген** — встроенная в МиниГен нейросеть смотрит на вывод сессии
 *   (последнее слово, выводы «выполнила такую-то работу») и определяет,
 *   завершена ли работа; если завершена — посылает команду завершения, потом
 *   проверяет, что сессия закрылась корректно, архивирует её и поднимает
 *   новую с продолжением нитки разработки;
 * - **Оператор** — управление **внешнее**: оператор или другой агент. Для
 *   МиниГена разницы нет — в обоих случаях программа в сессии не вмешивается.
 *
 * ⇒ «Внешнее управление» и «Оператор» — **одно и то же** положение
 * переключателя (заказ владельца: «внешнее управление это и есть внешнее,
 * оператор или другой агент, без разницы»).
 *
 * В **ядре** значения остались три (`manager` / `automaton` / `none`): `manager` —
 * это ответ **правила** (в области работает Управляющий ⇒ водит он), и правило
 * обязано уметь его назвать. Переключатель пишет `automaton` («Миниген») и
 * `none` («Оператор»).
 *
 * Вынесено в отдельный модуль, чтобы правила проверялись тестом, а не
 * глазами (как сортировка областей и карта генов).
 */

/** Значения, которые понимает **ядро** (`minigen_core.DRIVERS`). */
export const CORE_DRIVER_VALUES = ['manager', 'automaton', 'none'] as const

export type CoreDriverValue = (typeof CORE_DRIVER_VALUES)[number]

/** Значения, которые пишет **переключатель**: ровно два (заказ владельца 27.11). */
export const DRIVER_VALUES = ['automaton', 'none'] as const

export type DriverValue = (typeof DRIVER_VALUES)[number]

/** «По правилу» — правило ядра для необъявленного выбора. Это **не** пункт
 * переключателя: в кнопках его нет (промт КАРМАНА #177, п. 4).
 */
export const DRIVER_RULE = 'auto'

/** Человеческое имя положения переключателя. */
export const DRIVER_LABELS: Record<DriverValue, string> = {
  automaton: 'Миниген',
  none: 'Оператор'
}

/**
 * Что значит каждое положение — словами (подсказка в окне): кто водит сессиями
 * и что делает.
 */
export const DRIVER_HINTS: Record<DriverValue, string> = {
  automaton:
    'Встроенная в МиниГен нейросеть смотрит на вывод сессии и определяет, ' +
    'завершена ли работа; если завершена — закрывает и архивирует сессию и ' +
    'поднимает новую с продолжением нитки',
  none:
    'Управление внешнее: оператор или другой агент. МиниГен в сессии не ' +
    'вмешивается — ни завершает, ни архивирует, ни поднимает новых сессий'
}

/** Кнопка переключателя. */
export interface DriverButton {
  /** Подпись кнопки — видна всегда (как у «Режима»). */
  label: string
  /** Что кнопка включает: значение, которое пишется в конфиг области. */
  value: DriverValue
  /** Подсказка: что значит положение. */
  hint: string
}

/** Ровно две кнопки, в порядке владельца: «Миниген», «Оператор». */
export const DRIVER_BUTTONS: readonly DriverButton[] = DRIVER_VALUES.map((value) => ({
  value,
  label: DRIVER_LABELS[value],
  hint: DRIVER_HINTS[value]
}))

/** Область с переключателем водителя (значение и то, кто водит сейчас). */
export interface DriverArea {
  driver?: string
  driverNow?: {
    driver: string
    title: string
    why: string
    source: string
  }
}

/** Значение из переключателя (пустое — не из трёх). */
export function isDriverValue(value: unknown): value is CoreDriverValue {
  return (CORE_DRIVER_VALUES as readonly string[]).includes(String(value ?? ''))
}

/**
 * Кто водит сессиями области **сейчас** — значение **ядра**.
 *
 * Порядок:
 * 1. в проекте выбрано явно ⇒ берём его;
 * 2. выбора нет (пусто / «по правилу») ⇒ водителя определяет ядро — берём его
 *    ответ (`driverNow`);
 * 3. ядро не ответило (старое ядро на машине) ⇒ пусто: выдумывать нельзя.
 */
export function driverValue(area: DriverArea): CoreDriverValue | '' {
  const raw = String(area.driver ?? '').trim().toLowerCase()
  if (isDriverValue(raw)) return raw
  const now = String(area.driverNow?.driver ?? '').trim().toLowerCase()
  return isDriverValue(now) ? now : ''
}

/**
 * Положение **переключателя** — из двух: «Миниген» или «Оператор».
 *
 * ⚠️ «Внешнее управление» (`manager`) и «Оператор» (`none`) — **одно** положение:
 * управление внешнее. Для МиниГена разницы нет — в обоих случаях программа в
 * сессии не вмешивается.
 */
export function driverPosition(area: DriverArea): DriverValue | '' {
  const value = driverValue(area)
  if (!value) return ''
  return value === 'automaton' ? 'automaton' : 'none'
}

/** Выбран ли водитель в проекте явно (а не «по правилу»). */
export function driverExplicit(area: DriverArea): boolean {
  return isDriverValue(String(area.driver ?? '').trim().toLowerCase())
}

/** Кнопка включённого положения (первая — если водитель неизвестен). */
export function activeDriverButton(area: DriverArea): DriverButton {
  const position = driverPosition(area)
  return DRIVER_BUTTONS.find((item) => item.value === position) ?? DRIVER_BUTTONS[0]
}

/** Подпись включённого положения (или «—», если водителя нет). */
export function driverLabel(area: DriverArea): string {
  const position = driverPosition(area)
  return position ? DRIVER_LABELS[position] : '—'
}

/**
 * Подсказка к переключателю: что значит выбранное положение **и** кто водит
 * сессиями сейчас (и почему).
 */
export function driverHint(area: DriverArea): string {
  const position = driverPosition(area)
  const base = position ? DRIVER_HINTS[position] : 'Водитель не определён'
  const now = area.driverNow
  if (!now?.title) return base
  const why = now.why ? ` — ${now.why}` : ''
  const rule = now.source === 'правило' ? ' (по правилу)' : ''
  return `${base}. Сейчас водит: ${now.title}${rule}${why}`
}
