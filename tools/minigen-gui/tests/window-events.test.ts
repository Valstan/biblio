import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'
import { sendToWindow } from '../src/main/window-events'

/**
 * Регрессия 24.11: при закрытии окна «МиниГена» главный процесс падал —
 * «Uncaught Exception: TypeError: Object has been destroyed at Bridge.event».
 * Мост умирает вместе с окном и шлёт последнее событие в уже мёртвое окно.
 */

function liveWindow(sent: Array<[string, unknown]>): Parameters<typeof sendToWindow>[0] {
  return {
    isDestroyed: () => false,
    webContents: {
      isDestroyed: () => false,
      send: (channel: string, payload: unknown) => {
        sent.push([channel, payload])
      }
    }
  }
}

describe('sendToWindow', () => {
  it('доставляет событие в живое окно', () => {
    const sent: Array<[string, unknown]> = []
    const ok = sendToWindow(liveWindow(sent), 'minigen:event', { kind: 'bridge-status' })
    expect(ok).toBe(true)
    expect(sent).toEqual([['minigen:event', { kind: 'bridge-status' }]])
  })

  it('пропускает событие, если окно уничтожено', () => {
    const sent: Array<[string, unknown]> = []
    const dead = {
      isDestroyed: () => true,
      webContents: {
        isDestroyed: () => true,
        send: (channel: string, payload: unknown) => {
          sent.push([channel, payload])
        }
      }
    }
    expect(sendToWindow(dead, 'minigen:event', { kind: 'bridge-status' })).toBe(false)
    expect(sent).toHaveLength(0)
  })

  it('пропускает событие, если уничтожен webContents', () => {
    const sent: Array<[string, unknown]> = []
    const dead = {
      isDestroyed: () => false,
      webContents: {
        isDestroyed: () => true,
        send: (channel: string, payload: unknown) => {
          sent.push([channel, payload])
        }
      }
    }
    expect(sendToWindow(dead, 'minigen:event', {})).toBe(false)
    expect(sent).toHaveLength(0)
  })

  it('не роняет главный процесс, если окно умерло в гонке', () => {
    const racing = {
      isDestroyed: () => false,
      webContents: {
        isDestroyed: () => false,
        send: () => {
          throw new TypeError('Object has been destroyed')
        }
      }
    }
    expect(() => sendToWindow(racing, 'minigen:event', {})).not.toThrow()
    expect(sendToWindow(racing, 'minigen:event', {})).toBe(false)
  })

  it('терпит отсутствие окна', () => {
    expect(sendToWindow(null, 'minigen:event', {})).toBe(false)
    expect(sendToWindow(undefined, 'minigen:event', {})).toBe(false)
  })
})

describe('главный процесс', () => {
  const source = readFileSync(join(__dirname, '../src/main/index.ts'), 'utf8')
  // Комментарии вырезаем: в них упоминание `webContents.send(` — это
  // объяснение поломки, а не вызов.
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    // ⚠️ Без `$`: в JavaScript точка не матчит `\r`, а строки в файле CRLF —
    // с `$` регулярка не снимала комментарий и ловила саму себя.
    .map((line) => line.replace(/\/\/.*/, ''))
    .join('\n')

  it('не шлёт события в окно напрямую (только через sendToWindow)', () => {
    expect(code).not.toMatch(/webContents\.send\(/)
    expect(code).toContain('sendToWindow(mainWindow')
  })

  it('обнуляет ссылку на окно при закрытии', () => {
    expect(code).toMatch(/on\('closed'[\s\S]{0,120}mainWindow = null/)
  })
})
