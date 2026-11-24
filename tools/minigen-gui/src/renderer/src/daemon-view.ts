import type { DaemonStatus, Snapshot } from '@shared/types'

/**
 * Шапка окна: демоны и надзиратель — **по одному источнику** (снимок моста).
 *
 * ⚠️ Живой случай 24.11: шапка писала «демоны 0/0 · надзирателя нет», а
 * таблица в «Настройках» показывала живых демонов с pid. Расходились не
 * из-за разных данных, а из-за разных **путей**: шапка читала снимок (он
 * падал — и молча превращался в нули), а таблица — прямой запрос. Теперь обе
 * читают одно и то же, и когда данных нет — видно **причину**, а не
 * выдуманные нули («нет данных» ≠ «демонов нет»).
 *
 * Функция чистая — её можно проверить тестом.
 */

export interface DaemonChip {
  label: string
  tone: 'ok' | 'bad'
}

export interface DaemonHeader {
  /** Демоны: сколько живо из скольких. */
  daemons: DaemonChip
  /** Кто надзирает за демонами. */
  supervisor: DaemonChip
  /** Причина, если снимок или состояние демонов не прочитались (иначе — ''). */
  error: string
}

function supervisorChip(status: DaemonStatus | undefined): DaemonChip {
  const supervisor = status?.supervisor
  if (!supervisor) {
    // Данных нет — это не «надзирателя нет», а «неизвестно».
    return { label: 'надзиратель: неизвестно', tone: 'bad' }
  }
  if (supervisor.is_self) return { label: 'надзиратель: это окно', tone: 'ok' }
  if (supervisor.alive) return { label: 'надзиратель: фоновый режим', tone: 'ok' }
  return { label: 'надзиратель: его нет', tone: 'bad' }
}

export function daemonHeader(snapshot: Snapshot | null): DaemonHeader {
  if (!snapshot) {
    return {
      daemons: { label: 'демоны: неизвестно', tone: 'bad' },
      supervisor: { label: 'надзиратель: неизвестно', tone: 'bad' },
      error: 'снимок не читается: окно ещё не получило состояние'
    }
  }

  const status = snapshot.daemons
  const daemonsError = String(status?.error || '')
  const snapshotError = String(snapshot.error || '')

  if (daemonsError) {
    return {
      daemons: { label: 'демоны: не прочитано', tone: 'bad' },
      supervisor: { label: 'надзиратель: неизвестно', tone: 'bad' },
      error: `состояние демонов не прочитано: ${daemonsError}`
    }
  }

  const alive = Number(status?.alive_count ?? 0)
  const total = Number(status?.total ?? 0)
  const tone: 'ok' | 'bad' = total > 0 && alive === total ? 'ok' : 'bad'

  return {
    daemons: { label: `демоны ${alive}/${total}`, tone },
    supervisor: supervisorChip(status),
    error: snapshotError ? `снимок не читается: ${snapshotError}` : ''
  }
}
