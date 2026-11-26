/**
 * Логика чата «Управляющего» — чистая, без React и без сети.
 *
 * Здесь то, что видит владелец: приветствие, свёртка вызовов инструментов
 * («Что сделано: N действий»), подробности вызова (полные аргументы и полный
 * результат — без обрезки) и сборка списка реплик из ответа моста.
 *
 * Проверяется тестом (`tests/chat-view.test.ts`): правило владельца —
 * приёмка правилом, а не «на глаз».
 */

import type { ChatMessage, ChatReply, PendingAction, ToolCall } from '@shared/types'

/**
 * Приветствие — локальное системное сообщение: всегда первое, на диск не
 * пишется (в файле истории его нет).
 */
const GREETING =
  'Я — Управляющий «МиниГена». Знаю устройство программы, вижу её состояние и выполняю действия через инструменты. Скажите, что проверить или починить: сеть и «минимаксы» у областей, состояние сессий, состояние самой программы.'

/** Что говорить, когда нейросеть не ответила и не сказала почему. */
const NO_MODEL =
  'Нейросеть недоступна. Управление остаётся у алгоритмических функций — сторожа продолжают работу.'

/** Приветствие окна: первая реплика переписки. */
export function greetingMessage(): ChatMessage {
  return { role: 'system', text: GREETING }
}

/** Русская форма слова: 1 действие / 2-4 действия / 5+ действий. */
function pluralActions(count: number): string {
  const mod100 = count % 100
  const mod10 = count % 10
  if (mod100 >= 11 && mod100 <= 14) return `${count} действий`
  if (mod10 === 1) return `${count} действие`
  if (mod10 >= 2 && mod10 <= 4) return `${count} действия`
  return `${count} действий`
}

/**
 * Свёртка вызовов инструментов: «Что сделано: 3 действия».
 * Пустых вызовов нет — значит и свёртки нет.
 */
export function callsSummary(calls: ToolCall[]): string {
  const count = calls?.length ?? 0
  if (count <= 0) return ''
  return `Что сделано: ${pluralActions(count)}`
}

/**
 * Подробности вызова: имя инструмента, полные аргументы и полный результат.
 *
 * ⚠️ Обрезки нет: владелец открывает детали по требованию и должен увидеть
 * результат целиком, а не первые 400 символов.
 */
export function callDetails(call: ToolCall): { tool: string; args: string; result: string } {
  return {
    tool: call?.tool ?? '',
    args: stringify(call?.args ?? {}),
    result: stringify(call?.result)
  }
}

/**
 * Подробности ожидающего подтверждения: аргументы опасного действия.
 *
 * ⚠️ Сырой JSON в лице баннера — это «технический блок» (промт #129): владелец
 * видит, **что** требует подтверждения, а сами аргументы открывает по клику.
 */
export function pendingArgs(pending: PendingAction): string {
  return stringify(pending?.args ?? {})
}

/**
 * Пометка под баннером: действие ждало подтверждения **до** перезапуска окна.
 *
 * ⚠️ Честно для владельца (промт #129): он отвечает на вопрос, заданный до
 * перезапуска, — а не на новый. Без пометки он бы решил, что это свежий вопрос.
 */
export function pendingNote(pending: PendingAction): string {
  if (!pending?.restored) return ''
  return 'Это действие ждало вашего ответа до перезапуска окна.'
}

/**
 * Привести сообщения моста к виду окна: убрать мусор (пустой текст без
 * вызовов), сохранить порядок, вернуть приветствие первым.
 */
export function restoreMessages(stored: ChatMessage[]): ChatMessage[] {
  const clean = (stored ?? []).filter(isVisible).filter((message) => message.text !== GREETING)
  return [greetingMessage(), ...clean]
}

/** Дописать реплику в переписку. */
export function appendMessage(messages: ChatMessage[], message: ChatMessage): ChatMessage[] {
  return [...(messages ?? []), message]
}

/**
 * Собрать реплики из ответа моста: ответ нейросети (текст и вызовы) — и,
 * если сбой, честная системная пометка с текстом ошибки.
 */
export function appendAssistant(messages: ChatMessage[], reply: ChatReply): ChatMessage[] {
  const next = [...(messages ?? [])]
  const text = (reply?.text ?? '').trim()
  const calls = reply?.calls ?? []
  if (text || calls.length > 0) {
    const message: ChatMessage = { role: 'assistant', text: reply.text ?? '' }
    if (calls.length > 0) message.calls = calls
    next.push(message)
  }
  if (reply?.status === 'unavailable' || reply?.status === 'error') {
    next.push({ role: 'system', text: reply.error || NO_MODEL })
  }
  return next
}

/** Реплика видна, если в ней есть текст, вызовы или приложенные файлы. */
function isVisible(message: ChatMessage): boolean {
  if (!message) return false
  if ((message.text ?? '').trim()) return true
  if ((message.calls?.length ?? 0) > 0) return true
  return (message.attachments?.length ?? 0) > 0
}

/**
 * Значение в текст: строка — как есть, остальное — JSON. Без обрезки.
 * `undefined` — пусто (такого значения в ответе моста быть не должно).
 */
function stringify(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    const text = JSON.stringify(value, null, 2)
    return text ?? String(value)
  } catch {
    return String(value)
  }
}
