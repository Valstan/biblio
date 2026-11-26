import { useCallback, useEffect, useState } from 'react'
import type { AgentsSnapshot, KarmanChannel } from '@shared/types'
import { api } from '../api'
import {
  agentRows,
  applyConfirmText,
  channelView,
  createConfirmText,
  registrySummary,
  registryWarning,
  validateDraft,
  type AgentDraft
} from './agents-view'
import { requestSections, sectionsInUse, sectionsOf } from './agents-requests'

/**
 * Вкладка «Агенты» — реестр агентов «МиниГена» глазами (заявка владельца
 * от 24.11, раздел КАРМАНА «MiniGen», промт #122).
 *
 * Что показывает:
 * - **канал КАРМАНА**: жив или не настроен, откуда взят секрет и сколько
 *   разделов (заявки уходят в раздел своего проекта);
 * - **реестр агентов**: slug, название, роль, репозиторий, область и
 *   число заявок — и разделы, куда эти заявки ушли;
 * - действия: «Восстановить» агента (по реестру с GitHub) и «Создать
 *   агента» — с подтверждением, потому что создание меняет внешний мир.
 *
 * Окно **ничего не дублирует**: данные спрашиваются у моста командами
 * `agents.list` и `karman.status`, как и на остальных вкладках.
 */
export default function AgentsTab() {
  const [snapshot, setSnapshot] = useState<AgentsSnapshot | null>(null)
  const [channel, setChannel] = useState<KarmanChannel | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<AgentDraft>({ slug: '', title: '', role: '' })

  const load = useCallback(async () => {
    try {
      const [agents, karman] = await Promise.all([
        api.agentsList(),
        api.karmanStatus().catch(() => null)
      ])
      setSnapshot(agents)
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

  const run = async (action: () => Promise<unknown>, ok: string): Promise<boolean> => {
    setBusy(true)
    try {
      await action()
      setNote(ok)
      await load()
      return true
    } catch (err) {
      setNote(String((err as Error).message || err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const rows = agentRows(snapshot)
  const channelInfo = channelView(channel)
  const warning = registryWarning(snapshot)
  // Куда уходят заявки: по каждому агенту и по всем вместе (для канала).
  const byAgent = requestSections(snapshot)
  const usedSections = sectionsInUse(byAgent)

  // Ячейка «Заявки»: сколько заявок и в какие разделы они ушли.
  const requestCell = (slug: string, count: number) => {
    const sections = sectionsOf(byAgent, slug)
    return (
      <>
        {count}
        {sections.length > 0 && (
          <div className="muted small">раздел: {sections.join(', ')}</div>
        )}
      </>
    )
  }

  const restore = async (row: (typeof rows)[number]) => {
    if (!window.confirm(applyConfirmText(row))) return
    await run(
      () => api.agentsApply(row.slug),
      `агент «${row.title}» восстановлен`
    )
  }

  const create = async () => {
    const problems = validateDraft(draft)
    if (problems.length) {
      setNote(problems.join('; '))
      return
    }
    if (!window.confirm(createConfirmText(draft))) return
    const ok = await run(
      () => api.agentsCreate(draft),
      `агент «${draft.title}» заведён`
    )
    if (ok) {
      setCreating(false)
      setDraft({ slug: '', title: '', role: '' })
    }
  }

  const restoreRegistry = async () => {
    if (!window.confirm(
      'Вернуть локальную копию реестра к состоянию GitHub?\n\n' +
      'Правки локальной копии будут потеряны — источник правды это GitHub.\n\n' +
      'Продолжить?'
    )) return
    await run(
      () => api.agentsRegistryRestore(),
      'локальная копия реестра приведена к GitHub'
    )
  }

  return (
    <section>
      <div className="row head">
        <h2>Агенты</h2>
        <div className="row">
          <button className="ghost" onClick={() => void load()} disabled={busy}>
            Обновить
          </button>
          <button onClick={() => setCreating((value) => !value)} disabled={busy}>
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

      {creating && (
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
            <button className="primary" onClick={() => void create()} disabled={busy}>
              Создать
            </button>
            <button className="ghost" onClick={() => setCreating(false)} disabled={busy}>
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
        <p className="muted small">{registrySummary(snapshot)}</p>
        {rows.length === 0 ? (
          <p className="muted small">Агентов в реестре нет.</p>
        ) : (
          <table className="grid compact">
            <thead>
              <tr>
                <th>Агент</th>
                <th>Роль</th>
                <th>Репозиторий</th>
                <th>Область</th>
                <th>Заявки</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.slug}>
                  <td>
                    <span className="title">{row.title}</span>
                    <div className="muted small">{row.slug}</div>
                    {row.missing.length > 0 && (
                      <div className="muted small">
                        в манифесте нет: {row.missing.join(', ')}
                      </div>
                    )}
                    {row.skills.length > 0 && (
                      <div className="muted small">скиллы: {row.skills.join(', ')}</div>
                    )}
                  </td>
                  <td className="muted">{row.role}</td>
                  <td className="muted">{row.repo}</td>
                  <td className="muted">{row.area}</td>
                  <td className="muted">{requestCell(row.slug, row.requests)}</td>
                  <td className="actions">
                    <button className="ghost" onClick={() => void restore(row)} disabled={busy}>
                      Восстановить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
