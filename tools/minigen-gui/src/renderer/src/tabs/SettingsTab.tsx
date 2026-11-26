import { useCallback, useEffect, useState } from 'react'
import type {
  CloneStatus,
  CopyFirstRunSnapshot,
  CopyMode,
  CopyModeOption,
  CopyTokenIssue,
  CopyTokenRequest,
  CopyTokenStatus,
  DaemonStatus,
  DbImportPlan,
  DbStatus,
  LineageInfo,
  ProgramVersion,
  Settings,
  Snapshot
} from '@shared/types'
import { api } from '../api'
import { TZ_LABEL, formatDateTime } from '../time'
import { answerConfirmText, firstRunView, modeConfirmText, modeRows, type Tone } from './copy-view'
import CopyFirstRunQuestion from './copy-first-run'
import {
  DB_DECISIONS,
  dbPlanSummary,
  dbQuestions,
  dbStatusRows,
  type DbDecision
} from './db-view'
import { lineageRows as lineageRowsOf } from './lineage-view'
import {
  accessCheckText,
  issueConfirmText,
  maskToken,
  requestConfirmText,
  tokenIssueView,
  tokenRequestResultText,
  tokenRequestRows,
  tokenSetConfirmText,
  tokenSetResultText,
  tokenStateView,
  tokenStatusRows
} from './token-view'

interface Props {
  settings: Settings | null
  snapshot: Snapshot | null
  onSaved: (settings: Settings) => void
}

/**
 * Режимы копии — запасной список, если мост не привёл свой (значения — по
 * заказу владельца 25.11, промт #131). ⚠️ Это не выдумка окна: три режима и их
 * смысл заданы заказом, окно лишь подстраховывает случай пустого ответа.
 */
const FALLBACK_MODES: CopyModeOption[] = [
  {
    id: 'own',
    label: 'Свой репозиторий на GitHub',
    hint: 'Копия заводит свой репозиторий на GitHub, ветки и PR — в общий репозиторий программы. Обмен наработками доступен.'
  },
  {
    id: 'shared',
    label: 'Папка в общем репозитории',
    hint: 'Правки и PR идут прямо в общий репозиторий программы. Обмен наработками доступен.'
  },
  {
    id: 'local',
    label: 'Только локально, без сети',
    hint: 'Правки идут в локальный git-репозиторий на этой машине (история, откат, diff). PR не открывается, обмен с другими копиями недоступен.'
  }
]

/** Слово для чипа тона — коротко и честно, без выдумки. */
const TONE_TEXT: Record<Tone, string> = {
  ok: 'ок',
  warn: 'внимание',
  bad: 'неизвестно'
}

/**
 * Ответ моста на сохранение токена.
 *
 * ⚠️ В обёртке `api.copyTokenSet` объявлены только поля состояния, а мост
 * присылает ещё и `why`/`error`/`ok` — без них окно не сказало бы, принят
 * токен или нет.
 */
type TokenSetResult = CopyTokenStatus & {
  bound?: boolean
  ok?: boolean
  why?: string
  error?: string
}

/** Класс баннера по тону: «ок» — обычный, «внимание» и «плохо» — свои. */
const bannerClass = (tone: Tone): string =>
  tone === 'warn' ? 'banner warn' : tone === 'bad' ? 'banner bad' : 'banner'

export default function SettingsTab({ settings, snapshot, onSaved }: Props) {
  const [form, setForm] = useState<Settings | null>(settings)
  const [note, setNote] = useState('')
  const [daemons, setDaemons] = useState<DaemonStatus | null>(null)
  const [version, setVersion] = useState<ProgramVersion | null>(null)
  const [lineage, setLineage] = useState<LineageInfo | null>(null)
  const [copySnapshot, setCopySnapshot] = useState<CopyFirstRunSnapshot | null>(null)
  const [cloneStatus, setCloneStatus] = useState<CloneStatus | null>(null)
  const [copyError, setCopyError] = useState('')
  const [statusError, setStatusError] = useState('')
  const [modeChoice, setModeChoice] = useState<CopyMode | ''>('')
  const [busy, setBusy] = useState(false)
  // ── токен копии (заказ владельца 25.11, промт #130) ──────────────────────
  const [tokenStatus, setTokenStatus] = useState<CopyTokenStatus | null>(null)
  const [tokenRequests, setTokenRequests] = useState<CopyTokenRequest[] | null>(null)
  const [tokenError, setTokenError] = useState('')
  const [requestsError, setRequestsError] = useState('')
  const [tokenInput, setTokenInput] = useState('')
  const [githubNote, setGithubNote] = useState('')
  const [githubTone, setGithubTone] = useState<Tone>('ok')
  const [requestsNote, setRequestsNote] = useState('')
  const [requestsTone, setRequestsTone] = useState<Tone>('ok')
  /** Выпущенный токен — значение показывается один раз, отдельным блоком. */
  const [issued, setIssued] = useState<CopyTokenIssue | null>(null)
  const [issuedShown, setIssuedShown] = useState(false)
  // ── локальная база «МиниГена» (промт #92) ────────────────────────────────
  const [dbStatus, setDbStatus] = useState<DbStatus | null>(null)
  const [dbPlan, setDbPlan] = useState<DbImportPlan | null>(null)
  const [dbDecisions, setDbDecisions] = useState<Record<string, DbDecision>>({})
  const [dbNote, setDbNote] = useState('')
  const [dbError, setDbError] = useState('')

  useEffect(() => {
    if (settings && !form) setForm(settings)
  }, [settings, form])

  /**
   * Вопрос первого запуска и состояние копии — тоже по таймеру: окно
   * открывается раньше моста, и разовый запрос падает («мост не запущен»).
   */
  const loadCopy = useCallback(async () => {
    try {
      setCopySnapshot(await api.copyFirstRun())
      setCopyError('')
    } catch (err) {
      setCopyError(`вопрос первого запуска не принесли: ${String((err as Error).message || err)}`)
    }
    try {
      setCloneStatus(await api.cloneStatus())
      setStatusError('')
    } catch (err) {
      setStatusError(`состояние копии не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  /**
   * Токен копии и заявки на него — тоже по таймеру: окно открывается раньше
   * моста, и разовый запрос падает («мост не запущен»).
   */
  const loadTokens = useCallback(async () => {
    try {
      setTokenStatus(await api.copyTokenStatus())
      setTokenError('')
    } catch (err) {
      setTokenError(`состояние токена не принесли: ${String((err as Error).message || err)}`)
    }
    try {
      const data = await api.copyTokenRequests()
      setTokenRequests(Array.isArray(data?.requests) ? data.requests : [])
      setRequestsError('')
    } catch (err) {
      setRequestsError(`заявки на токен не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  /**
   * Состояние локальной базы — по таймеру, как и всё остальное: окно
   * открывается раньше моста, и разовый запрос падает («мост не запущен»).
   */
  const loadDb = useCallback(async () => {
    try {
      setDbStatus(await api.dbStatus())
      setDbError('')
    } catch (err) {
      setDbError(`состояние базы не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  useEffect(() => {
    // ⚠️ Всё, что читаем с моста, спрашиваем **по таймеру**, а не один раз:
    // окно открывается раньше моста, и разовый запрос в этот момент падает
    // («мост не запущен») ⇒ поле оставалось пустым навсегда («Версия:
    // неизвестна», «Родословная не прочитана» — живой случай 24.11).
    const load = () => {
      void api.daemonsStatus().then(setDaemons).catch(() => undefined)
      void api.programVersion().then(setVersion).catch(() => undefined)
      void api.lineageShow().then((data) => setLineage(data?.lineage ?? null)).catch(() => undefined)
      void loadCopy()
      void loadTokens()
      void loadDb()
    }
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [loadCopy, loadTokens, loadDb])

  const lineageRows = lineageRowsOf(lineage)
  const first = firstRunView(copySnapshot)
  const modes = first.modes.length ? first.modes : FALLBACK_MODES
  const currentMode = (cloneStatus?.mode || '') as CopyMode | ''
  // ⚠️ Пустой режим — это «не выбран», а не «первый в списке»: показываем
  // честную заглушку, иначе окно выдавало бы «свой GitHub» за текущий.
  const selectedMode = modeChoice || currentMode || ''
  const selectedOption = modes.find((option) => option.id === selectedMode)
  const copyRows = modeRows(cloneStatus)
  // ── токен копии: строки карточки «GitHub» и заявки ───────────────────────
  const tokenRows = tokenStatusRows(tokenStatus, cloneStatus)
  const tokenState = tokenStateView(tokenStatus)
  const requestRows = tokenRequestRows(tokenRequests)
  const issuedView = tokenIssueView(issued)
  const hasToken = tokenStatus?.has_token === true
  const requestsSection = form?.copyRequestsSection || 'MiniGen-tokens'

  if (!form) return <section><p className="muted">Загружаю настройки…</p></section>

  const save = async () => {
    const saved = await api.saveSettings(form)
    onSaved(saved)
    setNote('сохранено')
  }

  const daemonAction = async (action: 'start' | 'stop' | 'restart', name?: string) => {
    setNote(`${action}: ${name ?? 'все'}…`)
    if (action === 'start') await api.daemonsStart(name)
    if (action === 'stop') await api.daemonsStop(name)
    if (action === 'restart') await api.daemonsRestart(name)
    setDaemons(await api.daemonsStatus())
    setNote(`${action}: готово`)
  }

  /**
   * Ответ на вопрос первого запуска — решение владельца: согласие или отказ.
   * ⚠️ Отказ не ломает программу (копия просто работает локально), поэтому
   * оба ответа равноправны и оба спрашивают подтверждения.
   */
  const answerFirstRun = async (agree: boolean) => {
    if (!copySnapshot) return
    if (!window.confirm(answerConfirmText(agree, copySnapshot.offer))) return
    setBusy(true)
    try {
      const res = await api.copyAnswer(agree)
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не принял ответ')
        return
      }
      setNote(
        res.why || (agree ? 'согласие принято' : 'отказ принят — копия работает локально')
      )
      await loadCopy()
    } catch (err) {
      setNote(`ответ не принят: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Смена режима копии — тоже решение владельца, поэтому с подтверждением. */
  const applyMode = async () => {
    const option = modes.find((item) => item.id === selectedMode)
    if (!option) {
      setNote('выберите режим')
      return
    }
    if (!window.confirm(modeConfirmText(option))) return
    setBusy(true)
    try {
      const res = await api.copyMode(option.id)
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не сменил режим')
        return
      }
      setNote(res.why || `режим копии: ${option.label}`)
      setModeChoice('')
      await loadCopy()
    } catch (err) {
      setNote(`режим не сменили: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Сохранить токен, полученный от владельца.
   *
   * ⚠️ Перед записью — подтверждение: токен ложится в стор на машине (вне
   * репозитория), и в чат его вставлять не надо.
   */
  const saveToken = async () => {
    const value = tokenInput.trim()
    if (!value) {
      setGithubTone('bad')
      setGithubNote('вставьте значение токена')
      return
    }
    if (!window.confirm(tokenSetConfirmText())) return
    setBusy(true)
    try {
      const res = (await api.copyTokenSet({ token: value })) as TokenSetResult
      setGithubTone(res.ok === false ? 'bad' : 'ok')
      setGithubNote(tokenSetResultText(res))
      // Значение из поля убираем: секрет в сторе, лишний раз в окне он не нужен.
      if (res.ok !== false) setTokenInput('')
      await loadTokens()
    } catch (err) {
      setGithubTone('bad')
      setGithubNote(`токен не сохранили: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Проверка доступа: токен есть и выдан под эту папку — или нет. */
  const checkAccess = async () => {
    setBusy(true)
    try {
      const view = accessCheckText(await api.copyTokenCheck())
      setGithubTone(view.tone)
      setGithubNote(view.text)
    } catch (err) {
      setGithubTone('bad')
      setGithubNote(`доступ не проверили: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Выгрузить базу в один файл (промт #92).
   *
   * ⚠️ Файл с данными уходит с машины по решению человека: путь выбирает
   * оператор в диалоге (главный процесс), а данные берёт мост.
   */
  const exportDb = async () => {
    setBusy(true)
    setDbError('')
    try {
      const res = await api.dbExport()
      if (res.canceled) {
        setDbNote('выгрузка отменена')
        return
      }
      if (res.ok === false) {
        setDbError(res.error || 'выгрузить не вышло')
        return
      }
      setDbNote(`выгружено: ${res.path} (записей: ${res.total ?? 0})`)
    } catch (err) {
      setDbError(`выгрузить не вышло: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Импорт базы: сначала **разбор** файла (ничего не меняя), потом — решение
   * оператора по спорным записям.
   */
  const importDb = async () => {
    setBusy(true)
    setDbError('')
    try {
      const picked = await api.dbPickFile()
      if (!picked?.ok || !picked.path) {
        setDbNote('импорт отменён')
        return
      }
      const plan = await api.dbInspect(picked.path)
      if (plan.ok === false) {
        setDbError(plan.error || 'файл не разобран')
        setDbPlan(null)
        return
      }
      setDbPlan(plan)
      setDbDecisions({})
      setDbNote('файл разобран — посмотрите план и решите спорные записи')
    } catch (err) {
      setDbError(`разбор не прошёл: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Залить данные из файла — с решениями оператора по конфликтам. */
  const applyDbImport = async () => {
    if (!dbPlan?.path) return
    setBusy(true)
    setDbError('')
    try {
      const res = await api.dbImport(dbPlan.path, dbDecisions)
      if (res.ok === false) {
        setDbError(res.error || 'импорт не прошёл')
        return
      }
      setDbNote(res.why || 'импорт закончен')
      setDbPlan(null)
      setDbDecisions({})
      await loadDb()
    } catch (err) {
      setDbError(`импорт не прошёл: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Заявка на токен — уходит владельцу в его «МиниГен» (в раздел КАРМАНА).
   */
  const requestToken = async () => {
    setBusy(true)
    try {
      const res = await api.copyTokenRequest(undefined, requestsSection)
      setGithubTone(res.ok === false ? 'bad' : 'ok')
      setGithubNote(tokenRequestResultText(res))
    } catch (err) {
      setGithubTone('bad')
      setGithubNote(`заявка не ушла: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Выпуск токена по заявке (действие владельца).
   *
   * ⚠️ Значение токена показывается **один раз** — отдельным блоком под
   * маской; в заявках и в переписке его нет.
   */
  const issueToken = async (request: CopyTokenRequest | undefined) => {
    if (!request) {
      setRequestsTone('bad')
      setRequestsNote('заявка не найдена — обновите список')
      return
    }
    if (!window.confirm(issueConfirmText(request))) return
    setBusy(true)
    try {
      const res = await api.copyTokenIssue({
        instance_id: request.instance_id,
        folder: request.folder,
        name: request.name
      })
      const view = tokenIssueView(res)
      setRequestsTone(view.tone)
      setRequestsNote(view.text)
      setIssued(view.token ? res : null)
      setIssuedShown(false)
      await loadTokens()
    } catch (err) {
      setRequestsTone('bad')
      setRequestsNote(`токен не выпустили: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Закрепить копию (TOFU): запомнить отпечаток подписи, чтобы заявки этой
   * копии с другим отпечатком считались подделкой.
   */
  const trustCopy = async (request: CopyTokenRequest | undefined) => {
    if (!request) {
      setRequestsTone('bad')
      setRequestsNote('заявка не найдена — обновите список')
      return
    }
    if (!window.confirm(requestConfirmText(request))) return
    setBusy(true)
    try {
      const res = await api.copyTokenTrust(request.instance_id, request.key_id)
      if (res.ok === false) {
        setRequestsTone('bad')
        setRequestsNote(res.error || 'копию не закрепили')
        return
      }
      setRequestsTone('ok')
      setRequestsNote(res.why || 'копия закреплена')
      await loadTokens()
    } catch (err) {
      setRequestsTone('bad')
      setRequestsNote(`копию не закрепили: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <div className="row head">
        <h2>Настройки</h2>
        <button className="primary" onClick={() => void save()}>
          Сохранить
        </button>
      </div>
      {note && <div className="banner">{note}</div>}

      <div className="card">
        <h3>Программа</h3>
        <table className="grid compact">
          <tbody>
            <tr>
              <td>Версия</td>
              <td className="muted">
                {version?.commit ? `коммит ${version.commit}` : 'неизвестна'}
                {version?.installed_at
                  ? ` · установлено ${formatDateTime(version.installed_at)}`
                  : ''}
              </td>
            </tr>
            <tr>
              <td>Время</td>
              <td className="muted">{TZ_LABEL}</td>
            </tr>
            <tr>
              <td>Запущена из</td>
              <td className="muted">{version?.running_from ?? '—'}</td>
            </tr>
            <tr>
              <td>Исходники (для правок и PR)</td>
              <td className="muted">{version?.source || '—'}</td>
            </tr>
          </tbody>
        </table>
        <p className="muted small">
          Программа стоит отдельно от репозитория — её версия не зависит от того,
          какая ветка checkout в исходниках.
        </p>
        <p className="muted small">
          Время программа везде считает и показывает по часовому поясу{' '}
          <strong>{TZ_LABEL}</strong>: журнал, статистика, шаги обновления,
          «установлено» — всё в нём, независимо от зоны машины, на которой
          программа стоит.
        </p>
      </div>

      <div className="card">
        <h3>База «МиниГена»</h3>
        <p className="muted small">
          Локальная база программы: промты, планы и всё, что «МиниГен» копит
          на этой машине. Её можно выгрузить в один файл и залить в другую
          копию «МиниГена» на другом компьютере. Структура базы не привязана к
          версии программы, поэтому данные переживают обновление, а чужое и
          будущее не выбрасывается — лежит в неразобранном и уходит в
          следующей выгрузке.
        </p>
        {dbError && <div className="banner bad">{dbError}</div>}
        {dbNote && <div className="banner">{dbNote}</div>}
        <table className="grid compact">
          <tbody>
            {dbStatusRows(dbStatus).map((row) => (
              <tr key={row.label}>
                <td className="muted">{row.label}</td>
                <td>{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row">
          <button onClick={() => void exportDb()} disabled={busy}>
            Выгрузить базу
          </button>
          <button onClick={() => void importDb()} disabled={busy}>
            Импортировать базу
          </button>
        </div>
        {dbPlan && (
          <>
            <p className="muted small" style={{ marginTop: 8 }}>
              План импорта: {dbPlanSummary(dbPlan)}
            </p>
            {dbQuestions(dbPlan).length > 0 && (
              <ul className="muted small">
                {dbQuestions(dbPlan).map((question, index) => (
                  <li key={`${index}-${question}`}>{question}</li>
                ))}
              </ul>
            )}
            {(dbPlan.conflicts ?? []).length > 0 && (
              <table className="grid compact">
                <tbody>
                  {dbPlan.conflicts.map((conflict) => (
                    <tr key={conflict.id}>
                      <td className="muted">
                        {conflict.title} / {conflict.key}
                      </td>
                      <td>
                        <select
                          value={dbDecisions[conflict.id] ?? 'keep'}
                          onChange={(event) =>
                            setDbDecisions((current) => ({
                              ...current,
                              [conflict.id]: event.target.value as DbDecision
                            }))
                          }
                        >
                          {DB_DECISIONS.map((option) => (
                            <option key={option.id} value={option.id}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="row">
              <button
                className="primary"
                onClick={() => void applyDbImport()}
                disabled={busy}
              >
                Импортировать
              </button>
              <button
                onClick={() => {
                  setDbPlan(null)
                  setDbDecisions({})
                }}
                disabled={busy}
              >
                Отмена
              </button>
            </div>
            <p className="muted small">
              Спорная запись без вашего решения остаётся нетронутой: молча
              ничего не перезаписывается.
            </p>
          </>
        )}
      </div>

      <div className="card">
        <h3>Родословная копии</h3>
        {lineageRows.length === 0 ? (
          <p className="muted small">Родословная не прочитана.</p>
        ) : (
          <table className="grid compact">
            <tbody>
              {lineageRows.map((row) => (
                <tr key={row.label}>
                  <td className="muted">{row.label}</td>
                  <td>{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="muted small">
          Это кто эта программа в общей паутине копий: от какой версии форк, кто
          родитель по гену, сколько генов принято. Вся паутина — во вкладке
          «Гены».
        </p>
      </div>

      <div className="card">
        <h3>Копия «МиниГена»</h3>
        <p className="muted small">
          Настроить копию: режим работы, обмен наработками и вопрос первого
          запуска.
        </p>
        {copyError && <div className="banner bad">{copyError}</div>}
        {statusError && <div className="banner bad">{statusError}</div>}

        {!copySnapshot ? (
          <p className="muted small">
            Мост не ответил — вопрос первого запуска не задан, состояние копии
            неизвестно.
          </p>
        ) : (
          /* ⚠️ Тот же вопрос, что и в окне при первом открытии (промт #131) —
             один компонент на два места: если тексты разойдутся, окно однажды
             скажет владельцу не то. */
          <CopyFirstRunQuestion
            snapshot={copySnapshot}
            busy={busy}
            onAgree={() => void answerFirstRun(true)}
            onRefuse={() => void answerFirstRun(false)}
          />
        )}

        <p className="muted small" style={{ marginTop: 8 }}>
          Режим копии
        </p>
        <div className="row">
          <select
            value={selectedMode}
            onChange={(event) => setModeChoice(event.target.value as CopyMode)}
          >
            {!selectedMode && <option value="">режим не выбран</option>}
            {modes.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            className="primary"
            onClick={() => void applyMode()}
            disabled={busy || !selectedOption}
          >
            Применить
          </button>
        </div>
        <p className="muted small">{selectedOption?.hint ?? '—'}</p>

        <table className="grid compact">
          <tbody>
            {copyRows.map((row) => (
              <tr key={row.label}>
                <td className="muted">{row.label}</td>
                <td>
                  <span className={`chip ${row.tone}`}>{TONE_TEXT[row.tone]}</span>{' '}
                  <span className="muted small">{row.detail}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">
          В локальном режиме обмен с другими копиями недоступен — об этом
          сказано прямо, а не спрятано за пустым списком. Ручной обмен папками
          (вкладка «Гены») при этом работает.
        </p>
      </div>

      <div className="card">
        <h3>GitHub</h3>
        <p className="muted small">
          Репозиторий копии и токен доступа к своей папке в общем репозитории
          программы. Токен выпускает владелец — по заявке, которая приходит
          ему в его собственный «МиниГен».
        </p>
        {tokenError && <div className="banner bad">{tokenError}</div>}
        {githubNote && <div className={bannerClass(githubTone)}>{githubNote}</div>}

        <p>
          <span className={`chip ${tokenState.tone}`}>{tokenState.label}</span>{' '}
          <span className="muted small">{tokenState.detail}</span>
        </p>

        <table className="grid compact">
          <tbody>
            {tokenRows.map((row) => (
              <tr key={row.label}>
                <td className="muted">{row.label}</td>
                <td>{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <p className="muted small" style={{ marginTop: 8 }}>
          Токен, который дал владелец
        </p>
        <div className="row">
          <input
            type="password"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            placeholder="вставьте код из выпуска (секрет.доказательство)"
            autoComplete="off"
          />
          <button
            className="primary"
            onClick={() => void saveToken()}
            disabled={busy || !tokenInput.trim()}
          >
            Сохранить токен
          </button>
          <button className="ghost" onClick={() => void checkAccess()} disabled={busy}>
            Проверить доступ
          </button>
        </div>
        {!hasToken && (
          <div className="row">
            <button className="ghost" onClick={() => void requestToken()} disabled={busy}>
              Запросить токен
            </button>
          </div>
        )}
        <p className="muted small">
          Значение токена в чат не вставляют: в переписку и в отчёты оно не
          попадает. Программа хранит его в сторе на машине — вне репозитория.
          Вставлять надо код из выпуска целиком: секрет и доказательство
          (одной строкой). Без доказательства токен не подтвердит свою папку.
        </p>
      </div>

      <div className="card">
        <h3>Заявки на токен</h3>
        <p className="muted small">
          Заявки копий, которые ждут токен. Значения токена в заявках нет: видно,
          кто прислал, чья это копия и в каком состоянии подпись.
        </p>
        {requestsError && <div className="banner bad">{requestsError}</div>}
        {requestsNote && (
          <div className={bannerClass(requestsTone)}>{requestsNote}</div>
        )}

        {requestRows.length === 0 ? (
          <p className="muted small">
            Заявок нет: в разделе «{requestsSection}» пока пусто (или раздел
            недоступен).
          </p>
        ) : (
          <table className="grid compact">
            <thead>
              <tr>
                <th>Кто прислал</th>
                <th>Чья это копия</th>
                <th>Подпись</th>
                <th>Папка</th>
                <th>Когда</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {requestRows.map((row, index) => {
                const request = tokenRequests?.[index]
                return (
                  <tr key={row.id === '—' ? `${row.instance_id}-${index}` : row.id}>
                    <td>{row.who}</td>
                    <td>
                      <span className={`chip ${row.tone}`}>{row.relation}</span>{' '}
                      <span className="muted small">{row.relation_detail}</span>
                    </td>
                    <td className="muted small">{row.signature}</td>
                    <td className="muted small">{row.folder}</td>
                    <td className="muted small">{row.when}</td>
                    <td className="actions">
                      <button
                        className="ghost"
                        onClick={() => void issueToken(request)}
                        disabled={busy || !request}
                      >
                        Выпустить токен
                      </button>
                      <button
                        className="ghost"
                        onClick={() => void trustCopy(request)}
                        disabled={busy || !request}
                      >
                        Закрепить копию
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {issuedView.token && (
        <div className="card">
          <h3>Выпущенный токен</h3>
          <p className="muted small">
            Значение показывают один раз: второй раз мост его не отдаст — в
            сторе остаётся только отпечаток.
          </p>
          <div className="row">
            <span className="title">
              {issuedShown ? issuedView.token : maskToken(issuedView.token)}
            </span>
            <button
              className="ghost"
              onClick={() => setIssuedShown(!issuedShown)}
              disabled={busy}
            >
              {issuedShown ? 'Скрыть' : 'Показать'}
            </button>
          </div>
          <p className="muted small">
            Передайте копии этот код целиком: в чат его вставлять не надо — в
            переписку и в отчёты значение токена попадать не должно.
          </p>
        </div>
      )}

      <div className="card">
        <h3>Демоны «МиниГена»</h3>
        <p className="muted small">
          Сторожа аккаунтов, сети и сессий. Старая веб-панель (:8099) и страница
          управления (:8098) архивированы — демонов держит эта программа.
        </p>
        <p className="muted small">
          Надзиратель:{' '}
          {daemons?.supervisor ? (
            <>
              {daemons.supervisor.is_self ? 'это окно' : `pid ${daemons.supervisor.pid}`}
              {daemons.supervisor.alive ? '' : ' (процесса нет)'}
              {daemons.supervisor.path ? ` · ${daemons.supervisor.path}` : ''}
            </>
          ) : (
            'сейчас никто (демоны поднимают себя сами)'
          )}
        </p>
        <table className="grid compact">
          <thead>
            <tr>
              <th>Демон</th>
              <th>За что отвечает</th>
              <th>Состояние</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {(daemons?.daemons ?? []).map((daemon) => (
              <tr key={daemon.name}>
                <td>{daemon.name}</td>
                <td className="muted">{daemon.role}</td>
                <td>
                  <span className={`dot ${daemon.alive ? 'ok' : 'bad'}`} />
                  {daemon.alive ? `жив (pid ${daemon.pids.join(', ')})` : 'не работает'}
                </td>
                <td className="actions">
                  <button
                    className="ghost"
                    onClick={() => void daemonAction('restart', daemon.name)}
                  >
                    Перезапустить
                  </button>
                  {daemon.alive ? (
                    <button className="ghost" onClick={() => void daemonAction('stop', daemon.name)}>
                      Остановить
                    </button>
                  ) : (
                    <button className="ghost" onClick={() => void daemonAction('start', daemon.name)}>
                      Запустить
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="ghost" onClick={() => void daemonAction('restart')}>
            Перезапустить всех
          </button>
        </div>
      </div>

      <div className="card">
        <div className="grid2">
          <label>
            Обновление окна, мс
            <input
              type="number"
              value={form.refreshMs}
              onChange={(e) => setForm({ ...form, refreshMs: Number(e.target.value) })}
            />
          </label>
          <label>
            Строк журнала в хвосте
            <input
              type="number"
              value={form.journalTail}
              onChange={(e) => setForm({ ...form, journalTail: Number(e.target.value) })}
            />
          </label>
          <label>
            Область с конфигом провайдера (для чата)
            <input
              value={form.modelArea}
              onChange={(e) => setForm({ ...form, modelArea: e.target.value })}
            />
          </label>
          <label>
            Модель (пусто = по умолчанию из config.yaml)
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              placeholder="MiniMax-M3"
            />
          </label>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={form.confirmDangerous}
            onChange={(e) => setForm({ ...form, confirmDangerous: e.target.checked })}
          />
          Подтверждать опасные действия нейросети
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={form.manageDaemons}
            onChange={(e) => setForm({ ...form, manageDaemons: e.target.checked })}
          />
          Программа следит за демонами и поднимает упавшего
        </label>
      </div>

      <div className="card">
        <h3>Хранилище промтов</h3>
        <div className="grid2">
          <label>
            Где хранить промты
            <select
              value={form.promptStore}
              onChange={(e) =>
                setForm({ ...form, promptStore: e.target.value as Settings['promptStore'] })
              }
            >
              <option value="local">В базе «МиниГена»</option>
              <option value="karman">На внешнем ресурсе (Карман)</option>
            </select>
          </label>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={form.promptAutoDispatch}
            onChange={(e) => setForm({ ...form, promptAutoDispatch: e.target.checked })}
          />
          Раздавать промты рабочим областям без живого оператора
        </label>
        <p className="muted small">
          Автораздача идёт только для промтов, помеченных владельцем как «авто»,
          и только в область своего проекта: раздел промта — это проект области.
        </p>
      </div>

      <div className="card">
        <h3>Совет специалистов</h3>
        <label className="check">
          <input
            type="checkbox"
            checked={form.councilEnabled}
            onChange={(e) => setForm({ ...form, councilEnabled: e.target.checked })}
          />
          Новое задание сначала обсуждает совет, потом решается
        </label>
        <div className="grid2">
          <label>
            Сколько ролей отвечают на задачу
            <input
              type="number"
              min={1}
              max={6}
              value={form.councilMaxRoles}
              onChange={(e) =>
                setForm({ ...form, councilMaxRoles: Number(e.target.value) })
              }
            />
          </label>
        </div>
        <p className="muted small">
          Каждый голос совета — это вызов модели: совет дорог и медленен, поэтому
          ролей по умолчанию немного. Решение совета не пересиливает прямую
          просьбу владельца — при конфликте приоритет у владельца.
        </p>
      </div>

      {snapshot && (
        <div className="card">
          <h3>Пути</h3>
          <table className="grid compact">
            <tbody>
              <tr>
                <td>Каталоги областей</td>
                <td className="muted">{snapshot.areas_root}</td>
              </tr>
              <tr>
                <td>Боевые демоны</td>
                <td className="muted">{snapshot.daemon_dir}</td>
              </tr>
              <tr>
                <td>Журнал</td>
                <td className="muted">{snapshot.journal_path}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
