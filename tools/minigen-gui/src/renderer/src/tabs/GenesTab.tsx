import { useCallback, useEffect, useState } from 'react'
import type { GeneMap } from '@shared/types'
import { api } from '../api'
import { formatDateTime } from '../time'
import { describeEdge, layoutMap, selfSummary, NODE_SIZE } from './gene-map'

/**
 * Вкладка «Гены» — **карта родословной**: паутина копий «МиниГена» и генов
 * между ними (заказ владельца 24.11).
 *
 * Что показывает:
 * - граф: колонка — поколение; корень — версия программы, от которой берут
 *   форк; копии — узлы, гены (наработки) — между ними;
 * - «я в этой паутине»: кто эта копия, сколько генов создала, сколько взяла,
 *   у кого сколько позаимствовали;
 * - таблицы копий и генов и список связей текстом.
 *
 * Карта собирается из **пула наработок** (общий репозиторий): каждая копия
 * дописывает туда свои связи, поэтому карта общая для всех копий.
 */
export default function GenesTab() {
  const [map, setMap] = useState<GeneMap | null>(null)
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    try {
      setMap(await api.mapShow())
    } catch (err) {
      setNote(`карту не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => window.clearInterval(timer)
  }, [load])

  if (!map) {
    return (
      <section>
        <div className="row head">
          <h2>Гены</h2>
        </div>
        <p className="muted">{note || 'Собираю карту…'}</p>
      </section>
    )
  }

  const layout = layoutMap(map)
  const self = selfSummary(map)
  const copies = map.copies
  const genes = map.genes

  return (
    <section>
      <div className="row head">
        <h2>Гены</h2>
        <div className="row">
          <button className="ghost" onClick={() => void load()}>
            Обновить карту
          </button>
        </div>
      </div>

      {note && <div className="banner">{note}</div>}
      {!map.pool_ready && (
        <div className="banner warn">
          Пула наработок нет — карта пока только про эту копию. Пул заводится
          командой <code>clone.setup</code>.
        </div>
      )}

      <div className="card">
        <h3>Паутина разработки</h3>
        <p className="muted small">
          {map.stats.copies} копий · {map.stats.genes} генов · {map.stats.links}{' '}
          связей · {map.stats.events} событий в пуле. Колонка — поколение: слева
          корень (версия программы), правее копии и гены между ними.
        </p>
        {layout.nodes.length === 0 ? (
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
            {self.map((line) => (
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
            {copies.map((copy) => (
              <tr key={copy.instance}>
                <td>
                  {copy.instance}
                  {copy.is_self ? ' (это я)' : ''}
                </td>
                <td className="muted">{copy.generation}</td>
                <td className="muted">{formatDateTime(copy.born_at) || '—'}</td>
                <td className="muted">{copy.fork_commit || '—'}</td>
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
        {genes.length === 0 ? (
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
              {genes.map((gene) => (
                <tr key={gene.id}>
                  <td className="muted">{gene.id}</td>
                  <td>{gene.title || '—'}</td>
                  <td className="muted">{gene.author || '—'}</td>
                  <td className="muted">{gene.state}</td>
                  <td className="muted">{gene.taken_by.join(', ') || '—'}</td>
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
    </section>
  )
}
