import { beforeEach, describe, expect, it } from 'vitest'
import type { ChatMessage, PendingAction } from '../src/shared/types'
import * as chatStore from '../src/renderer/src/tabs/chat-store'

/**
 * Состояние переписки «Управляющего» живёт вне вкладки (промт #129).
 *
 * Проверяем главное: размонтирование вкладки (уход на другую вкладку окна)
 * переписку **не стирает** — она лежит в модуле, а не в `useState` компонента.
 */

const user: ChatMessage = { role: 'user', text: 'проверь сеть' }
const answer: ChatMessage = { role: 'assistant', text: 'проверил: сеть есть' }

beforeEach(() => {
  chatStore.reset()
})

describe('стор переписки', () => {
  it('сообщения переживают размонтирование вкладки', () => {
    // вкладка открыта: подписка есть
    const off = chatStore.subscribe(() => undefined)
    chatStore.setMessages([user, answer])
    off()
    // вкладку закрыли (уход на другую вкладку окна) и снова открыли
    const again = chatStore.subscribe(() => undefined)
    expect(chatStore.getMessages()).toEqual([user, answer])
    again()
  })

  it('restore заменяет список целиком', () => {
    chatStore.setMessages([user])
    chatStore.restore([answer])
    expect(chatStore.getMessages()).toEqual([answer])
  })

  it('reset очищает переписку', () => {
    chatStore.setMessages([user, answer])
    chatStore.reset()
    expect(chatStore.getMessages()).toEqual([])
  })

  it('пока список не сменили, ссылка та же — снимок для React стабилен', () => {
    chatStore.setMessages([user])
    const first = chatStore.getMessages()
    expect(chatStore.getMessages()).toBe(first)
    chatStore.setMessages([user, answer])
    expect(chatStore.getMessages()).not.toBe(first)
  })

  it('подписчика зовут при изменении, а после отписки — нет', () => {
    let count = 0
    const off = chatStore.subscribe(() => {
      count += 1
    })
    chatStore.setMessages([user])
    chatStore.restore([answer])
    chatStore.reset()
    expect(count).toBe(3)
    off()
    chatStore.setMessages([user])
    expect(count).toBe(3)
  })
})

describe('ожидающее подтверждение и занятость', () => {
  const pending: PendingAction = {
    tool: 'code.commit',
    args: { branch: 'fix/defect' },
    reason: 'нужно подтверждение владельца',
    call_id: 't1'
  }

  it('подтверждение переживает размонтирование вкладки', () => {
    const off = chatStore.subscribe(() => undefined)
    chatStore.setPending(pending)
    off()
    // вкладку закрыли (уход на другую вкладку окна) и открыли снова
    const again = chatStore.subscribe(() => undefined)
    expect(chatStore.getPending()).toEqual(pending)
    again()
  })

  it('занятость переживает размонтирование вкладки', () => {
    chatStore.setBusy(true)
    expect(chatStore.getBusy()).toBe(true)
  })

  it('reset чистит подтверждение и занятость', () => {
    chatStore.setPending(pending)
    chatStore.setBusy(true)
    chatStore.reset()
    expect(chatStore.getPending()).toBeUndefined()
    expect(chatStore.getBusy()).toBe(false)
  })

  it('ссылка на подтверждение стабильна, пока его не сменили', () => {
    chatStore.setPending(pending)
    const first = chatStore.getPending()
    expect(chatStore.getPending()).toBe(first)
    chatStore.setPending(undefined)
    expect(chatStore.getPending()).toBeUndefined()
  })
})
