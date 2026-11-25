import { useCallback, useEffect, useState } from 'react'
import type { CloneStatus, GeneMap, GeneRecord, GeneSuggestions } from '@shared/types'
import { api } from '../api'
import { formatDateTime } from '../time'
import { adoptConfirmText, exchangeView, suggestionRows, type SuggestionRow } from './copy-view'
import { describeEdge, layoutMap, selfSummary, NODE_SIZE } from './gene-map'

/**
 * Вкладка «Гены» — **карта родословной**: паутина копий «МиниГена» и генов
 * между ними (заказ владельца 24.11) + обмен наработками (25.11, промт #131).
 *
 * Что показывает:
 * - граф: колонка — поколение; корень — версия программы, от которой берут
 *   форк; копии — узлы, гены (наработки) — между ними;
 * - «я в этой паутине»: кто эта копия, сколько генов создала, сколько взяла,
 *   у кого сколько позаимствовали;
 * - таблицы копий и генов и список связей текстом;
 * - **обмен наработками**: доступен ли обмен (и почему нет), ручной обмен
 *   папками (выгрузить/внести) — работает и без сети;
 * - **наработки других копий**: чужие гены, которые стоит посмотреть;
 *   подходящие — кнопка «Внедрить» (через ветку и PR, решение за владельцем).
 *
 * ⚠️ Честность — правило вкладки: в локальном режиме обмен с другими
 * копиями **недоступен**, и это написано прямо, а не спрятано за пустым
 * списком. Ошибки моста показываются текстом, а не пустотой.
 *
 * Данные спрашиваются у моста по таймеру: окно открывается раньше моста, и
 * разовый запрос в этот момент падает.
 */

/** Значок «нет данных» — тот же, что в остальном окне. */
const DASH = '—'

export default function GenesTab() {
  const [map, setMap] = useState<GeneMap | null>(null)
  const [note, setNote] = useState('')
  const [mapError, setMapError] = useState('')
  const [busy, setBusy] = useState(false)
  const [showWorks, setShowWorks] = useState(false)
  const [cloneStatus, setCloneStatus] = useState<CloneStatus | null>(null)
  const [suggestions, setSuggestions] = useState<GeneSuggestions | null>(null)
  const [myGenes, setMyGenes] = useState<GeneRecord[]>([])
  const [copyError, setCopyError] = useState('')
  const [worksError, setWorksError] = useState('')
  const [myWorksError, setMyWorksError] = useState('')
  const [exportPath, setExportPath] = useState('')
  const [importPath, setImportPath] = useState('')

  const load = useCallback(async () => {
    try {
      setMap(await api.mapShow())
      setMapError('')
    } catch (err) {
      setMapError(`карту не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  /** Состояние копии и чужие подсказки — тоже по таймеру (мост встаёт позже окна). */
  const loadCopy = useCallback(async () => {
    try {
      setCloneStatus(await api.cloneStatus())
      setCopyError('')
    } catch (err) {
      setCopyError(`состояние копии не принесли: ${String((err as Error).message || err)}`)
    }
    try {
      setSuggestions(await api.genesSuggest())
      setWorksError('')
    } catch (err) {
      setWorksError(`чужие наработки не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  const loadWorks = useCallback(async () => {
    try {
      const data = await api.genesList()
      setMyGenes(data?.genes ?? [])
      setMyWorksError('')
    } catch (err) {
      setMyWorksError(`свои наработки не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    void loadCopy()
    const timer = window.setInterval(() => void loadCopy(), 5000)
    return () => window.clearInterval(timer)
  }, [loadCopy])

  /** Обновить пул наработок (клон и пул генов) — действие владельца. */
  const syncPool = async () => {
    setBusy(true)
    try {
      const res = await api.cloneSync()
      const failed = (res.steps ?? []).filter((step) => !step.ok)
      if (!res.ok) {
        setNote(res.why || 'мост не обновил пул')
      } else if (failed.length) {
        setNote(
          `пул обновлён с ошибками: ${failed
            .map((step) => `${step.what} — ${step.why || DASH}`)
            .join('; ')}`
        )
      } else {
        setNote('пул обновлён')
      }
    } catch (err) {
      setNote(`пул не обновили: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Ручной обмен без сети: выгрузить папку наработок и отнести соседу. */
  const exportWorks = async () => {
    setBusy(true)
    try {
      const res = await api.genesExport()
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не выгрузил папку наработок')
        return
      }
      setExportPath(res.path || '')
      setNote(
        res.path
          ? `папка наработок выгружена: ${res.path}`
          : 'папка наработок выгружена (путь не пришёл)'
      )
    } catch (err) {
      setNote(`выгрузка не вышла: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Внести папку наработок, выгруженную другой копией. */
  const importWorks = async () => {
    const path = importPath.trim()
    if (!path) {
      setNote('укажите папку с наработками')
      return
    }
    setBusy(true)
    try {
      const res = await api.genesImport(path)
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не внёс папку наработок')
        return
      }
      const added = res.added?.length ?? res.files
      setNote(
        `внесено наработок: ${added ?? DASH}${
          res.skipped?.length ? `, пропущено: ${res.skipped.length}` : ''
        }`
      )
      await loadCopy()
      await loadWorks()
    } catch (err) {
      setNote(`внесение не вышло: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Внедрение чужой наработки: сначала подтверждение (решение за владельцем),
   * потом мост готовит ветку и PR.
   */
  const adopt = async (row: SuggestionRow) => {
    if (!window.confirm(adoptConfirmText(row))) return
    setBusy(true)
    try {
      const res = await api.genesAdopt(row.gene)
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не внедрил наработку')
        return
      }
      setNote(
        `наработка ${row.gene} отправлена на внедрение` +
          (res.branch ? ` (ветка ${res.branch})` : '') +
          (res.pr ? `: PR ${res.pr}` : '') +
          (res.local_only ? ' — локально, PR не открывается' : '')
      )
      await loadCopy()
    } catch (err) {
      setNote(`внедрение не вышло: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  const exchange = exchangeView(cloneStatus)
  const rows = suggestionRows(suggestions)
  const fits = rows.filter((row) => row.fits)
  const others = rows.filter((row) => !row.fits)
  // Раскладка карты — считаем один раз на отрисовку (как было раньше).
  const layout = map ? layoutMap(map) : null

  return (
    <section>
      <div className="row head">
        <h2>Гены</h2>
        <div className="row">
          <button className="ghost" onClick={() => void syncPool()} disabled={busy}>
            Обновить пул
          </button>
          <button
            className="ghost"
            onClick={() => {
              const next = !showWorks
              setShowWorks(next)
              if (next) void loadWorks()
            }}
            disabled={busy}
          >
            {showWorks ? 'Скрыть наработки' : 'Наработки'}
          </button>
          <button className="ghost" onClick={() => void load()} disabled={busy}>
            Обновить карту
          </button>
        </div>
      </div>

      {note && <div className="banner">{note}</div>}
      {mapError && <div className="banner bad">{mapError}</div>}
      {copyError && <div className="banner bad">{copyError}</div>}
      {exchange.tone !== 'ok' && (
        <div className={`banner ${exchange.tone === 'warn' ? 'warn' : 'bad'}`}>
          {exchange.label}: {exchange.detail}
        </div>
      )}

      <div className="card">
        <h3>Обмен наработками</h3>
        <p>
          <span className={`chip ${exchange.tone}`}>{exchange.label}</span>{' '}
          <span className="muted small">{exchange.detail}</span>
        </p>
        <div className="row">
          <button className="ghost" onClick={() => void exportWorks()} disabled={busy}>
            Выгрузить папку
          </button>
          {exportPath && <span className="muted small">{exportPath}</span>}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <input
            value={importPath}
            onChange={(event) => setImportPath(event.target.value)}
            placeholder="путь к папке наработок другой копии"
          />
          <button className="ghost" onClick={() => void importWorks()} disabled={busy}>
            Внести папку
          </button>
        </div>
        <p className="muted small">
          Выгрузить и внести — ручной обмен без сети: отнёс папку соседней
          копии и принёс её обратно. Он работает и там, где обмен через пул
          недоступен.
        </p>
      </div>

      {showWorks && (
        <>
          <div className="card">
            <h3>Наработки</h3>
            {myWorksError && <p className="muted small">{myWorksError}</p>}
            {myGenes.length === 0 ? (
              <p className="muted small">Своих наработок в пуле нет.</p>
            ) : (
              <table className="grid compact">
                <thead>
                  <tr>
                    <th>Ген</th>
                    <th>Что сделано</th>
                    <th>Автор</th>
                    <th>Дата</th>
                    <th>Файлов</th>
                  </tr>
                </thead>
                <tbody>
                  {myGenes.map((gene) => (
                    <tr key={gene.id}>
                      <td className="muted">{gene.id || DASH}</td>
                      <td>{gene.title || DASH}</td>
                      <td className="muted">{gene.author || DASH}</td>
                      <td className="muted small">{formatDateTime(gene.created_at) || DASH}</td>
                      <td className="muted">{gene.files?.length ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            <h3>Наработки других копий</h3>
            {worksError && <p className="muted small">{worksError}</p>}
            {suggestions && !suggestions.available && (
              <p className="muted small">
                {suggestions.why || 'чужие наработки сейчас недоступны'}
              </p>
            )}
            {rows.length === 0 ? (
              <p className="muted small">
                {suggestions?.available
                  ? 'Чужих наработок мост не принёс.'
                  : 'Чужие наработки сейчас недоступны.'}
              </p>
            ) : (
              <>
                <table className="grid compact">
                  <thead>
                    <tr>
                      <th>Ген</th>
                      <th>Что сделано</th>
                      <th>Автор</th>
                      <th>Почему подходит</th>
                      <th>Дата</th>
                      <th>Действия</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fits.map((row) => (
                      <tr key={row.gene}>
                        <td className="muted">{row.gene}</td>
                        <td>{row.title}</td>
                        <td className="muted">{row.author}</td>
                        <td className="muted small">{row.why}</td>
                        <td className="muted small">{row.created}</td>
                        <td className="actions">
                          <button
                            className="ghost"
                            onClick={() => void adopt(row)}
                            disabled={busy}
                          >
                            Внедрить
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {others.length > 0 && (
                  <>
                    <p className="muted small" style={{ marginTop: 8 }}>
                      Прочие наработки: мост говорит, что к вашей версии они не
                      подходят — внедрять их нельзя, поэтому кнопки нет.
                    </p>
                    <table className="grid compact">
                      <thead>
                        <tr>
                          <th>Ген</th>
                          <th>Что сделано</th>
                          <th>Автор</th>
                          <th>Дата</th>
                        </tr>
                      </thead>
                      <tbody>
                        {others.map((row) => (
                          <tr key={row.gene}>
                            <td className="muted">{row.gene}</td>
                            <td>{row.title}</td>
                            <td className="muted">{row.author}</td>
                            <td className="muted small">{row.created}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </>
            )}
          </div>
        </>
      )}

      {!map ? (
        <div className="card">
          <h3>Паутина разработки</h3>
          <p className="muted small">
            {mapError ? 'Карта родословной пока не пришла.' : 'Собираю карту…'}
          </p>
        </div>
      ) : (
        <>
          <div className="card">
            <h3>Паутина разработки</h3>
            <p className="muted small">
              {map.stats.copies} копий · {map.stats.genes} генов · {map.stats.links}{' '}
              связей · {map.stats.events} событий в пуле. Колонка — поколение: слева
              корень (версия программы), правее копии и гены между ними.
            </p>
            {!layout ? null : layout.nodes.length === 0 ? (
              <p className="muted small">Карта пуста — копия ещё не заведена.</p>
            ) : (
              <div style={{ overflow: 'auto' }}>
                <svg
                  width={layout.width}
                  height={layout.height}
                  role="img"
                  aria-label="Карта родословной копий и генов"
                >
                  {layout.links.map((link, index) => {
                    const from = layout.nodes.find((node) => node.id === link.from)
                    const to = layout.nodes.find((node) => node.id === link.to)
                    if (!from || !to) return null
                    return (
                      <g key={`${link.from}-${link.to}-${index}`}>
                        <line
                          x1={from.x + NODE_SIZE.width / 2}
                          y1={from.y + NODE_SIZE.height / 2}
                          x2={to.x + NODE_SIZE.width / 2}
                          y2={to.y + NODE_SIZE.height / 2}
                          stroke={link.kind === 'took' ? '#7aa2f7' : '#4b5563'}
                          strokeWidth={link.kind === 'born' ? 2 : 1}
                          strokeDasharray={link.kind === 'replaced' ? '4 3' : undefined}
                        >
                          <title>{link.label}</title>
                        </line>
                      </g>
                    )
                  })}
                  {layout.nodes.map((node) => (
                    <g key={node.id}>
                      <rect
                        x={node.x}
                        y={node.y}
                        width={NODE_SIZE.width}
                        height={NODE_SIZE.height}
                        rx={node.kind === 'copy' ? 12 : 4}
                        fill={
                          node.kind === 'copy'
                            ? node.isSelf
                              ? '#2d4a2f'
                              : '#23303f'
                            : node.state === 'retracted'
                              ? '#4a2626'
                              : '#33294a'
                        }
                        stroke={node.isSelf ? '#8fce8f' : '#5a6472'}
                        strokeWidth={node.isSelf ? 2 : 1}
                      />
                      <text x={node.x + 10} y={node.y + 20} fill="#e6e6e6" fontSize="12">
                        {node.label.length > 26 ? `${node.label.slice(0, 25)}…` : node.label}
                      </text>
                      <text x={node.x + 10} y={node.y + 38} fill="#9aa3b0" fontSize="11">
                        {node.kind === 'copy'
                          ? `поколение ${node.generation}${node.isSelf ? ' · это я' : ''}`
                          : node.kind === 'gene'
                            ? node.state
                            : 'основа'}
                      </text>
                      <title>{node.title}</title>
                    </g>
                  ))}
                </svg>
              </div>
            )}
          </div>

          <div className="card">
            <h3>Я в этой паутине</h3>
            <table className="grid compact">
              <tbody>
                {selfSummary(map).map((line) => (
                  <tr key={line}>
                    <td className="muted">{line.split(':')[0]}</td>
                    <td>{line.includes(':') ? line.slice(line.indexOf(':') + 1).trim() : line}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Копии</h3>
            <table className="grid compact">
              <thead>
                <tr>
                  <th>Копия</th>
                  <th>Поколение</th>
                  <th>Родилась</th>
                  <th>Форк</th>
                  <th>Создала</th>
                  <th>Взяла</th>
                  <th>У неё взяли</th>
                </tr>
              </thead>
              <tbody>
                {map.copies.map((copy) => (
                  <tr key={copy.instance}>
                    <td>
                      {copy.instance}
                      {copy.is_self ? ' (это я)' : ''}
                    </td>
                    <td className="muted">{copy.generation}</td>
                    <td className="muted">{formatDateTime(copy.born_at) || DASH}</td>
                    <td className="muted">{copy.fork_commit || DASH}</td>
                    <td className="muted">{copy.genes_made.length}</td>
                    <td className="muted">{copy.genes_taken.length}</td>
                    <td className="muted">{copy.taken_by.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Гены</h3>
            {map.genes.length === 0 ? (
              <p className="muted small">В пуле пока нет генов.</p>
            ) : (
              <table className="grid compact">
                <thead>
                  <tr>
                    <th>Ген</th>
                    <th>Что сделано</th>
                    <th>Автор</th>
                    <th>Состояние</th>
                    <th>Кто взял</th>
                  </tr>
                </thead>
                <tbody>
                  {map.genes.map((gene) => (
                    <tr key={gene.id}>
                      <td className="muted">{gene.id}</td>
                      <td>{gene.title || DASH}</td>
                      <td className="muted">{gene.author || DASH}</td>
                      <td className="muted">{gene.state}</td>
                      <td className="muted">{gene.taken_by.join(', ') || DASH}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            <h3>Связи</h3>
            {map.edges.length === 0 ? (
              <p className="muted small">Связей пока нет.</p>
            ) : (
              <ul className="muted small">
                {map.edges.map((edge, index) => (
                  <li key={`${edge.from}-${edge.to}-${index}`}>{describeEdge(edge, map)}</li>
                ))}
              </ul>
            )}
            <p className="muted small">{map.note}</p>
          </div>
        </>
      )}
    </section>
  )
}
