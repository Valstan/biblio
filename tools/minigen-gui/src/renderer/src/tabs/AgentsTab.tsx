import { useCallback, useEffect, useMemo, useState } from 'react'
import type { AgentsSnapshot, Area, KarmanChannel, Snapshot } from '@shared/types'
import { api } from '../api'
import { verdictClass } from '../format'
import {
  AREA_COLUMNS,
  DEFAULT_AREA_SORT,
  nextSort,
  sortAreas,
  sortColumnsHint,
  type AreaSortKey
} from './areas-sort'
import { DRIVER_BUTTONS, driverHint, driverPosition } from './driver-view'
import {
  applyConfirmText,
  channelView,
  createConfirmText,
  mergedRows,
  registrySummary,
  registryWarning,
  validateDraft,
  type AgentDraft,
  type AgentRow,
  type MergedRow
} from './agents-view'
import { requestSections, sectionsInUse, sectionsOf } from './agents-requests'

interface Props {
  areas: Area[]
  snapshot: Snapshot | null
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}

/**
 * Вкладка «Агенты» — **области и агенты в одном списке**.
 *
 * ⚠️ **Заказ владельца 28.11: «Области» и «Агенты» — одно и то же.**
 * Раньше в окне было две вкладки, и они показывали разное: агент, созданный
 * в «Агентах», не появлялся в «Рабочих областях» (дефект записи конфига
 * областей), и владелец решал, что программа не работает. Теперь список
 * один: **строка — это область**, а агент (если он есть) — её содержание.
 *
 * Что показывает:
 * - **канал КАРМАНА**: жив или не настроен, откуда взят секрет и сколько
 *   разделов (заявки уходят в раздел своего проекта);
 * - **реестр агентов**: slug, название, роль, репозиторий, область и
 *   число заявок — и разделы, куда эти заявки ушли;
 * - **живое состояние области**: работает ли, вход, очки, водитель сессий
 *   и состояние сессий;
 * - действия: «Восстановить» агента (по реестру с GitHub), «Создать
 *   агента», «Создать область» и **«Починить список»** — пересобрать
 *   список областей по машине (после потери локального конфига).
 *
 * Окно **ничего не дублирует**: данные спрашиваются у моста командами
 * `agents.list`, `karman.status`, `areas.list` и `areas.rebuild`.
 */
export default function AgentsTab({ areas, snapshot, run, busy }: Props) {
  const [registry, setRegistry] = useState<AgentsSnapshot | null>(null)
  const [channel, setChannel] = useState<KarmanChannel | null>(null)
  const [note, setNote] = useState('')
  const [creatingAgent, setCreatingAgent] = useState(false)
  const [creatingArea, setCreatingArea] = useState(false)
  const [draft, setDraft] = useState<AgentDraft>({ slug: '', title: '', role: '' })
  const [areaForm, setAreaForm] = useState({ slug: '', title: '', folder: '', karman: '' })
  // Сортировка по клику на шапку столбца (заказ владельца 24.11).
  // По умолчанию — по статусу «Работает», работающие сверху.
  const [sort, setSort] = useState(DEFAULT_AREA_SORT)

  const load = useCallback(async () => {
    try {
      const [agents, karman] = await Promise.all([
        api.agentsList(),
        api.karmanStatus().catch(() => null)
      ])
      setRegistry(agents)
      setChannel(karman)
      setNote('')
    } catch (err) {
      setNote(`агентов не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(timer)
  }, [load])

  const runHere = async (action: () => Promise<unknown>, ok: string): Promise<boolean> => {
    try {
      await action()
      setNote(ok)
      await load()
      return true
    } catch (err) {
      setNote(String((err as Error).message || err))
      return false
    }
  }

  // Порядок строк: живые области (в порядке сортировки), потом агенты,
  // у которых область не заведена.
  const sorted = useMemo(() => sortAreas(areas, sort), [areas, sort])
  const rows = useMemo(() => mergedRows(sorted, registry), [sorted, registry])
  const channelInfo = channelView(channel)
  const warning = registryWarning(registry)
  // Куда уходят заявки: по каждому агенту и по всем вместе (для канала).
  const byAgent = requestSections(registry)
  const usedSections = sectionsInUse(byAgent)

  /**
   * Ячейка строки по колонке. Порядок ячеек задаётся `AREA_COLUMNS` — тем же
   * списком, что и шапки, — поэтому колонка водителя не может «уехать» в
   * конец: она в том же списке, что и остальные (промт #177, п. 2, 10).
   *
   * ⚠️ Промт КАРМАНА #182: колонки «Режим» больше нет — ручного режима в
   * «МиниГене» не осталось (автомат аккаунтов всегда включён, выключить его
   * нечем). «Водитель» на экране стал «Сессии», бывшие «Сессии» — «Состоянием».
   */
  const cell = (key: AreaSortKey, row: MergedRow) => {
    const area = row.live
    switch (key) {
      case 'area':
        return (
          <>
            <div className="title">{area?.title || row.area}</div>
            <div className="muted small">
              {row.area}
              {area?.folder ? ` · ${area.folder}` : ''}
            </div>
            {row.areaMissing && (
              <div className="muted small">область не заведена</div>
            )}
          </>
        )
      case 'running':
        if (!area) return <span className="muted">—</span>
        return (
          <>
            <span className={`dot ${area.running ? 'ok' : 'off'}`} />
            {area.running ? 'да' : 'нет'}
          </>
        )
      case 'account':
        if (!area) return <span className="muted">—</span>
        return (
          <>
            <div>{area.account?.state ?? '—'}</div>
            {area.account?.email && <div className="muted small">{area.account.email}</div>}
          </>
        )
      case 'points':
        return <>{area?.points?.points_today ?? '—'}</>
      case 'driver':
        // ⚠️ Промт КАРМАНА #182: на экране эта колонка называется «Сессии»
        // (словарь водителя прежний — поменялся только заголовок).
        return area ? <DriverCell area={area} busy={busy} run={run} /> : <span className="muted">—</span>
      case 'sessions':
        if (!area || area.sessions.length === 0) {
          return <span className="muted">—</span>
        }
        return (
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

  /** Ячейка «Агент»: кто в этой области работает. */
  const agentCell = (row: MergedRow) => (
    <>
      <div className="title">{row.agent ? row.title : (row.live?.title || row.area)}</div>
      <div className="muted small">
        {row.agent ? row.agent.slug : 'агента нет'}
      </div>
      {row.agent?.role && row.agent.role !== '—' && (
        <div className="muted small">{row.agent.role}</div>
      )}
      {row.agent?.repo && row.agent.repo !== '—' && (
        <div className="muted small">{row.agent.repo}</div>
      )}
      {row.skills.length > 0 && (
        <div className="muted small">скиллы: {row.skills.join(', ')}</div>
      )}
      {row.missing.length > 0 && (
        <div className="muted small">в манифесте нет: {row.missing.join(', ')}</div>
      )}
    </>
  )

  /** Ячейка «Заявки»: сколько заявок и в какие разделы они ушли. */
  const requestCell = (row: MergedRow) => {
    const sections = row.agent ? sectionsOf(byAgent, row.agent.slug) : []
    return (
      <>
        {row.requests}
        {sections.length > 0 && (
          <div className="muted small">раздел: {sections.join(', ')}</div>
        )}
      </>
    )
  }

  const createAgent = async () => {
    const problems = validateDraft(draft)
    if (problems.length) {
      setNote(problems.join('; '))
      return
    }
    if (!window.confirm(createConfirmText(draft))) return
    const ok = await runHere(
      () => api.agentsCreate(draft),
      `агент «${draft.title}» заведён`
    )
    if (ok) {
      setCreatingAgent(false)
      setDraft({ slug: '', title: '', role: '' })
    }
  }

  const createArea = async () => {
    const res = await api.createArea(areaForm)
    if (res.error) {
      setNote(res.error)
      return
    }
    setNote(`область ${res.slug} создана`)
    setCreatingArea(false)
    setAreaForm({ slug: '', title: '', folder: '', karman: '' })
  }

  /**
   * Завести область агента, у которого её нет (или которая выпала из
   * списка). Рабочая папка — репозиторий агента.
   */
  const makeArea = async (row: MergedRow) => {
    const slug = row.agent?.slug ?? ''
    const repoName = (row.repo || '').split('/').pop() ?? ''
    const workspace = registry?.workspace ?? ''
    const res = await api.createArea({
      slug: row.area,
      title: row.title || row.area,
      folder: repoName && workspace ? `${workspace}/${repoName}` : '',
      karman: slug || row.area
    })
    if (res.error) {
      setNote(res.error)
      return
    }
    setNote(`область ${res.slug} создана`)
    await run(() => api.snapshot())
  }

  const restore = async (row: AgentRow) => {
    if (!window.confirm(applyConfirmText(row))) return
    await runHere(
      () => api.agentsApply(row.slug),
      `агент «${row.title}» восстановлен`
    )
  }

  const restoreRegistry = async () => {
    if (!window.confirm(
      'Вернуть локальную копию реестра к состоянию GitHub?\n\n' +
      'Правки локальной копии будут потеряны — источник правды это GitHub.\n\n' +
      'Продолжить?'
    )) return
    await runHere(
      () => api.agentsRegistryRestore(),
      'локальная копия реестра приведена к GitHub'
    )
  }

  /** Пересобрать список областей по машине (после потери локального конфига). */
  const rebuildAreas = async () => {
    const res = await api.areasRebuild()
    if (res.error) {
      setNote(res.error)
      return
    }
    const added = res.added ?? []
    setNote(
      `список областей пересобран: всего ${res.areas ?? 0}` +
      (added.length ? `, добавлено: ${added.join(', ')}` : '')
    )
    await run(() => api.snapshot())
  }

  return (
    <section>
      <div className="row head">
        <h2>Агенты</h2>
        <div className="row">
          <button className="ghost" onClick={() => void load()} disabled={busy}>
            Обновить
          </button>
          <button
            className="ghost"
            onClick={() => void rebuildAreas()}
            disabled={busy}
            title="Пересобрать список областей по машине: конфиг, его копия, каталоги данных и реестр агентов"
          >
            Починить список
          </button>
          <button
            className="ghost"
            onClick={() => setCreatingArea((value) => !value)}
            disabled={busy}
          >
            Создать область
          </button>
          <button
            onClick={() => setCreatingAgent((value) => !value)}
            disabled={busy}
          >
            Создать агента
          </button>
        </div>
      </div>

      {note && <div className="banner">{note}</div>}
      {warning && (
        <div className="banner warn">
          {warning}.{' '}
          <button className="ghost" onClick={() => void restoreRegistry()} disabled={busy}>
            Вернуть как на GitHub
          </button>
        </div>
      )}

      {creatingAgent && (
        <div className="card create">
          <h3>Новый агент</h3>
          <p className="muted small">
            Создание заведёт репозиторий на GitHub, область, конституцию и
            скиллы и запишет агента в реестр — поэтому спрашиваем подтверждение.
          </p>
          <div className="grid4">
            <label>
              slug (латиницей)
              <input
                value={draft.slug}
                onChange={(event) =>
                  setDraft({ ...draft, slug: event.target.value.toLowerCase() })
                }
                placeholder="например: ideas"
              />
            </label>
            <label>
              название
              <input
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                placeholder="например: Идеи"
              />
            </label>
            <label>
              роль
              <input
                value={draft.role}
                onChange={(event) => setDraft({ ...draft, role: event.target.value })}
                placeholder="чем он занимается"
              />
            </label>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" onClick={() => void createAgent()} disabled={busy}>
              Создать
            </button>
            <button className="ghost" onClick={() => setCreatingAgent(false)} disabled={busy}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {creatingArea && (
        <div className="card create">
          <h3>Новая область</h3>
          <div className="grid4">
            <label>
              slug (латиницей)
              <input
                value={areaForm.slug}
                onChange={(event) => setAreaForm({ ...areaForm, slug: event.target.value })}
                placeholder="например sarafan2"
              />
            </label>
            <label>
              Название
              <input
                value={areaForm.title}
                onChange={(event) => setAreaForm({ ...areaForm, title: event.target.value })}
                placeholder="Сарафан 2"
              />
            </label>
            <label>
              Рабочая папка
              <input
                value={areaForm.folder}
                onChange={(event) => setAreaForm({ ...areaForm, folder: event.target.value })}
                placeholder="D:\valstan\REPO\sarafan"
              />
            </label>
            <label>
              Раздел КАРМАНА
              <input
                value={areaForm.karman}
                onChange={(event) => setAreaForm({ ...areaForm, karman: event.target.value })}
                placeholder="SARAFAN"
              />
            </label>
          </div>
          <p className="muted small">
            Раздел КАРМАНА — имя проекта области в библиотеке промтов (например
            <code> SARAFAN</code>). По нему промт из «Хранилища промтов» уходит в
            эту область; если раздел не совпадает, задание не отправляется — оно
            ушло бы не туда.
          </p>
          <div className="row">
            <button className="primary" onClick={() => void createArea()} disabled={busy}>
              Создать
            </button>
            <button className="ghost" onClick={() => setCreatingArea(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <h3>Канал КАРМАНА</h3>
        <p className="muted small">
          Заявки уходят в раздел своего проекта; если раздела нет — он
          заводится сам.
        </p>
        <p className="muted small">
          Заявки агентов ушли в разделы:{' '}
          <span className="title">{usedSections.length ? usedSections.join(', ') : 'пока никуда'}</span>
        </p>
        <p>
          <span className={`chip ${channelInfo.tone === 'ok' ? 'ok' : channelInfo.tone === 'warn' ? 'warn' : 'bad'}`}>
            {channelInfo.label}
          </span>{' '}
          <span className="muted small">{channelInfo.detail}</span>
        </p>
        {channelInfo.sections.length > 0 && (
          <div>
            <p className="muted small">Разделы канала:</p>
            <div className="chips">
              {channelInfo.sections.map((name) => (
                <span key={name} className="chip muted">
                  {name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h3>Реестр агентов</h3>
        <p className="muted small">{registrySummary(registry)}</p>
      </div>

      <table className="grid">
        <thead>
          <tr>
            <th>Агент</th>
            {AREA_COLUMNS.map((column) => (
              <th key={column.key}>
                <button
                  className={`sort${sort.key === column.key ? ' active' : ''}`}
                  onClick={() => setSort((current) => nextSort(current, column.key))}
                  // Список столбцов в подсказке — из того же `AREA_COLUMNS`,
                  // что и шапки ⇒ подсказка всегда совпадает с экраном
                  // (промт КАРМАНА #182, п. 6).
                  title={`Сортировать по этому столбцу. Столбцы: ${sortColumnsHint()}`}
                >
                  {column.label}
                  {sort.key === column.key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                </button>
              </th>
            ))}
            <th>Заявки</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={AREA_COLUMNS.length + 3} className="muted">
                Ни агентов, ни областей. Нажмите «Создать агента» или
                «Починить список» — список соберётся по машине.
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.area}>
                <td className="col-agent">{agentCell(row)}</td>
                {AREA_COLUMNS.map((column) => (
                  <td key={column.key} className={`col-${column.key}`}>
                    {cell(column.key, row)}
                  </td>
                ))}
                <td className="muted">{requestCell(row)}</td>
                <td className="actions">
                  {row.live ? (
                    <>
                      <button className="ghost" disabled={busy} onClick={() => void run(() => api.start(row.area))}>
                        Старт
                      </button>
                      <button className="ghost" disabled={busy} onClick={() => void run(() => api.stop(row.area))}>
                        Стоп
                      </button>
                      <button
                        className="ghost"
                        disabled={busy}
                        onClick={() => void run(() => api.restart(row.area))}
                      >
                        Перезапуск
                      </button>
                      <button
                        className="ghost"
                        disabled={busy}
                        onClick={() => void run(() => api.relogin(row.area))}
                        // Запасной **ручной** путь (промт КАРМАНА #182, п. 2):
                        // автоматика сама берёт свежий аккаунт, а эта кнопка — на
                        // случай сбоя автоматики.
                        title="Запасной ручной путь: взять следующий аккаунт у КАРМАНА, если автоматика не сработала"
                      >
                        Новый вход
                      </button>
                      {row.live.folder && (
                        <button
                          className="ghost"
                          onClick={() => void api.openFolder(row.live!.folder)}
                        >
                          → Папка
                        </button>
                      )}
                    </>
                  ) : (
                    <button
                      className="ghost"
                      disabled={busy}
                      onClick={() => void makeArea(row)}
                      title="Завести область этого агента: каталог данных, авторизация и запись в список"
                    >
                      Завести область
                    </button>
                  )}
                  {row.agent && (
                    <button className="ghost" disabled={busy} onClick={() => void restore(row.agent!)}>
                      Восстановить
                    </button>
                  )}
                </td>
              </tr>
            ))
          )}
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
 * Ячейка «Сессии» (на экране так с 27.11, промт КАРМАНА #182; раньше колонка
 * называлась «Водитель»): **две небольшие кнопки** — «Миниген» и «Оператор»,
 * и короткая строка «сейчас: …», которая колонку не раздувает (полный текст —
 * во всплывающей подсказке).
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
