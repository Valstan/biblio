import { useState } from 'react'
import type { ChatReply, ToolCall } from '@shared/types'
import { api } from '../api'

interface Message {
  role: 'user' | 'assistant' | 'system'
  text: string
  calls?: ToolCall[]
}

/**
 * Чат с встроенной нейросетью «МиниГена».
 *
 * Нейросеть не «говорит» — она вызывает инструменты и выполняет их. Поэтому
 * в чате видно, что она сделала. Опасные действия (перезапуск области, смена
 * аккаунта, отправка сообщения) требуют подтверждения владельца.
 */
export default function ChatTab() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'system',
      text: 'Спросите, что делает программа, попросите проверить интернет у областей или разбудить сессию. Нейросеть видит состояние программы и выполняет действия через инструменты.'
    }
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<ChatReply['pending'] | undefined>(undefined)

  const apply = (reply: ChatReply) => {
    setMessages((prev) => [
      ...prev,
      { role: 'assistant', text: reply.text || '', calls: reply.calls }
    ])
    setPending(reply.pending ?? undefined)
    if (reply.status === 'unavailable' || reply.status === 'error') {
      setMessages((prev) => [
        ...prev,
        {
          role: 'system',
          text:
            reply.error ??
            'Нейросеть недоступна. Управление остаётся у алгоритмических функций — сторожа продолжают работу.'
        }
      ])
    }
  }

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', text }])
    setBusy(true)
    try {
      apply(await api.chatSend(text))
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'system', text: `чат недоступен: ${(err as Error).message}` }
      ])
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (approved: boolean) => {
    setBusy(true)
    try {
      apply(await api.chatConfirm(approved))
    } finally {
      setBusy(false)
      setPending(undefined)
    }
  }

  return (
    <section className="chat">
      <div className="row head">
        <h2>Чат с нейросетью</h2>
        <button
          className="ghost"
          onClick={() => {
            setMessages([])
            setPending(undefined)
            void api.chatReset()
          }}
        >
          Очистить
        </button>
      </div>

      <div className="chat-log">
        {messages.map((message, index) => (
          <div key={index} className={`bubble ${message.role}`}>
            {message.text && <div className="text">{message.text}</div>}
            {message.calls && message.calls.length > 0 && (
              <div className="calls">
                {message.calls.map((call, callIndex) => (
                  <div key={callIndex} className="call">
                    <span className="chip info">{call.tool}</span>
                    <span className="muted small">{JSON.stringify(call.args)}</span>
                    <div className="muted small result">
                      {JSON.stringify(call.result).slice(0, 400)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="bubble assistant muted">нейросеть думает…</div>}
      </div>

      {pending && (
        <div className="banner warn">
          Требуется подтверждение: <b>{pending.tool}</b> — {pending.reason}{' '}
          <span className="muted small">{JSON.stringify(pending.args)}</span>
          <div className="row">
            <button className="primary" onClick={() => void confirm(true)} disabled={busy}>
              Выполнить
            </button>
            <button className="ghost" onClick={() => void confirm(false)} disabled={busy}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="row chat-input">
        <input
          value={input}
          placeholder="Например: проверь интернет у всех областей"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void send()
            }
          }}
        />
        <button className="primary" onClick={() => void send()} disabled={busy || !input.trim()}>
          Отправить
        </button>
      </div>
    </section>
  )
}
