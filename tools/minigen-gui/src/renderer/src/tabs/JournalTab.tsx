import { useEffect, useState } from 'react'
import type { JournalLine, Snapshot } from '@shared/types'
import { api } from '../api'

interface Props {
  snapshot: Snapshot | null
}

const TAGS = ['', 'СЕТЬ', 'СЕССИИ', 'АВТОЛЕЧЕНИЕ', 'ОБОЗРЕНИЕ']

export default function JournalTab({ snapshot }: Props) {
  const [lines, setLines] = useState<JournalLine[]>([])
  const [filter, setFilter] = useState('')
  const [tag, setTag] = useState('')
  const [live, setLive] = useState(true)
  const [path, setPath] = useState('')

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .journal({ limit: 300, contains: filter, tag })
        .then((data) => {
          if (!alive) return
          setLines(data.lines)
          setPath(data.path)
        })
        .catch(() => undefined)
    void load()
    if (!live) return () => { alive = false }
    const timer = window.setInterval(() => void load(), 5000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [filter, tag, live])

  return (
    <section>
      <div className="row head">
        <h2>Журнал</h2>
        <div className="row">
          <input
            value={filter}
            placeholder="фильтр по тексту"
            onChange={(e) => setFilter(e.target.value)}
          />
          {TAGS.map((value) => (
            <button
              key={value || 'all'}
              className={tag === value ? 'tab active' : 'tab'}
              onClick={() => setTag(value)}
            >
              {value || 'все'}
            </button>
          ))}
          <button className={live ? 'tab active' : 'tab'} onClick={() => setLive((v) => !v)}>
            {live ? 'вживую' : 'пауза'}
          </button>
        </div>
      </div>

      <p className="muted small">{path}</p>

      <div className="card journal">
        {lines.length === 0 && <p className="muted">Записей нет.</p>}
        {lines.map((line, index) => (
          <div key={index} className="jline">
            <span className="muted small jtime">{line.ts || '—'}</span>
            <span className="jtext">{line.text}</span>
          </div>
        ))}
      </div>

      {snapshot && (
        <p className="muted small">
          Демоны:{' '}
          {(snapshot.daemons.daemons ?? [])
            .map((daemon) => `${daemon.name}: ${daemon.alive ? 'жив' : 'нет'}`)
            .join(' · ')}
        </p>
      )}
    </section>
  )
}
