import { useCallback, useEffect, useState } from 'react'
import type { UpdateState } from '@shared/types'
import { api, desktop } from '../api'
import { describeUpdate } from './update-view'

/**
 * Вкладка «Обновление» — наглядный процесс обновления программы.
 *
 * Заказ владельца (24.11): «как мне запустить процесс обновления из
 * графического интерфейса программы? Сделай процесс обновления наглядным».
 *
 * Видно всё, что важно:
 * - какая версия стоит на диске и из какой сборки запущено окно
 *   (метка сборки вшивается при сборке — по ней видно, что окно старше);
 * - что есть в репозитории;
 * - пошаговый журнал обновления (каждый шаг — одна строка);
 * - кнопка перезапуска окна: раскатка меняет файлы, а старый код в памяти
 *   сам не сменится.
 */
export default function UpdateTab() {
  const [state, setState] = useState<UpdateState | null>(null)
  const [build, setBuild] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setState(await api.updateStatus())
    } catch {
      /* мост занят — следующий опрос всё покажет */
    }
  }, [])

  useEffect(() => {
    void load()
    void desktop.buildInfo().then((info) => setBuild(info.commit || '')).catch(() => undefined)
    const timer = window.setInterval(() => void load(), 1500)
    return () => window.clearInterval(timer)
  }, [load])

  const installed = state?.installed ?? null
  const verdict = describeUpdate(installed, build, state)
  const steps = state?.steps ?? []

  const check = async () => {
    setBusy(true)
    setNote('спрашиваю репозиторий…')
    try {
      const info = await api.updateCheck()
      setNote(info.why || (info.ok ? 'проверено' : 'проверить не вышло'))
      await load()
    } catch (err) {
      setNote(String((err as Error).message || err))
    } finally {
      setBusy(false)
    }
  }

  const start = async () => {
    const ok = window.confirm(
      'Обновить программу?\n\n' +
        'Программа заберёт новую версию из репозитория, соберёт окно и поставит ' +
        'программу на место. Демоны на это время перезапустятся, окно — ' +
        'после обновления (оно продолжит работать на старом коде, пока не ' +
        'перезапустишь).'
    )
    if (!ok) return
    setBusy(true)
    setNote('обновление пошло — шаги ниже')
    try {
      const result = await api.updateStart()
      setNote(result.why || result.error || '')
      await load()
    } catch (err) {
      setNote(String((err as Error).message || err))
    } finally {
      setBusy(false)
    }
  }

  const relaunch = async () => {
    if (!window.confirm('Перезапустить окно сейчас? Новый код подхватится сразу.')) return
    await desktop.relaunch()
  }

  return (
    <section>
      <div className="row head">
        <h2>Обновление программы</h2>
        <div className="row">
          <button className="ghost" onClick={() => void check()} disabled={busy || state?.running}>
            Проверить обновления
          </button>
          <button className="primary" onClick={() => void start()} disabled={busy || state?.running}>
            Обновить программу
          </button>
        </div>
      </div>

      {note && <div className="banner">{note}</div>}
      <div className={`banner ${verdict.tone === 'ok' ? '' : verdict.tone}`}>{verdict.text}</div>

      {verdict.needRestart && (
        <div className="row">
          <button className="primary" onClick={() => void relaunch()}>
            Перезапустить программу
          </button>
          <span className="muted small">
            Перезапуск окна — единственный способ перейти на новый код: раскатка
            меняет файлы на диске, а запущенное окно работает на старом.
          </span>
        </div>
      )}

      <div className="card">
        <h3>Состояние</h3>
        <table className="grid compact">
          <tbody>
            <tr>
              <td>Установлено на месте</td>
              <td className="muted">
                {installed?.commit ? `коммит ${installed.commit}` : 'неизвестно'}
                {installed?.installed_at
                  ? ` · ${new Date(installed.installed_at).toLocaleString('ru-RU')}`
                  : ''}
              </td>
            </tr>
            <tr>
              <td>Окно собрано из</td>
              <td className="muted">{build ? `коммита ${build}` : 'метки нет (сборка без метки)'}</td>
            </tr>
            <tr>
              <td>В репозитории</td>
              <td className="muted">
                {state?.available?.commit ? `коммит ${state.available.commit}` : 'неизвестно'}
                {state?.available?.behind ? ` · новых коммитов: ${state.available.behind}` : ''}
              </td>
            </tr>
            <tr>
              <td>Исходники</td>
              <td className="muted">{state?.repo || '—'}</td>
            </tr>
          </tbody>
        </table>
        <p className="muted small">
          Обновление идёт из репозитория: забрать изменения → собрать окно
          (npm run build) → поставить программу на место и перезапустить
          демонов. Никакого деплоя — та же раскатка, что и вручную.
        </p>
      </div>

      <div className="card">
        <h3>Шаги обновления</h3>
        {steps.length === 0 ? (
          <p className="muted small">Обновление ещё не запускали — журнал пуст.</p>
        ) : (
          <table className="grid compact">
            <tbody>
              {steps
                .slice()
                .reverse()
                .map((step, index) => (
                  <tr key={`${step.ts}-${index}`}>
                    <td style={{ width: 90 }} className="muted small">
                      {new Date(step.ts).toLocaleTimeString('ru-RU')}
                    </td>
                    <td style={{ width: 110 }} className="muted small">
                      {step.step}
                    </td>
                    <td>
                      <span
                        className={`dot ${step.status === 'error' ? 'bad' : step.status === 'ok' ? 'ok' : ''}`}
                      />
                      {step.text}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
        {state?.running && <p className="muted small">Обновление идёт — не закрывай окно.</p>}
      </div>
    </section>
  )
}
