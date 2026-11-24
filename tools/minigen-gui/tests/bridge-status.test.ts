import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

/**
 * Статус моста в шапке окна (живой случай 24.11).
 *
 * Мост поднимается **до** окна: событие «мост запущен» приходило раньше,
 * чем окно подписалось, — и шапка навсегда показывала «мост не запущен»
 * при живом мосте (и при живых демонах). Теперь:
 *
 * - главный процесс помнит последнее сообщение моста и отдают его по
 *   запросу (`minigen:bridge-status`);
 * - окно спрашивает этот статус само, а не ждёт события;
 * - если снимок не пришёл, окно спрашивает ещё раз (мост поднимается
 *   чуть позже окна).
 */

const main = readFileSync(join(__dirname, '../src/main/index.ts'), 'utf8')
const bridge = readFileSync(join(__dirname, '../src/main/bridge.ts'), 'utf8')
const preload = readFileSync(join(__dirname, '../src/preload/index.ts'), 'utf8')
const app = readFileSync(join(__dirname, '../src/renderer/src/App.tsx'), 'utf8')
const api = readFileSync(join(__dirname, '../src/renderer/src/api.ts'), 'utf8')

describe('статус моста', () => {
  it('главный процесс отдаёт статус моста по запросу окна', () => {
    expect(main).toContain("ipcMain.handle('minigen:bridge-status'")
    expect(preload).toContain('bridgeStatus()')
    expect(api).toContain('bridgeStatus')
  })

  it('мост помнит последнее сообщение (событие могло прийти до окна)', () => {
    expect(bridge).toContain('lastMessage')
    expect(bridge).toMatch(/status\(\)[^]*lastMessage/)
  })

  it('окно спрашивает статус моста само', () => {
    expect(app).toContain('desktop')
    expect(app).toMatch(/bridgeStatus\(\)/)
  })

  it('окно повторяет запрос снимка, пока мост поднимается', () => {
    expect(app).toContain('failures.current')
    expect(app).toMatch(/setTimeout\([\s\S]{0,120}refresh\(\)/)
  })
})
