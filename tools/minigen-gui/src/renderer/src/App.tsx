import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  CopyFirstRunSnapshot,
  ProgramVersion,
  Settings,
  Snapshot
} from '@shared/types'
import { api, desktop } from './api'
import { buildStatsCsv } from './format'
import { headerStatus } from './tabs/header-status'
import { TABS, type TabId } from './tabs/tabs'
import AreasTab from './tabs/AreasTab'
import StatsTab from './tabs/StatsTab'
import NetworkTab from './tabs/NetworkTab'
import ChatTab from './tabs/ChatTab'
import JournalTab from './tabs/JournalTab'
import GenesTab from './tabs/GenesTab'
import AgentsTab from './tabs/AgentsTab'
import PromptsTab from './tabs/PromptsTab'
import SettingsTab from './tabs/SettingsTab'
import UpdateTab from './tabs/UpdateTab'
import CopyFirstRunQuestion from './tabs/copy-first-run'
import { answerConfirmText, newWorksView, type NewWorksView } from './tabs/copy-view'
import { mergeSeen, readSeen, writeSeen } from './tabs/works-seen'

/** Как часто окно ищет наработки других копий (секунд). */
const WORKS_CHECK_SECONDS = 30
/** Как часто обновляется пул наработок (минут) — сеть не должна дёргаться часто. */
const POOL_SYNC_MINUTES = 10

export default function App() {
  const [tab, setTab] = useState<TabId>('areas')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [version, setVersion] = useState<ProgramVersion | null>(null)
  const [bridge, setBridge] = useState({ alive: false, message: 'мост не запущен' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const failures = useRef(0)
  const retryTimer = useRef<number | null>(null)
  // ── первый запуск копии (заказ владельца 25.11, промт #131) ──────────────
  const [copySnapshot, setCopySnapshot] = useState<CopyFirstRunSnapshot | null>(null)
  const [copyError, setCopyError] = useState('')
  const [copyBusy, setCopyBusy] = useState(false)
  const [copyNote, setCopyNote] = useState('')
  /** тон сообщения об ответе: '' — обычное, 'bad' — отказ моста. */
  const [copyNoteTone, setCopyNoteTone] = useState<'plain' | 'bad'>('plain')
  // ── наработки других копий: уведомление о новых (промт #131) ─────────────
  const [newWorks, setNewWorks] = useState<NewWorksView | null>(null)
  const [openWorks, setOpenWorks] = useState(false)
  const seenWorks = useRef<string[]>(readSeen())
  const lastPoolSync = useRef(0)

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

  // ── вопрос первого запуска копии (заказ владельца 25.11, промт #131) ────
  // ⚠️ Вопрос живёт в окне, а не в установщике: программа может быть
  // перенесена на другую машину, и установщик там был не этот. Поэтому
  // спрашиваем мост при открытии окна — и спрашиваем **по таймеру**: мост
  // поднимается позже окна, и разовый запрос в этот момент падает.
  const loadCopy = useCallback(async () => {
    try {
      setCopySnapshot(await api.copyFirstRun())
      setCopyError('')
    } catch (err) {
      setCopyError(
        `вопрос первого запуска не принесли: ${String((err as Error).message || err)}`
      )
    }
  }, [])

  useEffect(() => {
    void loadCopy()
    const timer = window.setInterval(() => void loadCopy(), 5000)
    return () => window.clearInterval(timer)
  }, [loadCopy])

  /**
   * Ответ владельца на вопрос первого запуска.
   *
   * ⚠️ Оба ответа равноправны и оба спрашивают подтверждения: отказ не ломает
   * программу (копия просто работает локально).
   */
  const answerFirstRun = async (agree: boolean) => {
    if (!copySnapshot) return
    if (!window.confirm(answerConfirmText(agree, copySnapshot.offer))) return
    setCopyBusy(true)
    try {
      const res = await api.copyAnswer(agree)
      if (res.ok === false) {
        setCopyNote(res.error || res.why || 'мост не принял ответ')
        setCopyNoteTone('bad')
        return
      }
      setCopyNote(
        res.why ||
          (agree ? 'согласие принято' : 'отказ принят — копия работает локально')
      )
      setCopyNoteTone('plain')
      await loadCopy()
    } catch (err) {
      setCopyNote(`ответ не принят: ${String((err as Error).message || err)}`)
      setCopyNoteTone('bad')
    } finally {
      setCopyBusy(false)
    }
  }

  // ── наработки других копий: ищем новые и зовём владельца (промт #131) ────
  /**
   * Обновить пул наработок — но не чаще, чем раз в `POOL_SYNC_MINUTES`.
   *
   * ⚠️ Обновление пула — сетевая операция (git fetch/pull), поэтому окно не
   * дёргает сеть каждые полминуты: сеть нужна, чтобы узнать о новых
   * наработках соседей, а не сама по себе.
   */
  const refreshPool = useCallback(async () => {
    const now = Date.now()
    if (now - lastPoolSync.current < POOL_SYNC_MINUTES * 60 * 1000) return
    lastPoolSync.current = now
    try {
      // В локальном режиме мост отвечает сразу и без сети («сети нет —
      // обновлять нечего»), так что сеть тут не тревожится зря.
      await api.cloneSync()
    } catch {
      /* мост не ответил — уведомление придёт в следующий раз */
    }
  }, [])

  /**
   * Найти наработки, которых копия ещё не видела, и предложить их владельцу.
   *
   * ⚠️ Чужой ген никогда не попадает в программу молча: тут — только
   * уведомление, внедрение — отдельным действием через ветку и PR.
   */
  const checkWorks = useCallback(async () => {
    void refreshPool()
    try {
      const data = await api.genesSuggest()
      const view = newWorksView(data, seenWorks.current)
      if (!view.count) return
      setNewWorks(view)
      // Показали — значит владелец их видел: в следующий раз не повторяем.
      seenWorks.current = mergeSeen(seenWorks.current, view.genes)
      writeSeen(seenWorks.current)
    } catch {
      /* мост не ответил — уведомление придёт в следующий раз */
    }
  }, [refreshPool])

  useEffect(() => {
    void checkWorks()
    const timer = window.setInterval(
      () => void checkWorks(),
      WORKS_CHECK_SECONDS * 1000
    )
    return () => window.clearInterval(timer)
  }, [checkWorks])

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

      {newWorks && newWorks.count > 0 && (
        <div className="banner warn">
          <div className="row">
            <span>{newWorks.text}</span>
            <button
              className="ghost"
              onClick={() => {
                setTab('genes')
                setOpenWorks(true)
              }}
            >
              Показать
            </button>
          </div>
        </div>
      )}

      {/* ⚠️ Итог ответа живёт **отдельно** от вопроса: вопрос после ответа
          исчезает, а сообщение («принято, но не всё получилось…») остаётся —
          иначе владелец не узнал бы, что клон не взялся. */}
      {copyNote && (
        <div className={`banner ${copyNoteTone === 'bad' ? 'bad' : ''}`}>
          {copyNote}
        </div>
      )}

      {/* ⚠️ Вопрос первого запуска копии — на любой вкладке, пока владелец не
          ответил: он спрошен один раз, и ответ «отказаться» не ломает
          программу (копия просто работает локально). */}
      {copySnapshot && !copySnapshot.first_run?.asked && (
        <div className="card">
          <h3>Первый запуск копии «МиниГена» — нужен ваш ответ</h3>
          {copyError && <div className="banner bad">{copyError}</div>}
          <CopyFirstRunQuestion
            snapshot={copySnapshot}
            busy={copyBusy}
            onAgree={() => void answerFirstRun(true)}
            onRefuse={() => void answerFirstRun(false)}
          />
        </div>
      )}

      <main className="content">
        {tab === 'areas' && (
          <AreasTab areas={areas} snapshot={snapshot} run={run} busy={busy} />
        )}
        {tab === 'stats' && <StatsTab run={run} busy={busy} />}
        {tab === 'network' && <NetworkTab snapshot={snapshot} run={run} busy={busy} />}
        {tab === 'chat' && <ChatTab />}
        {tab === 'journal' && <JournalTab snapshot={snapshot} />}
        {tab === 'genes' && (
          <GenesTab openWorks={openWorks} onWorksClosed={() => setOpenWorks(false)} />
        )}
        {tab === 'agents' && <AgentsTab />}
        {tab === 'prompts' && <PromptsTab />}
        {tab === 'update' && <UpdateTab />}
        {tab === 'settings' && (
          <SettingsTab settings={settings} snapshot={snapshot} onSaved={setSettings} />
        )}
      </main>
    </div>
  )
}
