import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Settings, Snapshot } from '@shared/types'
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
  { id: 'chat', label: 'Чат' },
  { id: 'journal', label: 'Журнал' },
  { id: 'settings', label: 'Настройки' }
]

export default function App() {
  const [tab, setTab] = useState<Tab>('areas')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">МГ</span>
          <div>
            <h1>МиниГен</h1>
            <span className="sub">
              {areas.length} областей · {running} запущено
              {snapshot?.config_error ? ' · конфиг не читается!' : ''}
            </span>
          </div>
        </div>
        <div className="topbar-actions">
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
