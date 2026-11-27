import { useMemo, useState } from 'react'
import type { Area, Snapshot } from '@shared/types'
import { api } from '../api'
import { verdictClass } from '../format'
import { AREA_COLUMNS, DEFAULT_AREA_SORT, nextSort, sortAreas, type AreaSortKey } from './areas-sort'
import { MODE_BUTTONS } from './areas-mode'
import { DRIVER_BUTTONS, driverHint, driverPosition } from './driver-view'

interface Props {
  areas: Area[]
  snapshot: Snapshot | null
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}

/**
 * Переключатель «водитель проекта» (промт КАРМАНА #174, слова владельца —
 * #177; **два** положения — заказ владельца 27.11): в каждый момент у проекта
 * один водитель сессий. Положений **два**: «Миниген» и «Оператор».
 * «Внешнее управление» и «Оператор» — одно и то же: управление внешнее, а
 * оператор или другой агент — без разницы.
 * «По правилу» в переключателе нет (владелец просил без него) — это правило
 * ядра для области, где водитель ещё не выбран явно; окно показывает, кто
 * водит фактически, и почему.
 */

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

  /**
   * Ячейка строки по колонке. Порядок ячеек задаётся `AREA_COLUMNS` — тем же
   * списком, что и шапки, — поэтому колонка «Водитель» не может «уехать» в
   * конец: она в том же списке, что и остальные (промт #177, п. 2, 10).
   */
  const cell = (key: AreaSortKey, area: Area) => {
    switch (key) {
      case 'area':
        return (
          <>
            <div className="title">{area.title}</div>
            <div className="muted small">
              {area.slug}
              {area.folder ? ` · ${area.folder}` : ''}
            </div>
          </>
        )
      case 'running':
        return (
          <>
            <span className={`dot ${area.running ? 'ok' : 'off'}`} />
            {area.running ? 'да' : 'нет'}
          </>
        )
      case 'account':
        return (
          <>
            <div>{area.account?.state ?? '—'}</div>
            {area.account?.email && <div className="muted small">{area.account.email}</div>}
          </>
        )
      case 'points':
        return <>{area.points?.points_today ?? '—'}</>
      case 'mode':
        // ⚠️ Промт КАРМАНА #177 (п. 1): «Режим» — две небольшие кнопки, обе
        // подписи всегда на виду, включённая выделена. ОДНА кнопка с
        // меняющейся надписью тут больше не годится.
        return (
          <div className="mode-switch">
            {MODE_BUTTONS.map((button) => (
              <button
                key={button.label}
                className={`mode-btn ${area.autoheal === button.autoheal ? 'on' : 'off'}`}
                disabled={busy}
                title={button.hint}
                aria-pressed={area.autoheal === button.autoheal}
                onClick={() =>
                  void run(() => api.setAutoheal(area.slug, button.autoheal))
                }
              >
                {button.label}
              </button>
            ))}
          </div>
        )
      case 'driver':
        return <DriverCell area={area} busy={busy} run={run} />
      case 'sessions':
        return area.sessions.length === 0 ? (
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
        )
      default:
        return null
    }
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
              Раздел КАРМАНА
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
              {AREA_COLUMNS.map((column) => (
                <td key={column.key} className={`col-${column.key}`}>
                  {cell(column.key, area)}
                </td>
              ))}
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

/**
 * Ячейка «Водитель» — узкая колонка сразу за «Режимом» (промт КАРМАНА #177,
 * п. 2; заказ владельца 27.11): **две небольшие кнопки**, как у «Режима» (не
 * выпадающее меню), и короткая строка «сейчас: …», которая колонку не
 * раздувает (полный текст — во всплывающей подсказке).
 *
 * ⚠️ Положений **два**: «Миниген» и «Оператор» (в этом порядке). «Внешнее
 * управление» и «Оператор» — одно и то же положение: управление внешнее, а
 * оператор или другой агент — без разницы.
 */
function DriverCell({
  area,
  busy,
  run
}: {
  area: Area
  busy: boolean
  run: (action: () => Promise<unknown>) => Promise<void>
}) {
  const position = driverPosition(area)
  const now = area.driverNow
  const byRule = now?.source === 'правило'
  const nowText = now?.title
    ? `сейчас: ${now.title}${byRule ? ' (по правилу)' : ''}`
    : 'сейчас: —'
  const nowTitle = now?.why ? `${nowText} — ${now.why}` : nowText
  return (
    <>
      <div className="driver-switch">
        {DRIVER_BUTTONS.map((button) => (
          <button
            key={button.value}
            className={`driver-btn ${position === button.value ? 'on' : 'off'}`}
            disabled={busy}
            // У включённой кнопки — полная подсказка: что значит положение и
            // кто водит на самом деле (и почему).
            title={position === button.value ? driverHint(area) : button.hint}
            aria-pressed={position === button.value}
            onClick={() =>
              void run(() => api.setDriver(area.slug, button.value))
            }
          >
            {button.label}
          </button>
        ))}
      </div>
      <div className="muted small driver-now" title={nowTitle}>
        {nowText}
      </div>
    </>
  )
}
