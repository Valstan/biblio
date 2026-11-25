import { Fragment, useCallback, useEffect, useState } from 'react'
import type { PromptTarget, PromptsSnapshot } from '@shared/types'
import { api } from '../api'
import { TZ } from '../time'
import {
  archiveConfirmText,
  autoEligible,
  dispatchConfirmText,
  filterRows,
  markConfirmText,
  promptRows,
  storeView,
  targetsFor,
  validateDraft,
  type PromptRow
} from './prompts-view'

/**
 * Вкладка «Хранилище промтов» — место, где лежат промты и планы по проектам
 * (заказ владельца от 24.11, раздел КАРМАНА «MiniGen»).
 *
 * Что показывает:
 * - **место хранения**: в базе «МиниГена» или на внешнем ресурсе (Карман) —
 *   честно, включая состояние «не спросили» и «недоступно»;
 * - **список промтов**: вид (промт/план), раздел, дата, метка «авто» и
 *   превью текста, с поиском и выбором раздела;
 * - действия: **«В область»** (ручная отправка промта в рабочую область
 *   своего проекта), **«Авто»** (пометить/снять пометку автораздачи) и
 *   **«В архив»** (сделано — перестаёт выдаваться);
 * - **форма добавления** промта в локальное хранилище.
 *
 * ⚠️ Действия, которые тратят ресурсы или убирают промт из оборота, идут
 * только с подтверждением — окно спрашивает и называет последствия.
 *
 * Данные спрашиваются у моста по таймеру: окно открывается раньше моста, и
 * разовый запрос в этот момент падает (мост ещё не поднялся).
 */

/** Значок «нет данных» — тот же, что в остальном окне. */
const DASH = '—'

/** Дата моста приходит строкой — показываем её по Москве, если она разбирается. */
function stamp(value: string): string {
  if (!value || value === DASH) return DASH
  const ms = Date.parse(value)
  if (Number.isNaN(ms)) return value
  return new Date(ms).toLocaleString('ru-RU', { timeZone: TZ })
}

export default function PromptsTab() {
  const [snapshot, setSnapshot] = useState<PromptsSnapshot | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [section, setSection] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [targetSlug, setTargetSlug] = useState('')
  const [draft, setDraft] = useState({ project: '', title: '', body: '' })

  const load = useCallback(async () => {
    try {
      setSnapshot(await api.promptsList())
    } catch (err) {
      setNote(`хранилище не спросили: ${String((err as Error).message || err)}`)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [load])

  /** Раздел промта уходит в мост только если он есть — «—» это не раздел. */
  const projectOf = (row: PromptRow): string | undefined =>
    row.project === DASH ? undefined : row.project

  const store = storeView(snapshot?.store ?? null)
  const sections = snapshot?.store?.sections ?? []
  const targets = snapshot?.targets ?? []
  // Пока хранилище недоступно, список промтов пуст — не выдумываем записи.
  const rows = store.tone === 'ok' ? promptRows(snapshot) : []
  const bySection = rows.filter(
    (row) => !section || row.project.toLowerCase() === section.toLowerCase()
  )
  const visible = filterRows(bySection, query)

  const dispatch = async (row: PromptRow, target: PromptTarget) => {
    if (!window.confirm(dispatchConfirmText(row, target))) return
    setBusy(true)
    try {
      const res = await api.promptsDispatch({
        id: row.id,
        project: projectOf(row),
        area: target.slug
      })
      if (!res.ok) {
        setNote(res.error || res.why || 'мост не отправил промт')
        return
      }
      setNote(`промт «${row.title}» отправлен в область ${target.slug}`)
      setOpenId(null)
      await load()
    } catch (err) {
      setNote(String((err as Error).message || err))
    } finally {
      setBusy(false)
    }
  }

  const archive = async (row: PromptRow) => {
    if (!window.confirm(archiveConfirmText(row))) return
    setBusy(true)
    try {
      const res = await api.promptsArchive({ id: row.id, project: projectOf(row) })
      if (!res.ok) {
        setNote(res.error || 'мост не архивировал промт')
        return
      }
      setNote(`промт «${row.title}» в архиве`)
      await load()
    } catch (err) {
      setNote(String((err as Error).message || err))
    } finally {
      setBusy(false)
    }
  }

  const mark = async (row: PromptRow) => {
    const next = !autoEligible(row)
    if (!window.confirm(markConfirmText(row, next))) return
    setBusy(true)
    try {
      const res = await api.promptsMark({
        id: row.id,
        project: projectOf(row),
        auto: next
      })
      if (!res.ok) {
        setNote(res.error || 'мост не пометил промт')
        return
      }
      setNote(next ? `промт «${row.title}» помечен для автораздачи` : `прометка снята`)
      await load()
    } catch (err) {
      setNote(String((err as Error).message || err))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    const problems = validateDraft(draft)
    if (problems.length) {
      setNote(problems.join('; '))
      return
    }
    setBusy(true)
    try {
      const res = await api.promptsSave({
        project: draft.project.trim(),
        title: draft.title.trim(),
        body: draft.body
      })
      if (!res.ok) {
        setNote(res.error || 'мост не сохранил промт')
        return
      }
      setNote('промт сохранён')
      setDraft({ project: draft.project, title: '', body: '' })
      await load()
    } catch (err) {
      setNote(String((err as Error).message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <div className="row head">
        <h2>Хранилище промтов</h2>
        <button className="ghost" onClick={() => void load()} disabled={busy}>
          Обновить
        </button>
      </div>

      {note && <div className="banner">{note}</div>}

      {store.tone !== 'ok' && (
        <div className={`banner ${store.tone === 'warn' ? 'warn' : 'bad'}`}>
          {store.label}: {store.detail}. Пока хранилище недоступно, список промтов пуст.
        </div>
      )}

      <div className="card">
        <h3>Место хранения</h3>
        <p>
          <span className={`chip ${store.tone === 'ok' ? 'ok' : store.tone === 'warn' ? 'warn' : 'bad'}`}>
            {store.label}
          </span>{' '}
          <span className="muted small">{store.detail}</span>
        </p>
        <p className="muted small">
          Переключается в «Настройках». Автораздача сейчас{' '}
          {snapshot?.auto_dispatch ? 'включена' : 'выключена'}: промт уходит в
          область своего проекта сам, только если владелец пометил его «авто».
        </p>
      </div>

      <div className="card">
        <h3>Промты и планы</h3>
        <div className="grid2">
          <label>
            Раздел
            <select value={section} onChange={(event) => setSection(event.target.value)}>
              <option value="">все разделы</option>
              {sections.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Поиск
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="название, раздел или текст"
            />
          </label>
        </div>

        {visible.length === 0 ? (
          <p className="muted small">
            {rows.length === 0 ? 'Промтов в хранилище нет.' : 'Ничего не нашлось.'}
          </p>
        ) : (
          <table className="grid compact">
            <thead>
              <tr>
                <th>Название</th>
                <th>Вид</th>
                <th>Раздел</th>
                <th>Дата</th>
                <th>Авто</th>
                <th>Текст</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const options = targetsFor(row, targets)
                const chosen = options.find((item) => item.slug === targetSlug) ?? options[0]
                return (
                  <Fragment key={row.id}>
                    <tr>
                      <td>
                        <span className="title">{row.title}</span>
                        {row.missing.length > 0 && (
                          <div className="muted small">нет: {row.missing.join(', ')}</div>
                        )}
                        {row.archived && <div className="muted small">в архиве</div>}
                      </td>
                      <td className="muted">{row.kind}</td>
                      <td className="muted">{row.project}</td>
                      <td className="muted small">{stamp(row.created)}</td>
                      <td className="muted small">{autoEligible(row) ? 'авто' : DASH}</td>
                      <td className="muted small">{row.preview}</td>
                      <td className="actions">
                        <button
                          className="ghost"
                          onClick={() => {
                            setOpenId(openId === row.id ? null : row.id)
                            setTargetSlug(options[0]?.slug ?? '')
                          }}
                          disabled={busy}
                        >
                          В область
                        </button>
                        <button className="ghost" onClick={() => void mark(row)} disabled={busy}>
                          {autoEligible(row) ? 'Снять авто' : 'Авто'}
                        </button>
                        <button className="ghost" onClick={() => void archive(row)} disabled={busy}>
                          В архив
                        </button>
                      </td>
                    </tr>
                    {openId === row.id && (
                      <tr>
                        <td colSpan={7}>
                          {options.length === 0 ? (
                            <span className="muted small">
                              раздел промта ≠ проект области: у раздела «{row.project}»
                              нет своей области
                            </span>
                          ) : (
                            <div className="row">
                              <select
                                value={chosen?.slug ?? ''}
                                onChange={(event) => setTargetSlug(event.target.value)}
                              >
                                {options.map((item) => (
                                  <option key={item.slug} value={item.slug}>
                                    {item.slug} — {item.title}
                                    {item.busy ? ' (живая сессия)' : ''}
                                  </option>
                                ))}
                              </select>
                              <button
                                className="primary"
                                onClick={() => void dispatch(row, chosen)}
                                disabled={busy || !chosen}
                              >
                                Отправить
                              </button>
                              <button className="ghost" onClick={() => setOpenId(null)} disabled={busy}>
                                Отмена
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Новый промт</h3>
        <p className="muted small">
          Сохраняется в локальное хранилище «МиниГена». Раздел — коротким именем
          латиницей: по нему промт уходит в область своего проекта.
        </p>
        <div className="grid2">
          <label>
            раздел
            <input
              value={draft.project}
              onChange={(event) => setDraft({ ...draft, project: event.target.value })}
              placeholder="например: biblio"
            />
          </label>
          <label>
            заголовок
            <input
              value={draft.title}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              placeholder="например: разбор заявки"
            />
          </label>
        </div>
        <label style={{ marginTop: 8 }}>
          текст промта
          <textarea
            value={draft.body}
            onChange={(event) => setDraft({ ...draft, body: event.target.value })}
            placeholder="что именно сделать"
            rows={6}
            style={{
              font: 'inherit',
              background: 'var(--bg)',
              border: '1px solid var(--line)',
              borderRadius: 6,
              color: 'var(--text)',
              padding: '6px 8px',
              width: '100%',
              resize: 'vertical'
            }}
          />
        </label>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="primary" onClick={() => void save()} disabled={busy}>
            Сохранить
          </button>
        </div>
      </div>
    </section>
  )
}
