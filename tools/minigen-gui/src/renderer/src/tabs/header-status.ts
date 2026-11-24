import type { ProgramVersion, Snapshot } from '@shared/types'

/**
 * Шапка окна: что окно говорит о себе и о программе.
 *
 * Вынесено в отдельный модуль — чтобы это проверялось тестом, а не глазами
 * (живой случай 24.11: снимок не пришёл, и шапка молча показывала «0 областей»,
 * «демоны 0/0», «надзирателя нет» — владелец решил, что программа пустая,
 * а она просто не получила снимок).
 *
 * Правило: **нет снимка — значит «неизвестно», а не «ноль»**. Ноль — это
 * утверждение о мире; отсутствие снимка — утверждение о молчании.
 */

export interface BridgeState {
  alive: boolean
  message: string
}

export interface HeaderStatus {
  /** «19 областей · 5 запущено» — либо «снимок не пришёл». */
  areasLabel: string
  running: number
  areas: number
  /** «установленная копия 756ffa6» — либо пусто. */
  versionLabel: string
  daemonLabel: string
  daemonTone: 'ok' | 'bad'
  supervisorLabel: string
  supervisorTone: 'ok' | 'bad'
  bridgeLabel: string
  bridgeTone: 'ok' | 'bad'
  /** Конфиг областей не читается — предупреждение в шапке. */
  configBroken: boolean
}

export function headerStatus(
  snapshot: Snapshot | null,
  version: ProgramVersion | null,
  bridge: BridgeState
): HeaderStatus {
  const areas = snapshot?.areas ?? []
  const running = areas.filter((area) => area.running).length
  const known = snapshot !== null

  const daemons = snapshot?.daemons
  const aliveCount = daemons?.alive_count ?? 0
  const totalCount = daemons?.total ?? 0
  const supervisor = daemons?.supervisor ?? null

  const supervisorLabel = !supervisor
    ? known
      ? 'надзирателя нет'
      : 'надзиратель: неизвестно'
    : supervisor.is_self
      ? 'надзиратель: это окно'
      : supervisor.alive
        ? 'надзиратель: фоновый режим'
        : 'надзиратель: его нет'

  return {
    areasLabel: known ? `${areas.length} областей · ${running} запущено` : 'снимок не пришёл',
    running,
    areas: areas.length,
    versionLabel: version?.commit
      ? `${version.installed ? 'установленная копия' : 'рабочее дерево'} ${version.commit}`
      : '',
    daemonLabel: known ? `демоны ${aliveCount}/${totalCount}` : 'демоны: неизвестно',
    daemonTone:
      known && totalCount > 0 && aliveCount === totalCount ? 'ok' : 'bad',
    supervisorLabel,
    supervisorTone: known && supervisor?.alive ? 'ok' : 'bad',
    bridgeLabel: bridge.message,
    bridgeTone: bridge.alive ? 'ok' : 'bad',
    configBroken: Boolean(snapshot?.config_error)
  }
}
