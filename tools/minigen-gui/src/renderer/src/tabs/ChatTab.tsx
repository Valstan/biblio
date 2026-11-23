import { useEffect, useState } from 'react'
import type { ChatReply, ModelCatalog, ModelEntry, ToolCall } from '@shared/types'
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
 *
 * Над чатом — выбор модели, размера контекста и уровня эффорта. Списки
 * берутся **из родного конфига платформы** (`contextWindowOptions`,
 * `thinking.effortOptions`), поэтому выбор выглядит как в родной среде и
 * останется верным, когда появится другая платформа.
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
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null)
  const [model, setModel] = useState('')
  const [context, setContext] = useState(0)
  const [effort, setEffort] = useState('')
  const [modelNote, setModelNote] = useState('')

  const loadCatalog = async () => {
    try {
      const cat = await api.modelCatalog()
      setCatalog(cat)
      const eff = cat.effective
      setModel(eff?.model ?? cat.default_model)
      setContext(eff?.context ?? cat.area_context ?? 0)
      setEffort(eff?.effort ?? cat.area_effort ?? '')
    } catch (err) {
      setModelNote(`каталог моделей недоступен: ${(err as Error).message}`)
    }
  }

  useEffect(() => {
    void loadCatalog()
  }, [])

  const entry: ModelEntry | undefined = catalog?.models.find((m) => m.id === model)
  const contextOptions = entry?.context_options?.length
    ? entry.context_options
    : (catalog?.effective?.context_options ?? [])
  const effortOptions = entry?.effort_options?.length
    ? entry.effort_options
    : (catalog?.effective?.effort_options ?? [])

  const saveModel = async () => {
    setBusy(true)
    try {
      await api.saveSettings({ model, modelContext: context, modelEffort: effort })
      setModelNote('сохранено: модель, контекст и эффорт — и для чата, и для областей')
    } catch (err) {
      setModelNote(`не сохранилось: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const applyToAreas = async () => {
    setBusy(true)
    try {
      await api.saveSettings({ model, modelContext: context, modelEffort: effort })
      const res = await api.modelApply({ model, modelContext: context, modelEffort: effort })
      const changed = res.changed ?? 0
      const failed = res.failed?.length ?? 0
      setModelNote(
        `применено к областям: изменено ${changed}${failed ? `, не получилось: ${failed}` : ''}`
      )
    } catch (err) {
      setModelNote(`не применилось: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

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

      <div className="card model-bar">
        <div className="row">
          <label>
            Модель{' '}
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {(catalog?.models ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} ({item.id})
                </option>
              ))}
            </select>
          </label>
          <label>
            Контекст{' '}
            <select value={context} onChange={(e) => setContext(Number(e.target.value))}>
              {contextOptions.map((value) => (
                <option key={value} value={value}>
                  {value.toLocaleString('ru-RU')}
                  {entry?.context_hints?.[String(value)] ? ` (${entry.context_hints[String(value)]})` : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            Эффорт{' '}
            <select value={effort} onChange={(e) => setEffort(e.target.value)}>
              {effortOptions.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button className="ghost" onClick={() => void saveModel()} disabled={busy}>
            Сохранить
          </button>
          <button className="ghost" onClick={() => void applyToAreas()} disabled={busy}>
            Применить к областям
          </button>
        </div>
        <div className="muted small">
          {catalog
            ? `Провайдер ${catalog.provider_name} · настройки взяты из его родового конфига${catalog.config_path ? ` (${catalog.config_path})` : ''}`
            : 'каталог моделей загружается…'}
          {modelNote ? ` · ${modelNote}` : ''}
        </div>
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
