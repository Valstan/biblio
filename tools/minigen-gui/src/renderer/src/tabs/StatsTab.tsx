import { useEffect, useState } from 'react'
import type { Stats } from '@shared/types'
import { api } from '../api'
import { buildStatsCsv } from '../format'

interface Props {
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}

const DAYS = [1, 3, 7, 30]

export default function StatsTab({ run, busy }: Props) {
  const [days, setDays] = useState(7)
  const [stats, setStats] = useState<Stats | null>(null)
  const [note, setNote] = useState('')

  const load = () =>
    api.stats(days).then((data) => {
      setStats(data)
      return data
    })

  useEffect(() => {
    void load()
  }, [days])

  const exportCsv = async () => {
    if (!stats) return
    const name = `minigen-stats-${days}d.csv`
    const res = await window.minigen.saveText(name, '﻿' + buildStatsCsv(stats))
    setNote(res.ok ? `сохранено: ${res.path}` : 'не сохранилось')
  }

  const reasons = Object.entries(stats?.by_reason ?? {})
  const max = Math.max(1, ...reasons.map(([, count]) => count))

  return (
    <section>
      <div className="row head">
        <h2>Статистика работы</h2>
        <div className="row">
          {DAYS.map((value) => (
            <button
              key={value}
              className={days === value ? 'tab active' : 'tab'}
              onClick={() => setDays(value)}
            >
              {value} дн
            </button>
          ))}
          <button className="ghost" onClick={() => void run(load)} disabled={busy}>
            Пересчитать
          </button>
          <button className="ghost" onClick={() => void exportCsv()}>
            Экспорт CSV
          </button>
        </div>
      </div>

      {note && <div className="banner">{note}</div>}

      {stats && (
        <>
          <div className="cards">
            <div className="card">
              <div className="num">{stats.restarts}</div>
              <div className="muted">событий за {stats.days} дн</div>
            </div>
            <div className="card">
              <div className="num">
                {stats.areas_running}/{stats.areas_total}
              </div>
              <div className="muted">областей запущено</div>
            </div>
            <div className="card">
              <div className="num">{stats.network_events}</div>
              <div className="muted">сетевых событий</div>
            </div>
            <div className="card">
              <div className="num">{Object.keys(stats.by_kind).length}</div>
              <div className="muted">видов событий</div>
            </div>
          </div>

          <div className="split">
            <div className="card">
              <h3>По причинам</h3>
              {reasons.length === 0 && <p className="muted">Событий нет.</p>}
              {reasons.map(([reason, count]) => (
                <div key={reason} className="bar-row">
                  <span className="bar-label">{reason}</span>
                  <span className="bar">
                    <span className="bar-fill" style={{ width: `${(count / max) * 100}%` }} />
                  </span>
                  <span className="bar-count">{count}</span>
                </div>
              ))}
              {Object.keys(stats.by_kind).length > 0 && (
                <>
                  <h3>По видам</h3>
                  <div className="chips">
                    {Object.entries(stats.by_kind).map(([kind, count]) => (
                      <span key={kind} className="chip">
                        {kind}: {count}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="card">
              <h3>По областям</h3>
              <table className="grid compact">
                <thead>
                  <tr>
                    <th>Область</th>
                    <th>Событий</th>
                    <th>Последнее</th>
                    <th>Запущена</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_area.map((area) => (
                    <tr key={area.slug}>
                      <td>{area.slug}</td>
                      <td>{area.total}</td>
                      <td className="muted small">{area.last || '—'}</td>
                      <td>{area.running ? 'да' : 'нет'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <h3>Последние события</h3>
            {stats.events.length === 0 && <p className="muted">Событий не найдено.</p>}
            <ul className="events">
              {stats.events.map((event, index) => (
                <li key={`${event.ts}-${index}`}>
                  <span className="muted small">{event.ts || '—'}</span>
                  <span className="chip">{event.slug}</span>
                  <span className="chip info">{event.kind}</span>
                  <span className="muted small">{event.reason}</span>
                  <div className="muted small">{event.text}</div>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </section>
  )
}
