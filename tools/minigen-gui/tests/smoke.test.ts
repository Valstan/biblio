import { describe, expect, it } from 'vitest'
import { clickTabScript, mapGeometryScript, parseSmokeTabs, shotName } from '../src/main/smoke'

/**
 * Дымовая приёмка окна (24.11): окно само снимает вкладки — видимый текст и
 * скриншот, — чтобы «приёмку глазами» можно было прогнать автоматически.
 *
 * Проверяем главное: метка вкладки попадает в скрипт **как JSON-литерал**
 * (иначе кавычка в метке порвала бы скрипт — так ломалась старая панель).
 */

describe('дымовая приёмка окна', () => {
  it('метки вкладок читаются из списка', () => {
    expect(parseSmokeTabs('Гены,Настройки')).toEqual(['Гены', 'Настройки'])
    expect(parseSmokeTabs(' Гены , Настройки ')).toEqual(['Гены', 'Настройки'])
    expect(parseSmokeTabs('')).toEqual([])
    expect(parseSmokeTabs(undefined)).toEqual([])
  })

  it('скрипт нажимает кнопку вкладки с такой меткой', () => {
    const script = clickTabScript('Гены')
    expect(script).toContain('"Гены"')
    expect(script).toContain('nav.tabs button')
    expect(script).toContain('.click()')
  })

  it('метка с кавычкой не рвёт скрипт', () => {
    const script = clickTabScript('Кавычка " и \\ слэш')
    // JSON-литерал: кавычка и слэш экранированы, сырой текст — нет
    expect(script).toContain('\\"')
    expect(script).toContain('\\\\')
    expect(script).not.toContain('"Кавычка " и')
  })

  it('имя снимка — по порядку и по метке', () => {
    expect(shotName('Гены', 0)).toBe('01-geny.png')
    expect(shotName('Настройки', 1)).toBe('02-nastroyki.png')
    expect(shotName('***', 2)).toBe('03-tab.png')
  })

  it('проверка обрезки карты смотрит на полотно и на узлы', () => {
    const script = mapGeometryScript()
    expect(script).toContain('svg[aria-label]')
    expect(script).toContain('getAttribute(\'width\')')
    expect(script).toContain('maxX <= width')
  })
})
