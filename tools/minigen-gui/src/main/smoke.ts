/**
 * Дымовая приёмка окна: снимки вкладок — видимый текст и скриншот.
 *
 * Зачем (24.11): вкладку «Гены» (карта родословной) и карточку «Родословная
 * копии» видел только владелец — автоматической приёмки не было, и поломка
 * вёрстки осталась бы незамеченной. Теперь окно умеет **само себя показать**:
 * переключает вкладку, отдаёт её видимый текст и снимок экрана, после чего
 * закрывается. Приёмка (`tools/minimax-project-areas/test_window_smoke_ui.py`)
 * читает это и проверяет, что на вкладке есть ожидаемое.
 *
 * Переменные окружения (включаются в дымовом режиме `MINIGEN_SMOKE=1`):
 * - `MINIGEN_SMOKE_TABS` — метки вкладок через запятую («Гены,Настройки»);
 * - `MINIGEN_SMOKE_TEXT` — файл, куда пишется JSON `{метка: видимый текст}`;
 * - `MINIGEN_SMOKE_SHOTS` — папка, куда пишутся PNG-снимки вкладок.
 */

/** Метки вкладок, которые надо снять (из `MINIGEN_SMOKE_TABS`). */
export function parseSmokeTabs(raw: string | undefined): string[] {
  return (raw || '')
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

/**
 * Скрипт для рендерера: нажать кнопку вкладки с такой меткой.
 *
 * ⚠️ Метка вставляется как **JSON-литерал** (`JSON.stringify`), а не как
 * сырой текст: иначе метка с кавычкой или обратным слэшем порвала бы
 * скрипт (та же ошибка, что ломала старую панель — «страница грузится» ≠
 * «скрипт работает»).
 */
export function clickTabScript(label: string): string {
  const wanted = JSON.stringify(label)
  return `(() => {
  const wanted = ${wanted}
  const buttons = Array.from(document.querySelectorAll('nav.tabs button'))
  const button = buttons.find((item) => (item.textContent || '').trim() === wanted)
  if (!button) return 'нет вкладки: ' + wanted
  button.click()
  return 'ок'
})()`
}

/** Транслитерация — чтобы имя снимка было читаемым и без кириллицы. */
const TRANSLIT: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z',
  и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r',
  с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh',
  щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya'
}

/** Имя файла снимка: `01-genes.png` (по порядку вкладок и её метке). */
export function shotName(label: string, index: number): string {
  const slug = label
    .toLowerCase()
    .split('')
    .map((char) => (TRANSLIT[char] !== undefined ? TRANSLIT[char] : char))
    .join('')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return `${String(index + 1).padStart(2, '0')}-${slug || 'tab'}.png`
}

/**
 * Скрипт: **обрезана ли карта**. Живой случай 24.11: ширина полотна считалась
 * по числу колонок, а не по последней занятой ⇒ правый край карты уезжал за
 * полотно и узлы обрезались. На глаз это видно, а в тексте вкладки — нет,
 * поэтому проверка живёт здесь: сравниваем размер полотна и крайние узлы.
 */
export function mapGeometryScript(): string {
  return `(() => {
  const svg = document.querySelector('svg[aria-label]')
  if (!svg) return null
  const width = Number(svg.getAttribute('width')) || 0
  const height = Number(svg.getAttribute('height')) || 0
  let maxX = 0
  let maxY = 0
  svg.querySelectorAll('rect').forEach((node) => {
    maxX = Math.max(maxX, Number(node.getAttribute('x')) + Number(node.getAttribute('width')))
    maxY = Math.max(maxY, Number(node.getAttribute('y')) + Number(node.getAttribute('height')))
  })
  return { ok: maxX <= width && maxY <= height, width, height, max_x: maxX, max_y: maxY }
})()`
}
