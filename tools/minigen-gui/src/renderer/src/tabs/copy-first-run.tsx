import type { CopyFirstRunSnapshot } from '@shared/types'
import { firstRunView } from './copy-view'

/**
 * Вопрос первого запуска копии — **тот самый вопрос** (заказ владельца
 * 25.11, промт #131).
 *
 * ⚠️ Один компонент на два места (окно при первом открытии и вкладка
 * «Настройки») — намеренно: если текст вопроса разойдётся в двух местах,
 * окно однажды скажет владельцу не то. Здесь — только содержимое вопроса:
 * репозиторий, какая папка копии появится, что это даёт и что будет при
 * отказе, — и две кнопки: «Согласиться» / «Отказаться».
 *
 * Честность — правило этого вопроса:
 * - **отказ не ломает программу** (копия просто работает локально), поэтому
 *   обе кнопки равноправны;
 * - **репозиторий принадлежит владельцу**, а согласие даётся на этой машине
 *   и касается только этой копии — это написано в тексте;
 * - **нет ответа моста — «неизвестно»**, а не пустой вопрос.
 */
export default function CopyFirstRunQuestion({
  snapshot,
  busy,
  onAgree,
  onRefuse
}: {
  snapshot: CopyFirstRunSnapshot | null
  busy?: boolean
  onAgree: () => void
  onRefuse: () => void
}) {
  const first = firstRunView(snapshot)

  if (!snapshot) {
    return (
      <p className="muted small">
        Мост не ответил — вопрос первого запуска не задан, состояние копии
        неизвестно.
      </p>
    )
  }

  if (first.asked) {
    return (
      <p className="muted small">
        Вопрос первого запуска уже задан:{' '}
        {snapshot.first_run?.agreed ? 'согласие' : 'отказ'}. Режим копии меняется
        в настройках.
      </p>
    )
  }

  return (
    <>
      <p className="title">{first.title}</p>
      <table className="grid compact">
        <tbody>
          <tr>
            <td className="muted">Репозиторий</td>
            <td>{first.repository}</td>
          </tr>
          <tr>
            <td className="muted">Папка копии</td>
            <td>{first.folder}</td>
          </tr>
        </tbody>
      </table>
      <p className="muted small">Что это даёт:</p>
      <ul className="muted small">
        {(first.gives.length ? first.gives : ['—']).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="muted small">Если отказаться:</p>
      <ul className="muted small">
        {(first.ifRefuse.length ? first.ifRefuse : ['—']).map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="muted small">{first.who}</p>
      <div className="row">
        <button className="primary" onClick={onAgree} disabled={busy}>
          Согласиться
        </button>
        <button className="ghost" onClick={onRefuse} disabled={busy}>
          Отказаться
        </button>
      </div>
    </>
  )
}
