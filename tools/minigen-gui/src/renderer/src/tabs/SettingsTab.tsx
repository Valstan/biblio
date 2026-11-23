import { useEffect, useState } from 'react'
import type { Settings, Snapshot } from '@shared/types'
import { api } from '../api'

interface Props {
  settings: Settings | null
  snapshot: Snapshot | null
  onSaved: (settings: Settings) => void
}

export default function SettingsTab({ settings, snapshot, onSaved }: Props) {
  const [form, setForm] = useState<Settings | null>(settings)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (settings && !form) setForm(settings)
  }, [settings, form])

  if (!form) return <section><p className="muted">Загружаю настройки…</p></section>

  const save = async () => {
    const saved = await api.saveSettings(form)
    onSaved(saved)
    setNote('сохранено')
  }

  return (
    <section>
      <div className="row head">
        <h2>Настройки</h2>
        <button className="primary" onClick={() => void save()}>
          Сохранить
        </button>
      </div>
      {note && <div className="banner">{note}</div>}

      <div className="card">
        <div className="grid2">
          <label>
            Обновление окна, мс
            <input
              type="number"
              value={form.refreshMs}
              onChange={(e) => setForm({ ...form, refreshMs: Number(e.target.value) })}
            />
          </label>
          <label>
            Строк журнала в хвосте
            <input
              type="number"
              value={form.journalTail}
              onChange={(e) => setForm({ ...form, journalTail: Number(e.target.value) })}
            />
          </label>
          <label>
            Область с конфигом провайдера (для чата)
            <input
              value={form.modelArea}
              onChange={(e) => setForm({ ...form, modelArea: e.target.value })}
            />
          </label>
          <label>
            Модель (пусто = по умолчанию из config.yaml)
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              placeholder="MiniMax-M3"
            />
          </label>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={form.confirmDangerous}
            onChange={(e) => setForm({ ...form, confirmDangerous: e.target.checked })}
          />
          Подтверждать опасные действия нейросети
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={form.manageDaemons}
            onChange={(e) => setForm({ ...form, manageDaemons: e.target.checked })}
          />
          Программа управляет демонами (нужно, если супервизора панели нет)
        </label>
      </div>

      {snapshot && (
        <div className="card">
          <h3>Пути</h3>
          <table className="grid compact">
            <tbody>
              <tr>
                <td>Каталоги областей</td>
                <td className="muted">{snapshot.areas_root}</td>
              </tr>
              <tr>
                <td>Боевые демоны</td>
                <td className="muted">{snapshot.daemon_dir}</td>
              </tr>
              <tr>
                <td>Журнал</td>
                <td className="muted">{snapshot.journal_path}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
