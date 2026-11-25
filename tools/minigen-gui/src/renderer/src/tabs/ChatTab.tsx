import { useEffect, useState, useSyncExternalStore } from 'react'
import type { ChatReply, ModelCatalog, ModelEntry } from '@shared/types'
import { api } from '../api'
import * as chatStore from './chat-store'
import {
  appendAssistant,
  appendMessage,
  callDetails,
  callsSummary,
  restoreMessages
} from './chat-view'

/**
 * «Управляющий» — чат с встроенной нейросетью «МиниГена».
 *
 * Владелец так назвал главный чат программы (24.11): нейросеть здесь —
 * управляющий, а не собеседник. Она видит состояние программы, читает её
 * документацию, вызывает инструменты и выполняет их.
 *
 * Нейросеть не «говорит» — она вызывает инструменты и выполняет их. Поэтому
 * в чате видно, что она сделала: вызовы свёрнуты («Что сделано: N действий»),
 * а подробности открываются по клику. Опасные действия (перезапуск области,
 * смена аккаунта, отправка сообщения) требуют подтверждения владельца.
 *
 * Переписка живёт **вне вкладки** (`tabs/chat-store.ts`): окно ставит вкладку
 * условием, поэтому при уходе на другую вкладку компонент размонтируется, а
 * переписка остаётся. После перезапуска окна её восстанавливает мост
 * (`chat.history` — история лежит на диске машины; окно её читает и рисует,
 * но файл не ведёт).
 *
 * Над чатом — выбор модели, размера контекста и уровня эффорта. Списки
 * берутся **из родного конфига платформы** (`contextWindowOptions`,
 * `thinking.effortOptions`), поэтому выбор выглядит как в родной среде и
 * останется верным, когда появится другая платформа.
 */
export default function ChatTab() {
  // Переписка — из стора модуля: размонтирование вкладки её не стирает.
  const messages = useSyncExternalStore(chatStore.subscribe, chatStore.getMessages)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<ChatReply['pending'] | undefined>(undefined)
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null)
  const [model, setModel] = useState('')
  const [context, setContext] = useState(0)
  const [effort, setEffort] = useState('')
  const [modelNote, setModelNote] = useState('')
  // Пока мост отвечает на историю — говорим честно, а не показываем пустоту.
  const [loadingHistory, setLoadingHistory] = useState(true)

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

  /**
   * Восстановить переписку из истории моста (файл на диске машины).
   * Мост может не ответить — тогда говорим честно, а не показываем пустоту.
   */
  const loadHistory = async () => {
    try {
      const history = await api.chatHistory()
      chatStore.restore(restoreMessages(history?.messages ?? []))
    } catch (err) {
      chatStore.setMessages(
        appendMessage(restoreMessages([]), {
          role: 'system',
          text: `мост не ответил — переписка не восстановлена: ${(err as Error).message}`
        })
      )
    } finally {
      setLoadingHistory(false)
    }
  }

  useEffect(() => {
    void loadCatalog()
    // Историю спрашиваем, только если переписки ещё нет: вернувшись на
    // вкладку, владелец видит ту же переписку, а не пустоту.
    if (chatStore.getMessages().length === 0) {
      void loadHistory()
    } else {
      setLoadingHistory(false)
    }
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

  /** Ответ моста → реплики в переписке. */
  const applyReply = (reply: ChatReply) => {
    chatStore.setMessages(appendAssistant(chatStore.getMessages(), reply))
    setPending(reply.pending ?? undefined)
  }

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    // Реплика владельца попадает в переписку сразу — не дожидаясь моста.
    chatStore.setMessages(appendMessage(chatStore.getMessages(), { role: 'user', text }))
    setBusy(true)
    try {
      applyReply(await api.chatSend(text))
    } catch (err) {
      chatStore.setMessages(
        appendMessage(chatStore.getMessages(), {
          role: 'system',
          text: `чат недоступен: ${(err as Error).message}`
        })
      )
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (approved: boolean) => {
    setBusy(true)
    try {
      applyReply(await api.chatConfirm(approved))
    } catch (err) {
      chatStore.setMessages(
        appendMessage(chatStore.getMessages(), {
          role: 'system',
          text: `чат недоступен: ${(err as Error).message}`
        })
      )
    } finally {
      setBusy(false)
      setPending(undefined)
    }
  }

  /** «Очистить»: стираем переписку в окне и просим мост стереть файл. */
  const clear = () => {
    chatStore.reset()
    // приветствие остаётся: это приветствие окна, а не реплика истории
    chatStore.setMessages(restoreMessages([]))
    setPending(undefined)
    void api.chatReset()
  }

  return (
    <section className="chat">
      <div className="row head">
        <h2>Управляющий</h2>
        <button className="ghost" onClick={clear}>
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
              <details className="calls">
                <summary>{callsSummary(message.calls)}</summary>
                {message.calls.map((call, callIndex) => {
                  const details = callDetails(call)
                  return (
                    <div key={callIndex} className="call">
                      <span className="chip info">{details.tool}</span>
                      <pre className="args">{details.args}</pre>
                      <pre className="out">{details.result}</pre>
                    </div>
                  )
                })}
              </details>
            )}
          </div>
        ))}
        {loadingHistory && <div className="bubble system muted">переписка восстанавливается…</div>}
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
