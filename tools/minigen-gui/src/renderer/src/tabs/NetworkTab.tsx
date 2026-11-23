import { useState } from 'react'
import type { NetworkState, Snapshot } from '@shared/types'
import { api } from '../api'

interface Props {
  snapshot: Snapshot | null
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
}

export default function NetworkTab({ snapshot, run, busy }: Props) {
  const [live, setLive] = useState<NetworkState | null>(null)
  const [note, setNote] = useState('')

  const check = async () => {
    setNote('проверяю…')
    const result = await api.networkCheck()
    setLive(result)
    setNote('')
  }

  const network = live ?? snapshot?.network ?? null

  return (
    <section>
      <div className="row head">
        <h2>Сеть и вендор</h2>
        <div className="row">
          <button className="primary" onClick={() => void run(check)} disabled={busy}>
            Проверить все
          </button>
        </div>
      </div>

      {note && <div className="banner">{note}</div>}

      {network && (
        <>
          <div className="cards">
            <div className="card">
              <div className="num">
                {(network.paths || []).filter((path) => path.alive).length}/
                {(network.paths || []).length}
              </div>
              <div className="muted">путей живо</div>
            </div>
            <div className="card">
              <div className="num">{network.stable ? 'да' : 'нет'}</div>
              <div className="muted">связь устойчива</div>
            </div>
            <div className="card">
              <div className="num">
                {String((network.vendor as Record<string, unknown>)?.code ?? '—')}
              </div>
              <div className="muted">код вендора</div>
            </div>
          </div>

          <div className="card">
            <h3>Пути (прокси)</h3>
            {(network.paths || []).length === 0 && (
              <p className="muted">
                Пути не измерены. Нажмите «Проверить все» — измерение идёт по сети.
              </p>
            )}
            <table className="grid compact">
              <thead>
                <tr>
                  <th>Путь</th>
                  <th>Роль</th>
                  <th>Жив</th>
                  <th>Код</th>
                  <th>Страна</th>
                  <th>Заметка</th>
                </tr>
              </thead>
              <tbody>
                {(network.paths || []).map((path) => (
                  <tr key={path.name}>
                    <td>{path.name}</td>
                    <td className="muted">{path.role}</td>
                    <td>
                      <span className={`dot ${path.alive ? 'ok' : 'bad'}`} />
                      {path.alive ? 'да' : 'нет'}
                    </td>
                    <td>{path.code || '—'}</td>
                    <td>{path.country || '—'}</td>
                    <td className="muted small">{path.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="split">
            <div className="card">
              <h3>Вендор MiniMax</h3>
              <pre className="dump">{JSON.stringify(network.vendor ?? {}, null, 2)}</pre>
            </div>
            <div className="card">
              <h3>Прокси в окружении</h3>
              <pre className="dump">{JSON.stringify(network.proxy ?? {}, null, 2)}</pre>
            </div>
          </div>

          {snapshot?.daemons && (
            <div className="card">
              <h3>Демоны</h3>
              <div className="chips">
                <span className={`chip ${snapshot.daemons.supervisor ? 'ok' : 'muted'}`}>
                  супервизор: {snapshot.daemons.supervisor ? 'жив' : 'нет'}
                </span>
                {Object.entries(snapshot.daemons.procs).map(([name, alive]) => (
                  <span key={name} className={`chip ${alive ? 'ok' : 'muted'}`}>
                    {name}: {alive ? 'жив' : 'нет'}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  )
}
