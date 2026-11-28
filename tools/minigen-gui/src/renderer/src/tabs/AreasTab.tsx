import { useMemo, useState } from 'react'
import type { AgentDeletePlan, AgentDeleteResult, Area, Snapshot } from '@shared/types'
import { api } from '../api'
import { verdictClass } from '../format'
import {
  AREA_COLUMNS,
  DEFAULT_AREA_SORT,
  activityOf,
  nextSort,
  sortAreas,
  sortColumnsHint,
  type AreaSortKey
} from './areas-sort'
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
  // ⚠️ Промт КАРМАНА #208: по умолчанию — **по активности** внутри двух
  // блоков (работающие сверху); блок «работающие сверху» держится всегда.
  const [sort, setSort] = useState(DEFAULT_AREA_SORT)
  const sorted = useMemo(() => sortAreas(areas, sort), [areas, sort])
  // Удаление агента (промт КАРМАНА #208): сначала план, потом подтверждение.
  const [deleting, setDeleting] = useState<{ slug: string; plan: AgentDeletePlan } | null>(null)
  const [deleteResult, setDeleteResult] = useState<AgentDeleteResult | null>(null)

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
   * списком, что и шапки, — поэтому колонка водителя не может «уехать» в
   * конец: она в том же списке, что и остальные (промт #177, п. 2, 10).
   *
   * ⚠️ Промт КАРМАНА #182: колонки «Режим» больше нет — ручного режима в
   * «МиниГене» не осталось (автомат аккаунтов всегда включён, выключить его
   * нечем). «Водитель» на экране стал «Сессиями», бывшие «Сессии» — «Состоянием».
   *
   * ⚠️ Промт КАРМАНА #208: колонки **«Работает» больше нет** — признак виден
   * теперь цветом всей строки (класс `row-running`), а порядок задан двумя
   * блоками (работающие сверху) и активностью внутри блока.
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
            <ActivityLine area={area} />
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
      case 'driver':
        // ⚠️ Промт КАРМАНА #182: на экране эта колонка называется «Сессии»
        // (словарь водителя прежний — поменялся только заголовок).
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

  /** Спросить план удаления агента (ничего ещё не удалено). */
  const askDelete = async (area: Area) => {
    setDeleteResult(null)
    await run(async () => {
      const plan = await api.deleteAgentPlan(area.slug)
      setDeleting({ slug: area.slug, plan })
    })
  }

  /** Подтверждение: удалить. */
  const confirmDelete = async () => {
    if (!deleting) return
    const slug = deleting.slug
    await run(async () => {
      const res = await api.deleteAgent(slug)
      setDeleteResult(res)
      if (res.ok) {
        setDeleting(null)
        setMessage(`агент ${slug} удалён`)
      } else {
        setMessage(`удаление не прошло: ${res.error ?? 'причина неизвестна'}`)
      }
    })
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

      {deleting && (
        <div className="card delete-card">
          <h3>Удаление агента: {deleting.plan.title || deleting.slug}</h3>
          <p className="muted small">
            Удаление <strong>необратимо</strong>: агента не будет ни в списке
            областей, ни среди запущенных, ни в разборе сессий. Ниже — что
            именно исчезнет.
          </p>
          <div className="delete-list">
            {deleting.plan.targets.map((target) => (
              <div className="delete-target" key={`${target.what}:${target.path}`}>
                <span className="delete-what">{target.what}</span>
                <span className="muted small">{target.path}</span>
                {!target.exists && <span className="muted small"> — на диске нет</span>}
              </div>
            ))}
            <div className="delete-target">
              <span className="delete-what">запись в настройке областей</span>
              <span className="muted small">{deleting.plan.slug}</span>
            </div>
          </div>
          {deleting.plan.note && (
            <p className="muted small">{deleting.plan.note}</p>
          )}
          {!deleting.plan.can_delete && (
            <div className="banner bad">
              <div>Удаление не проходит: {deleting.plan.why}</div>
              {deleting.plan.git.unpushed.length > 0 && (
                <>
                  <div>Не отправлено на GitHub (коммиты):</div>
                  <ul>
                    {deleting.plan.git.unpushed.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </>
              )}
              {deleting.plan.git.dirty.length > 0 && (
                <>
                  <div>Не закоммичено (файлы):</div>
                  <ul>
                    {deleting.plan.git.dirty.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          <div className="row">
            <button
              className="danger"
              disabled={busy || !deleting.plan.can_delete}
              onClick={() => void confirmDelete()}
            >
              Удалить
            </button>
            <button className="ghost" onClick={() => setDeleting(null)} disabled={busy}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {deleteResult && !deleteResult.ok && (
        <div className="banner bad">
          Удаление не прошло: {deleteResult.error ?? 'причина неизвестна'}
          {deleteResult.failed && deleteResult.failed.length > 0 && (
            <ul>
              {deleteResult.failed.map((item) => (
                <li key={item.what}>
                  {item.what}
                  {item.why ? ` — ${item.why}` : ''}
                </li>
              ))}
            </ul>
          )}
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
                  // Список столбцов в подсказке — из того же `AREA_COLUMNS`,
                  // что и шапки ⇒ подсказка всегда совпадает с экраном
                  // (промт КАРМАНА #182, п. 6; #208 — колонка «Работает» убрана
                  // и из шапки, и из подсказки).
                  title={`Сортировать по этому столбцу. Столбцы: ${sortColumnsHint()}`}
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
            // ⚠️ Промт КАРМАНА #208: работающий агент помечен **цветом всей
            // строки** (класс `row-running`) — колонки «Работает» больше нет.
            <tr key={area.slug} className={area.running ? 'row-running' : undefined}>
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
                  // Запасной **ручной** путь (промт КАРМАНА #182, п. 2):
                  // автоматика сама берёт свежий аккаунт, а эта кнопка — на
                  // случай сбоя автоматики.
                  title="Запасной ручной путь: взять следующий аккаунт у КАРМАНА, если автоматика не сработала"
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
                {/* ⚠️ Промт КАРМАНА #208: удаление агента целиком — рядом с
                    остальными действиями строки. Кнопка сначала показывает,
                    что именно исчезнет, и спрашивает подтверждение. */}
                <button
                  className="ghost danger-ghost"
                  disabled={busy}
                  onClick={() => void askDelete(area)}
                  title="Удалить агента целиком: папку проекта, запись из настроек, папку агента, паспорта ниток, авторизацию и ярлык. Перед удалением — проверка, что всё отправлено на GitHub."
                >
                  Удалить
                </button>
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
 * Счётчик активности области (промт КАРМАНА #208): сколько раз агент получал
 * аккаунты и обновления аккаунтов MiniMax из Кармана. Значение приходит из
 * моста — его считает автомат аккаунтов по журналу, а не человек на глаз.
 *
 * ⚠️ Порядок списка внутри блока строится по этому же счётчику (см.
 * `areas-sort.ts`), поэтому он виден не только в отчёте, но и в проверке.
 */
function ActivityLine({ area }: { area: Area }) {
  const activity = area.activity
  const total = activityOf(area)
  const granted = activity?.granted ?? 0
  const updated = activity?.updated ?? 0
  const title = activity?.last
    ? `Активность: ${total} (выдач: ${granted}, обновлений: ${updated}); последнее событие ${activity.last}`
    : `Активность: ${total} (выдач: ${granted}, обновлений: ${updated})`
  return (
    <div className="muted small activity" title={title}>
      активность: {total} · выдач {granted} · обновлений {updated}
    </div>
  )
}

/**
 * Ячейка «Сессии» (на экране так с 27.11, промт КАРМАНА #182; раньше колонка
 * называлась «Водитель»): **две небольшие кнопки** — «Миниген» и «Оператор»,
 * и короткая строка «сейчас: …», которая колонку не раздувает (полный текст —
 * во всплывающей подсказке).
 *
 * ⚠️ Положений **два**: «Миниген» и «Оператор» (в этом порядке). «Внешнее
 * управление» и «Оператор» — одно положение (внешнее управление).
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
