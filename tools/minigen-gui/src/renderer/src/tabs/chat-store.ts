/**
 * Состояние переписки «Управляющего» — вне компонента.
 *
 * Переписка живёт в модуле окна, а не в `useState` внутри вкладки: вкладка
 * в окне ставится условием (`{tab === 'chat' && <ChatTab />}`), поэтому при
 * уходе на другую вкладку компонент размонтируется и `useState` обнулился бы.
 * Здесь переписка остаётся на месте и после возвращения на вкладку.
 *
 * ⚠️ Здесь же живёт **ожидающее подтверждение** (`pending`) и **занятость**
 * (`busy`). Это не украшение, а часть состояния чата:
 * - подтверждение опасного действия — это вопрос Управляющего владельцу. Если
 *   оно живёт в компоненте, уход на другую вкладку **молча съедает** вопрос:
 *   владелец возвращается и не видит, что на него ждут ответа (промт #129);
 * - занятость нужна, чтобы после возвращения на вкладку нельзя было слать
 *   вторую реплику, пока первая ещё думается.
 *
 * ⚠️ Стор — **только в памяти окна**. На диск историю пишет мост
 * (`chat.history`); окно её читает и рисует, но файл не ведёт.
 *
 * Подписка в React — через `useSyncExternalStore`: массив реплик меняется
 * ссылкой, поэтому снимок стабилен между отрисовками.
 */

import type { ChatMessage, PendingAction } from '@shared/types'

/** Слушатель изменений переписки. */
type Listener = () => void

/** Текущая переписка. Пока её не сменили — та же самая ссылка. */
let messages: ChatMessage[] = []
/** Опасное действие, ждущее подтверждения владельца (ссылка стабильна). */
let pending: PendingAction | undefined = undefined
/** Модель думает — отправлять вторую реплику нельзя. */
let busy = false

const listeners = new Set<Listener>()

/** Переписка сейчас (одна и та же ссылка, пока список не сменили). */
export function getMessages(): ChatMessage[] {
  return messages
}

/** Заменить переписку целиком и позвать подписчиков. */
export function setMessages(next: ChatMessage[]): void {
  messages = next
  notify()
}

/** Подписаться на изменения; возвращает отписку. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Очистить переписку (кнопка «Очистить»). */
export function reset(): void {
  messages = []
  pending = undefined
  busy = false
  notify()
}

/** Восстановить переписку из истории моста — список заменяется целиком. */
export function restore(stored: ChatMessage[]): void {
  messages = stored
  notify()
}

/** Ожидающее подтверждение (или `undefined`, если его нет). */
export function getPending(): PendingAction | undefined {
  return pending
}

/** Поставить (или снять) ожидающее подтверждение. */
export function setPending(next: PendingAction | undefined): void {
  pending = next
  notify()
}

/** Модель думает? */
export function getBusy(): boolean {
  return busy
}

/** Пометить: модель думает (или закончила). */
export function setBusy(next: boolean): void {
  busy = next
  notify()
}

/** Позвать подписчиков (копия списка — подписка может отписаться на ходу). */
function notify(): void {
  for (const listener of [...listeners]) listener()
}
