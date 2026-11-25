import { useCallback, useEffect, useState } from 'react'
import type {
  CloneStatus,
  CopyFirstRunSnapshot,
  CopyMode,
  CopyModeOption,
  DaemonStatus,
  LineageInfo,
  ProgramVersion,
  Settings,
  Snapshot
} from '@shared/types'
import { api } from '../api'
import { TZ_LABEL, formatDateTime } from '../time'
import { answerConfirmText, firstRunView, modeConfirmText, modeRows, type Tone } from './copy-view'
import { lineageRows as lineageRowsOf } from './lineage-view'

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
    }
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [loadCopy])

  const lineageRows = lineageRowsOf(lineage)
  const first = firstRunView(copySnapshot)
  const modes = first.modes.length ? first.modes : FALLBACK_MODES
  const currentMode = (cloneStatus?.mode || '') as CopyMode | ''
  // ⚠️ Пустой режим — это «не выбран», а не «первый в списке»: показываем
  // честную заглушку, иначе окно выдавало бы «свой GitHub» за текущий.
  const selectedMode = modeChoice || currentMode || ''
  const selectedOption = modes.find((option) => option.id === selectedMode)
  const copyRows = modeRows(cloneStatus)

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
        ) : first.asked ? (
          <p className="muted small">
            Вопрос первого запуска уже задан:{' '}
            {copySnapshot?.first_run?.agreed ? 'согласие' : 'отказ'}. Режим копии
            меняется ниже.
          </p>
        ) : (
          <>
            <p className="title">{first.title}</p>
            <table className="grid compact">
              <tbody>
                <tr>
                  <td className="muted">Репозиторий</td>
                  <td>{first.repository}</td>
                </tr>
                <tr>
                  <td className="muted">Папка копии</td>
                  <td>{first.folder}</td>
                </tr>
              </tbody>
            </table>
            <p className="muted small">Что это даёт:</p>
            <ul className="muted small">
              {(first.gives.length ? first.gives : ['—']).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <p className="muted small">Если отказаться:</p>
            <ul className="muted small">
              {(first.ifRefuse.length ? first.ifRefuse : ['—']).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <p className="muted small">{first.who}</p>
            <div className="row">
              <button
                className="primary"
                onClick={() => void answerFirstRun(true)}
                disabled={busy}
              >
                Согласиться
              </button>
              <button
                className="ghost"
                onClick={() => void answerFirstRun(false)}
                disabled={busy}
              >
                Отказаться
              </button>
            </div>
          </>
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
