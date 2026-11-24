import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ProgramVersion, Settings, Snapshot } from '@shared/types'
import { api } from './api'
import { buildStatsCsv } from './format'
import AreasTab from './tabs/AreasTab'
import StatsTab from './tabs/StatsTab'
import NetworkTab from './tabs/NetworkTab'
import ChatTab from './tabs/ChatTab'
import JournalTab from './tabs/JournalTab'
import SettingsTab from './tabs/SettingsTab'

type Tab = 'areas' | 'stats' | 'network' | 'chat' | 'journal' | 'settings'

const TABS: { id: Tab; label: string }[] = [
  { id: 'areas', label: 'Области' },
  { id: 'stats', label: 'Статистика' },
  { id: 'network', label: 'Сеть' },
  { id: 'chat', label: 'Управляющий' },
  { id: 'journal', label: 'Журнал' },
  { id: 'settings', label: 'Настройки' }
]

export default function App() {
  const [tab, setTab] = useState<Tab>('areas')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [version, setVersion] = useState<ProgramVersion | null>(null)
  const [bridge, setBridge] = useState({ alive: false, message: 'мост не запущен' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const snap = await api.snapshot()
      setSnapshot(snap)
      setError('')
    } catch (err) {
      setError(String((err as Error).message || err))
    }
    // версия меняется только при раскатке — но пусть и в шапке будет видна
    void api.programVersion().then(setVersion).catch(() => undefined)
  }, [])

  useEffect(() => {
    void refresh()
    api.settings().then(setSettings).catch(() => undefined)
    return window.minigen.onEvent((event) => {
      setBridge({ alive: event.alive, message: event.message })
    })
  }, [refresh])

  const interval = settings?.refreshMs ?? 5000
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh()
    }, Math.max(1000, interval))
    return () => window.clearInterval(timer)
  }, [interval, refresh])

  // Дымовая проверка экспорта CSV: окно собирает CSV из живых данных тем же
  // кодом, что и кнопка «Экспорт CSV», и отдаёт его главному процессу.
  useEffect(() => {
    if (!window.minigen.smoke?.csv) return
    const timer = window.setInterval(() => {
      if (!snapshot) return
      window.clearInterval(timer)
      try {
        ;(window as unknown as { __minigenSmokeCsv?: string }).__minigenSmokeCsv =
          buildStatsCsv(snapshot.stats)
      } catch {
        /* пусто — главный процесс сообщит об этом */
      }
    }, 500)
    return () => window.clearInterval(timer)
  }, [snapshot])

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await action()
        await refresh()
      } catch (err) {
        setError(String((err as Error).message || err))
      } finally {
        setBusy(false)
      }
    },
    [refresh]
  )

  const areas = snapshot?.areas ?? []
  const running = useMemo(() => areas.filter((area) => area.running).length, [areas])

  // ── индикатор «жив ли МиниГен» (23.11, вопрос владельца) ──────────────
  // Показываем в шапке, на любой вкладке: сколько демонов живо, кто ими
  // надзирает (это окно или фоновый режим) и какая версия программы идёт.
  const daemons = snapshot?.daemons
  const aliveCount = daemons?.alive_count ?? 0
  const totalCount = daemons?.total ?? 0
  const daemonTone = totalCount > 0 && aliveCount === totalCount ? 'ok' : 'bad'
  const supervisor = daemons?.supervisor ?? null
  const supervisorLabel = !supervisor
    ? 'надзирателя нет'
    : supervisor.is_self
      ? 'надзиратель: это окно'
      : supervisor.alive
        ? 'надзиратель: фоновый режим'
        : 'надзиратель: его нет'
  const supervisorTone = supervisor && supervisor.alive ? 'ok' : 'bad'
  const versionLabel = version?.commit
    ? `${version.installed ? 'установленная копия' : 'рабочее дерево'} ${version.commit}`
    : ''

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">МГ</span>
          <div>
            <h1>МиниГен</h1>
            <span className="sub">
              {areas.length} областей · {running} запущено
              {versionLabel ? ` · ${versionLabel}` : ''}
              {snapshot?.config_error ? ' · конфиг не читается!' : ''}
            </span>
          </div>
        </div>
        <div className="topbar-actions">
          <span className={`chip ${daemonTone}`}>демоны {aliveCount}/{totalCount}</span>
          <span className={`chip ${supervisorTone}`}>{supervisorLabel}</span>
          <span className={`status ${bridge.alive ? 'ok' : 'bad'}`}>{bridge.message}</span>
          <button className="ghost" onClick={() => void refresh()} disabled={busy}>
            Обновить
          </button>
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((item) => (
          <button
            key={item.id}
            className={tab === item.id ? 'tab active' : 'tab'}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {error && <div className="banner bad">⚠ {error}</div>}

      <main className="content">
        {tab === 'areas' && (
          <AreasTab areas={areas} snapshot={snapshot} run={run} busy={busy} />
        )}
        {tab === 'stats' && <StatsTab run={run} busy={busy} />}
        {tab === 'network' && <NetworkTab snapshot={snapshot} run={run} busy={busy} />}
        {tab === 'chat' && <ChatTab />}
        {tab === 'journal' && <JournalTab snapshot={snapshot} />}
        {tab === 'settings' && (
          <SettingsTab settings={settings} snapshot={snapshot} onSaved={setSettings} />
        )}
      </main>
    </div>
  )
}
