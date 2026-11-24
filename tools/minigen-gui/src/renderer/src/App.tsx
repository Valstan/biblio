import { useCallback, useEffect, useRef, useState } from 'react'
import type { ProgramVersion, Settings, Snapshot } from '@shared/types'
import { api, desktop } from './api'
import { buildStatsCsv } from './format'
import { headerStatus } from './tabs/header-status'
import AreasTab from './tabs/AreasTab'
import StatsTab from './tabs/StatsTab'
import NetworkTab from './tabs/NetworkTab'
import ChatTab from './tabs/ChatTab'
import JournalTab from './tabs/JournalTab'
import GenesTab from './tabs/GenesTab'
import SettingsTab from './tabs/SettingsTab'
import UpdateTab from './tabs/UpdateTab'

type Tab = 'areas' | 'stats' | 'network' | 'chat' | 'journal' | 'genes' | 'update' | 'settings'

const TABS: { id: Tab; label: string }[] = [
  { id: 'areas', label: 'Области' },
  { id: 'stats', label: 'Статистика' },
  { id: 'network', label: 'Сеть' },
  { id: 'chat', label: 'Управляющий' },
  { id: 'journal', label: 'Журнал' },
  { id: 'genes', label: 'Гены' },
  { id: 'update', label: 'Обновление' },
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
  const failures = useRef(0)
  const retryTimer = useRef<number | null>(null)

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const snap = await api.snapshot()
      setSnapshot(snap)
      setError('')
      failures.current = 0
    } catch (err) {
      setError(String((err as Error).message || err))
      // ⚠️ Мост поднимается чуть позже окна: первый снимок может прийти на
      // «мост не запущен» — и владелец видел пустую шапку («0 областей»,
      // «демоны 0/0», «мост не запущен») до следующей штатной перезагрузки.
      // Спрашиваем ещё раз — коротко и с повтором.
      failures.current += 1
      if (failures.current <= 5) {
        if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
        retryTimer.current = window.setTimeout(() => {
          void refresh()
        }, 700)
      }
    }
    // версия меняется только при раскатке — но пусть и в шапке будет видна
    void api.programVersion().then(setVersion).catch(() => undefined)
  }, [])

  useEffect(() => {
    void refresh()
    api.settings().then(setSettings).catch(() => undefined)
    // Состояние моста спрашиваем сами: событие о старте моста могло прийти
    // **до** подписки окна — и шапка долго показывала «мост не запущен»
    // при живом мосте (живой случай 24.11, видно на снимке окна).
    void desktop
      .bridgeStatus()
      .then((status) => setBridge(status))
      .catch(() => undefined)
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

  // ── индикатор «жив ли МиниГен» (23.11, вопрос владельца) ──────────────
  // Показываем в шапке, на любой вкладке: сколько демонов живо, кто ими
  // надзирает (это окно или фоновый режим) и какая версия программы идёт.
  // Считает чистый модуль — его проверяет тест (живой случай 24.11).
  const head = headerStatus(snapshot, version, bridge)

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">МГ</span>
          <div>
            <h1>МиниГен</h1>
            <span className="sub">
              {head.areasLabel}
              {head.versionLabel ? ` · ${head.versionLabel}` : ''}
              {head.configBroken ? ' · конфиг не читается!' : ''}
            </span>
          </div>
        </div>
        <div className="topbar-actions">
          <span className={`chip ${head.daemonTone}`}>{head.daemonLabel}</span>
          <span className={`chip ${head.supervisorTone}`}>{head.supervisorLabel}</span>
          <span className={`status ${head.bridgeTone}`}>{head.bridgeLabel}</span>
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
        {tab === 'genes' && <GenesTab />}
        {tab === 'update' && <UpdateTab />}
        {tab === 'settings' && (
          <SettingsTab settings={settings} snapshot={snapshot} onSaved={setSettings} />
        )}
      </main>
    </div>
  )
}
