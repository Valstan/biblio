import { useEffect, useState } from 'react'
import type { DaemonStatus, ProgramVersion, Settings, Snapshot } from '@shared/types'
import { api } from '../api'

interface Props {
  settings: Settings | null
  snapshot: Snapshot | null
  onSaved: (settings: Settings) => void
}

export default function SettingsTab({ settings, snapshot, onSaved }: Props) {
  const [form, setForm] = useState<Settings | null>(settings)
  const [note, setNote] = useState('')
  const [daemons, setDaemons] = useState<DaemonStatus | null>(null)
  const [version, setVersion] = useState<ProgramVersion | null>(null)

  useEffect(() => {
    if (settings && !form) setForm(settings)
  }, [settings, form])

  useEffect(() => {
    const load = () => api.daemonsStatus().then(setDaemons).catch(() => undefined)
    void load()
    void api.programVersion().then(setVersion).catch(() => undefined)
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [])

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
                  ? ` · установлено ${new Date(version.installed_at).toLocaleString('ru-RU')}`
                  : ''}
              </td>
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
