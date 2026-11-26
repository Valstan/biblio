import { useMemo, useState } from 'react'
import type { Area, Snapshot } from '@shared/types'
import { api } from '../api'
import { verdictClass } from '../format'
import { AREA_COLUMNS, DEFAULT_AREA_SORT, nextSort, sortAreas } from './areas-sort'

interface Props {
  areas: Area[]
  snapshot: Snapshot | null
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}

export default function AreasTab({ areas, snapshot, run, busy }: Props) {
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ slug: '', title: '', folder: '', karman: '' })
  const [message, setMessage] = useState('')
  // Сортировка по клику на шапку столбца (заказ владельца 24.11).
  // По умолчанию — по статусу «Работает», работающие сверху.
  const [sort, setSort] = useState(DEFAULT_AREA_SORT)
  const sorted = useMemo(() => sortAreas(areas, sort), [areas, sort])

  const submit = async () => {
    const res = await api.createArea(form)
    if (res.error) {
      setMessage(res.error)
      return
    }
    setMessage(`область ${res.slug} создана`)
    setCreating(false)
    setForm({ slug: '', title: '', folder: '', karman: '' })
  }

  return (
    <section>
      <div className="row head">
        <h2>Рабочие области</h2>
        <div className="row">
          <button className="ghost" onClick={() => void run(() => api.points())} disabled={busy}>
            Обновить очки
          </button>
          <button className="primary" onClick={() => setCreating((v) => !v)}>
            Создать область
          </button>
        </div>
      </div>

      {message && <div className="banner">{message}</div>}

      {creating && (
        <div className="card create">
          <div className="grid4">
            <label>
              slug (латиницей)
              <input
                value={form.slug}
                onChange={(e) => setForm({ ...form, slug: e.target.value })}
                placeholder="например sarafan2"
              />
            </label>
            <label>
              Название
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="Сарафан 2"
              />
            </label>
            <label>
              Рабочая папка
              <input
                value={form.folder}
                onChange={(e) => setForm({ ...form, folder: e.target.value })}
                placeholder="D:\valstan\REPO\sarafan"
              />
            </label>
            <label>
              Раздел КАРМАНа
              <input
                value={form.karman}
                onChange={(e) => setForm({ ...form, karman: e.target.value })}
                placeholder="SARAFAN"
              />
            </label>
          </div>
          <p className="muted small">
            Раздел КАРМАНа — имя проекта области в библиотеке промтов (например
            <code> SARAFAN</code>). По нему промт из «Хранилища промтов» уходит в
            эту область; если раздел не совпадает, задание не отправляется — оно
            ушло бы не туда.
          </p>
          <div className="row">
            <button className="primary" onClick={() => void submit()} disabled={busy}>
              Создать
            </button>
            <button className="ghost" onClick={() => setCreating(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <table className="grid">
        <thead>
          <tr>
            {AREA_COLUMNS.map((column) => (
              <th key={column.key}>
                <button
                  className={`sort${sort.key === column.key ? ' active' : ''}`}
                  onClick={() => setSort((current) => nextSort(current, column.key))}
                  title="Сортировать по этому столбцу"
                >
                  {column.label}
                  {sort.key === column.key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                </button>
              </th>
            ))}
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((area) => (
            <tr key={area.slug}>
              <td>
                <div className="title">{area.title}</div>
                <div className="muted small">
                  {area.slug}
                  {area.folder ? ` · ${area.folder}` : ''}
                </div>
              </td>
              <td>
                <span className={`dot ${area.running ? 'ok' : 'off'}`} />
                {area.running ? 'да' : 'нет'}
              </td>
              <td>
                <div>{area.account?.state ?? '—'}</div>
                {area.account?.email && <div className="muted small">{area.account.email}</div>}
              </td>
              <td>{area.points?.points_today ?? '—'}</td>
              <td>
                <button
                  className={`toggle ${area.autoheal ? 'on' : 'off'}`}
                  disabled={busy}
                  onClick={() =>
                    void run(() => api.setAutoheal(area.slug, !area.autoheal))
                  }
                >
                  {area.autoheal ? 'Автомат' : 'Вручную'}
                </button>
              </td>
              <td>
                {area.sessions.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  <div className="chips">
                    {area.sessions.slice(0, 3).map((session) => (
                      <span
                        key={session.session_id}
                        className={`chip ${verdictClass(session.verdict)}`}
                        title={session.reason}
                      >
                        {session.verdict}
                        {session.fill ? ` ${session.fill}%` : ''}
                      </span>
                    ))}
                    {area.sessions.length > 3 && (
                      <span className="chip muted">+{area.sessions.length - 3}</span>
                    )}
                  </div>
                )}
              </td>
              <td className="actions">
                <button className="ghost" disabled={busy} onClick={() => void run(() => api.start(area.slug))}>
                  Старт
                </button>
                <button className="ghost" disabled={busy} onClick={() => void run(() => api.stop(area.slug))}>
                  Стоп
                </button>
                <button
                  className="ghost"
                  disabled={busy}
                  onClick={() => void run(() => api.restart(area.slug))}
                >
                  Перезапуск
                </button>
                <button
                  className="ghost"
                  disabled={busy}
                  onClick={() => void run(() => api.relogin(area.slug))}
                  title="Взять следующий аккаунт у КАРМАНА"
                >
                  Новый вход
                </button>
                {area.folder && (
                  <button
                    className="ghost"
                    onClick={() => void api.openFolder(area.folder)}
                  >
                    Папка
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {snapshot && (
        <p className="muted small">
          Каталоги областей: {snapshot.areas_root} · боевые демоны: {snapshot.daemon_dir} · журнал:{' '}
          {snapshot.journal_path}
        </p>
      )}
    </section>
  )
}
