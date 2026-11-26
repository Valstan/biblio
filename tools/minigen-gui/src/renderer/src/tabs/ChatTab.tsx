import { useEffect, useRef, useState, useSyncExternalStore, type DragEvent } from 'react'
import type { ChatAttachment, ChatReply, ModelCatalog, ModelEntry } from '@shared/types'
import { api } from '../api'
import * as chatStore from './chat-store'
import {
  appendAssistant,
  appendMessage,
  callDetails,
  callsSummary,
  pendingArgs,
  restoreMessages
} from './chat-view'
import {
  attachmentLabel,
  collectFiles,
  detectKind,
  mergeAttachments,
  visibleAttachments
} from './attachments-view'

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
 *
 * Оператор может **прикладывать файлы** к чату (промт #90): кнопкой рядом
 * со строкой ввода или перетаскиванием в любое место окна чата. Окно знает
 * только имя, вид и размер файла — **содержимое читает мост** и кладёт его
 * в контекст хода. Приложенный файл уходит в модель (в вендор) — об этом
 * окно предупреждает честно, а не молча.
 */
export default function ChatTab() {
  // Переписка — из стора модуля: размонтирование вкладки её не стирает.
  const messages = useSyncExternalStore(chatStore.subscribe, chatStore.getMessages)
  // Ожидающее подтверждение и занятость — **тоже** в сторе (промт #129):
  // уход на другую вкладку не должен молча съедать вопрос Управляющего
  // владельцу («подтвердите действие») и не должен оставлять возможность
  // слать вторую реплику, пока первая ещё думается.
  const pending = useSyncExternalStore(chatStore.subscribe, chatStore.getPending)
  const busy = useSyncExternalStore(chatStore.subscribe, chatStore.getBusy)
  const [input, setInput] = useState('')
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null)
  const [model, setModel] = useState('')
  const [context, setContext] = useState(0)
  const [effort, setEffort] = useState('')
  const [modelNote, setModelNote] = useState('')
  // Пока мост отвечает на историю — говорим честно, а не показываем пустоту.
  const [loadingHistory, setLoadingHistory] = useState(true)
  // Файлы, приложенные оператором и ждущие отправки (промт #90).
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  // Честная пометка оператору: «путь не получили» и подобное.
  const [attachNote, setAttachNote] = useState('')
  // Подсветка зоны приёма файлов, пока файл перетаскивают.
  const [dragOver, setDragOver] = useState(false)
  // Глубина вложенности при перетаскивании: без неё подсветка мигает над
  // «детьми» зоны (счётчик растёт и падает на каждом элементе внутри).
  const dragDepth = useRef(0)
  // Сеттеры стора — те же имена, чтобы код хода не менялся по сути.
  const setBusy = chatStore.setBusy
  const setPending = chatStore.setPending

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

  // Окно не должно уезжать на перетащенный файл (Chromium так умеет): пока
  // открыта вкладка чата, перехватываем drop на уровне документа. Сама зона
  // приёма — секция `.chat` (обработчик выше).
  useEffect(() => {
    const stop = (event: Event) => event.preventDefault()
    document.addEventListener('dragover', stop)
    document.addEventListener('drop', stop)
    return () => {
      document.removeEventListener('dragover', stop)
      document.removeEventListener('drop', stop)
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

  /**
   * Кнопка «Прикрепить файл»: диалог выбора открывает **главный процесс**,
   * окно получает настоящие пути и само на диск не ходит.
   */
  const attachByDialog = async () => {
    try {
      const picked = (await window.minigen.pickFiles()) ?? []
      // Пустой список — оператор отменил выбор: молчим, это не ошибка.
      if (picked.length === 0) return
      const added: ChatAttachment[] = []
      const blind: string[] = []
      for (const item of picked) {
        if (!item?.path) {
          blind.push(item?.name || 'файл')
          continue
        }
        added.push({
          name: item.name,
          path: item.path,
          size: item.size ?? 0,
          kind: detectKind(item.name)
        })
      }
      if (added.length > 0) {
        setAttachments((current) => mergeAttachments(current, added))
      }
      setAttachNote(
        blind.length > 0
          ? `путь файла получить не удалось — выберите файл кнопкой ещё раз (${blind.join(', ')})`
          : ''
      )
    } catch (err) {
      setAttachNote(`файлы не выбраны: ${(err as Error).message}`)
    }
  }

  /**
   * Перетащенные файлы → вложения.
   *
   * ⚠️ Electron 32 не отдаёт путь файла сам: настоящий путь берёт главный
   * процесс (`getPathForFile`). Если пути нет — говорим оператору честно и
   * предлагаем кнопку, а не игнорируем файл молча.
   */
  const attachDropped = (files: File[]) => {
    if (files.length === 0) return
    const added: ChatAttachment[] = []
    const blind: string[] = []
    for (const file of files) {
      const path = window.minigen.getPathForFile(file)
      if (!path) {
        blind.push(file.name)
        continue
      }
      added.push({
        name: file.name,
        path,
        size: file.size,
        kind: detectKind(file.name)
      })
    }
    if (added.length > 0) {
      setAttachments((current) => mergeAttachments(current, added))
      setAttachNote('')
    }
    if (blind.length > 0) {
      setAttachNote(blindNote(blind))
    }
  }

  /** Снять вложение (чип с «×»). */
  const removeAttachment = (path: string | undefined) => {
    setAttachments((current) => current.filter((item) => item.path !== path))
  }

  /** Зона приёма файлов: подсветка и «отпустите файл». */
  const onDragEnter = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    dragDepth.current += 1
    setDragOver(true)
  }

  const onDragOver = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    setDragOver(true)
  }

  const onDragLeave = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragOver(false)
  }

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    dragDepth.current = 0
    setDragOver(false)
    attachDropped(collectFiles(event.dataTransfer?.files ?? null))
  }

  const send = async () => {
    const text = input.trim()
    const files = attachments
    // Отправить можно и без текста: задачу сформулирует мост (промт #90).
    // ⚠️ Пока переписка восстанавливается — отправлять нельзя: ответ моста
    // вернул бы историю и **затёр** бы реплику, которую владелец уже отправил.
    if ((!text && files.length === 0) || busy || loadingHistory) return
    setInput('')
    setAttachments([])
    // Реплика владельца попадает в переписку сразу — не дожидаясь моста.
    // Вложения — вместе с ней, чтобы в логе сразу было видно, что приложено.
    chatStore.setMessages(
      appendMessage(chatStore.getMessages(), {
        role: 'user',
        text,
        attachments: files.length > 0 ? visibleAttachments(files) : undefined
      })
    )
    setBusy(true)
    try {
      applyReply(await api.chatSend(text, files.length > 0 ? files : undefined))
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
    <section
      className={`chat${dragOver ? ' drag-over' : ''}`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {dragOver && <div className="drop-hint">Отпустите файл — прикреплю к чату</div>}
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
            {message.attachments && message.attachments.length > 0 && (
              <div className="attachments">
                {message.attachments.map((item, itemIndex) => (
                  <span key={itemIndex} className="chip file">
                    {attachmentLabel(item)}
                  </span>
                ))}
              </div>
            )}
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
          Требуется подтверждение: <b>{pending.tool}</b> — {pending.reason}
          {/* ⚠️ Аргументы — в свёртке (промт #129): владелец видит, **что**
              требует подтверждения, а технический JSON открывает по клику. */}
          <details className="pending-args">
            <summary>Что именно выполняется</summary>
            <pre className="args">{pendingArgs(pending)}</pre>
          </details>
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

      {attachments.length > 0 && (
        <div className="chips attach-list">
          {attachments.map((item, index) => (
            <span key={item.path || index} className="chip file">
              {attachmentLabel(item)}
              <button
                className="x"
                title="Снять вложение"
                aria-label="Снять вложение"
                onClick={() => removeAttachment(item.path)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="row chat-input">
        <button
          className="ghost attach-btn"
          onClick={() => void attachByDialog()}
          title="Прикрепить файл к чату"
        >
          Прикрепить файл
        </button>
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
        <button
          className="primary"
          onClick={() => void send()}
          disabled={busy || (!input.trim() && attachments.length === 0)}
        >
          Отправить
        </button>
      </div>
      <div className="muted small attach-hint">
        Файлы, приложенные к чату, уходят в модель (в вендор) — вместе с содержимым.
      </div>
      {attachNote && <div className="attach-note">{attachNote}</div>}
    </section>
  )
}

/**
 * Честная пометка оператору: путь файла получить не удалось (Electron его не
 * дал) — предлагаем кнопку, а не молча игнорируем файл.
 */
function blindNote(names: string[]): string {
  return `путь файла получить не удалось — прикрепите файл кнопкой (${names.join(', ')})`
}
