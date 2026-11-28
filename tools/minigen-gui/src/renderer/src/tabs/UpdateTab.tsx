import { useCallback, useEffect, useState } from 'react'
import type { GeneSuggestions, UpdateState } from '@shared/types'
import { api, desktop } from '../api'
import { describeUpdate, formatInstalledAt } from './update-view'
import { notWantedRows, suggestionRows } from './copy-view'
import { formatTime } from '../time'

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
 * - **наработки других копий** с рейтингом и пометками — чтобы решение
 *   «ставить ген или нет» принималось по фактам (промт КАРМАНА #187);
 * - кнопка перезапуска окна: раскатка меняет файлы, а старый код в памяти
 *   сам не сменится.
 */
export default function UpdateTab() {
  const [state, setState] = useState<UpdateState | null>(null)
  const [build, setBuild] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [works, setWorks] = useState<GeneSuggestions | null>(null)
  const [worksError, setWorksError] = useState('')

  const load = useCallback(async () => {
    try {
      setState(await api.updateStatus())
    } catch {
      /* мост занят — следующий опрос всё покажет */
    }
  }, [])

  /** Наработки других копий — их же видно во вкладке «Гены». */
  const loadWorks = useCallback(async () => {
    try {
      setWorks(await api.genesSuggest())
      setWorksError('')
    } catch (err) {
      setWorksError(`наработки не принесли: ${String((err as Error).message || err)}`)
    }
  }, [])

  useEffect(() => {
    void load()
    void loadWorks()
    void desktop.buildInfo().then((info) => setBuild(info.commit || '')).catch(() => undefined)
    const timer = window.setInterval(() => void load(), 1500)
    const worksTimer = window.setInterval(() => void loadWorks(), 5000)
    return () => {
      window.clearInterval(timer)
      window.clearInterval(worksTimer)
    }
  }, [load, loadWorks])

  const installed = state?.installed ?? null
  const verdict = describeUpdate(installed, build, state)
  const steps = state?.steps ?? []
  // Наработки других копий: рейтинг, число голосов и пометки — видны **до**
  // того, как владелец решит, ставить ген или нет (промт КАРМАНА #187).
  const workRows = suggestionRows(works)
  const refusedRows = notWantedRows(works)

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
                  ? ` · ${formatInstalledAt(installed.installed_at)}`
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
                      {formatTime(step.ts)}
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

      <div className="card">
        <h3>Наработки других копий</h3>
        {worksError && <p className="muted small">{worksError}</p>}
        {!worksError && workRows.length === 0 && refusedRows.length === 0 && (
          <p className="muted small">
            {works?.available
              ? 'Чужих наработок сейчас нет.'
              : 'Наработки сейчас недоступны: обмен с копиями не работает.'}
          </p>
        )}
        {workRows.length > 0 && (
          <table className="grid compact">
            <thead>
              <tr>
                <th>Ген</th>
                <th>Что сделано</th>
                <th>Автор</th>
                <th>Рейтинг</th>
                <th>Пометки</th>
              </tr>
            </thead>
            <tbody>
              {workRows.map((row) => (
                <tr key={row.gene}>
                  <td className="muted">{row.gene}</td>
                  <td>{row.title}</td>
                  <td className="muted">{row.author}</td>
                  <td className="muted small">
                    {row.rating.has
                      ? `${row.rating.score} ${row.rating.stars} · голосов: ${row.rating.votes}`
                      : 'оценок нет'}
                  </td>
                  <td>
                    {row.touchesAppearance && (
                      <span className="chip warn">меняет внешний вид</span>
                    )}
                    {row.rolledBackElsewhere && (
                      <span className="chip bad">откатывали в другой копии</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {refusedRows.length > 0 && (
          <>
            <p className="muted small" style={{ marginTop: 8 }}>
              Эти гены уже откатывали (или пометили «не ставить») — в список
              наработок они не попадают.
            </p>
            <table className="grid compact">
              <thead>
                <tr>
                  <th>Ген</th>
                  <th>Что сделано</th>
                  <th>Пометка</th>
                  <th>Рейтинг</th>
                </tr>
              </thead>
              <tbody>
                {refusedRows.map((row) => (
                  <tr key={row.gene}>
                    <td className="muted">{row.gene}</td>
                    <td>{row.title}</td>
                    <td>
                      <span className={row.rolledBack ? 'chip warn' : 'chip muted'}>
                        {row.why}
                      </span>
                    </td>
                    <td className="muted small">
                      {row.rating.has
                        ? `${row.rating.score} · голосов: ${row.rating.votes}`
                        : 'оценок нет'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <p className="muted small">
          Рейтинг и пометки видны до решения о внедрении: внедряют гены во
          вкладке «Гены».
        </p>
      </div>
    </section>
  )
}
