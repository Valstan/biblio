import { describe, expect, it } from 'vitest'
import { headerStatus } from '../src/renderer/src/tabs/header-status'
import type { ProgramVersion, Snapshot } from '../src/shared/types'

/**
 * Шапка окна «МиниГена» (живой случай 24.11).
 *
 * Снимок не пришёл — а шапка показывала «0 областей», «демоны 0/0»,
 * «надзирателя нет». Владелец видел пустую программу и не понимал, что
 * она сломана. Теперь:
 *
 * - пришедший снимок — показываем правду (области, демоны, надзиратель);
 * - непришедший снимок — говорим «неизвестно», а не «ноль».
 */

function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  return {
    at: 0,
    areas: [
      { slug: 'brain', title: 'Мозг', running: true },
      { slug: 'minigen', title: 'МиниГен', running: false }
    ],
    daemons: {
      daemons: [],
      alive_count: 3,
      total: 3,
      supervisor: { pid: 10, alive: true, is_self: true }
    },
    ...over
  } as unknown as Snapshot
}

const version: ProgramVersion = {
  commit: '756ffa6',
  installed: true,
  installed_at: 1795518465,
  source: 'D:/repo',
  running_from: 'C:/Program'
} as unknown as ProgramVersion

describe('шапка окна', () => {
  it('показывает правду, когда снимок пришёл', () => {
    const head = headerStatus(snapshot(), version, { alive: true, message: 'мост запущен' })
    expect(head.areasLabel).toBe('2 областей · 1 запущено')
    expect(head.daemonLabel).toBe('демоны 3/3')
    expect(head.daemonTone).toBe('ok')
    expect(head.supervisorLabel).toBe('надзиратель: это окно')
    expect(head.supervisorTone).toBe('ok')
    expect(head.versionLabel).toBe('установленная копия 756ffa6')
  })

  it('без снимка говорит «неизвестно», а не «ноль»', () => {
    const head = headerStatus(null, version, { alive: false, message: 'мост не запущен' })
    expect(head.areasLabel).toBe('снимок не пришёл')
    expect(head.daemonLabel).toBe('демоны: неизвестно')
    expect(head.supervisorLabel).toBe('надзиратель: неизвестно')
    expect(head.daemonTone).toBe('bad')
    expect(head.bridgeLabel).toBe('мост не запущен')
    expect(head.bridgeTone).toBe('bad')
  })

  it('надзиратель: фоновая копия — и это «хорошо»', () => {
    const snap = snapshot({
      daemons: {
        daemons: [],
        alive_count: 3,
        total: 3,
        supervisor: { pid: 20, alive: true, is_self: false }
      }
    } as unknown as Partial<Snapshot>)
    const head = headerStatus(snap, version, { alive: true, message: 'мост запущен' })
    expect(head.supervisorLabel).toBe('надзиратель: фоновый режим')
    expect(head.supervisorTone).toBe('ok')
  })

  it('упавший демон и надзиратель видны как «плохо»', () => {
    const snap = snapshot({
      daemons: {
        daemons: [],
        alive_count: 1,
        total: 3,
        supervisor: { pid: 20, alive: false, is_self: false }
      }
    } as unknown as Partial<Snapshot>)
    const head = headerStatus(snap, version, { alive: true, message: 'мост запущен' })
    expect(head.daemonTone).toBe('bad')
    expect(head.supervisorTone).toBe('bad')
    expect(head.supervisorLabel).toBe('надзиратель: его нет')
  })

  it('нераспознанный конфиг виден в шапке', () => {
    const snap = snapshot({ config_error: 'битый JSON' } as unknown as Partial<Snapshot>)
    expect(headerStatus(snap, version, { alive: true, message: 'м' }).configBroken).toBe(true)
  })
})
