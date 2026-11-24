import { describe, expect, it } from 'vitest'
import { daemonHeader } from '../src/renderer/src/daemon-view'
import type { DaemonStatus, Snapshot } from '../src/shared/types'

/**
 * Шапка окна не должна врать (живой случай 24.11: вопрос владельца «кто в
 * итоге врет?»).
 *
 * Что было: шапка писала «демоны 0/0 · надзирателя нет», а таблица в
 * «Настройках» — живых демонов с pid. Расходились из-за разных источников:
 * шапка читала снимок (он падал — и превращался в нули), таблица — другой
 * запрос. Теперь обе читают одно, и при сбое видна **причина**, а не нули.
 */

function daemons(over: Partial<DaemonStatus> = {}): DaemonStatus {
  return {
    daemons: [
      { name: 'лечащий', role: 'аккаунты', script: 'C:/bin/minimax-autheal.py', exists: true, alive: true, pids: [2516] },
      { name: 'сетевой сторож', role: 'сеть', script: 'C:/bin/minimax-netwatch.py', exists: true, alive: true, pids: [29964] },
      { name: 'сторож сессий', role: 'сессии', script: 'C:/bin/minimax-sessionwatch.py', exists: true, alive: true, pids: [40904] }
    ],
    alive_count: 3,
    total: 3,
    checked_at: 1_700_000_000_000,
    supervisor: { pid: 7592, path: 'C:/МиниГен/app/bridge/minigen_bridge.py', alive: true },
    ...over
  }
}

function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  return {
    at: 1_700_000_000_000,
    areas: [],
    network: {} as Snapshot['network'],
    daemons: daemons(),
    stats: {} as Snapshot['stats'],
    config_error: '',
    journal_path: 'C:/journal.log',
    areas_root: 'C:/areas',
    daemon_dir: 'C:/bin',
    ...over
  }
}

describe('шапка: демоны и надзиратель', () => {
  it('живые демоны видны и по числам, и по надзирателю', () => {
    const header = daemonHeader(snapshot())
    expect(header.daemons.label).toBe('демоны 3/3')
    expect(header.daemons.tone).toBe('ok')
    expect(header.supervisor.label).toContain('надзиратель')
    expect(header.error).toBe('')
  })

  it('снимок не пришёл — это «неизвестно», а не «демонов нет»', () => {
    const header = daemonHeader(null)
    expect(header.daemons.label).toBe('демоны: неизвестно')
    expect(header.daemons.tone).toBe('bad')
    expect(header.error).toContain('снимок не читается')
  })

  it('снимок пришёл с ошибкой — видно причину, а не нули', () => {
    const header = daemonHeader(snapshot({ error: 'TypeError: fmt_ms() takes 1 positional argument' }))
    expect(header.error).toContain('снимок не читается')
    expect(header.error).toContain('fmt_ms')
    expect(header.daemons.label).not.toContain('0/0')
  })

  it('состояние демонов не прочиталось — нулей не показываем', () => {
    const header = daemonHeader(snapshot({
      daemons: daemons({ error: 'PermissionError: не удалось прочитать процессы' })
    }))
    expect(header.daemons.label).toBe('демоны: не прочитано')
    expect(header.daemons.tone).toBe('bad')
    expect(header.supervisor.label).toBe('надзиратель: неизвестно')
    expect(header.error).toContain('PermissionError')
  })

  it('надзиратель — это окно / фоновый режим / его нет', () => {
    const self = daemonHeader(snapshot({
      daemons: daemons({ supervisor: { pid: 1, path: 'C:/x', alive: true, is_self: true } })
    }))
    expect(self.supervisor.label).toBe('надзиратель: это окно')

    const background = daemonHeader(snapshot({
      daemons: daemons({ supervisor: { pid: 2, path: 'C:/x', alive: true } })
    }))
    expect(background.supervisor.label).toBe('надзиратель: фоновый режим')

    const dead = daemonHeader(snapshot({
      daemons: daemons({ supervisor: { pid: 3, path: 'C:/x', alive: false } })
    }))
    expect(dead.supervisor.label).toBe('надзиратель: его нет')
    expect(dead.supervisor.tone).toBe('bad')
  })

  it('часть демонов не работает — тон плохой, но числа настоящие', () => {
    const header = daemonHeader(snapshot({ daemons: daemons({ alive_count: 1, total: 3 }) }))
    expect(header.daemons.label).toBe('демоны 1/3')
    expect(header.daemons.tone).toBe('bad')
  })
})
