import { useCallback, useEffect, useState } from 'react'
import type { CloneStatus, InstallGuide } from '@shared/types'
import { api } from '../api'
import { exchangeView } from './copy-view'
import { COPY_RULES, geneSteps, guideSections, plainLine, sendChecklist } from './cuttings-view'

/**
 * Вкладка «Черенки» — **размножение** «МиниГена»: поставить копию другому
 * человеку и передать ему наработки (гены).
 *
 * ⚠️ Заказ владельца (26.11): «сделай в интерфейсе МиниГена ссылку на
 * инструкцию установки минигена… А то я сейчас порылся и не могу понять что
 * мне отправить знакомому чтобы он у себя установил миниген тоже».
 *
 * Что показывает:
 * - **что отправить знакомому** — готовый список: инструкция, адрес репозитория,
 *   установщик, требования к машине и что знакомому делать у себя;
 * - **саму инструкцию** — читается прямо в окне, её можно скопировать в буфер
 *   (вставить в письмо) или открыть файлом;
 * - **как передать наработки** — публикация гена в пул, выгрузка папки, внесение
 *   чужой папки, чужие наработки;
 * - **что важно помнить** про вторую копию (отдельная машина, своя родословная,
 *   свой вход в GitHub).
 *
 * ⚠️ Честность: если инструкции на машине нет — вкладка говорит об этом прямо
 * и говорит, что делать (где файл должен лежать), а не показывает пустоту.
 *
 * Данные спрашиваются у моста по таймеру: окно открывается раньше моста, и
 * разовый запрос в этот момент падает.
 */
export default function CuttingsTab({ onOpenGenes }: { onOpenGenes: () => void }) {
  const [guide, setGuide] = useState<InstallGuide | null>(null)
  const [guideError, setGuideError] = useState('')
  const [cloneStatus, setCloneStatus] = useState<CloneStatus | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [showText, setShowText] = useState(false)
  const [title, setTitle] = useState('')
  const [importPath, setImportPath] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await api.installGuide()
      setGuide(data)
      setGuideError('')
    } catch (err) {
      setGuideError(
        `инструкцию не принесли: ${String((err as Error).message || err)}`
      )
    }
    try {
      setCloneStatus(await api.cloneStatus())
    } catch {
      /* состояние обмена — не критично: шаги покажутся и без него */
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => window.clearInterval(timer)
  }, [load])

  /** Открыть файл инструкции (программа по умолчанию). */
  const openGuide = async () => {
    if (!guide?.path) return
    await window.minigen.openExternal(guide.path)
  }

  /** Скопировать инструкцию в буфер — чтобы вставить в письмо знакомому. */
  const copyGuide = async () => {
    if (!guide?.text) return
    const ok = await window.minigen.copyText(guide.text)
    setNote(
      ok
        ? 'инструкция в буфере обмена — вставьте её в письмо'
        : 'буфер обмена не принял текст'
    )
  }

  /** Открыть папку с установщиком (проводником). */
  const openInstaller = async () => {
    if (!guide?.installer_dir) return
    const res = await api.openFolder(guide.installer_dir)
    if (res?.ok === false) setNote(res.error || 'мост не открыл папку')
  }

  /** Опубликовать текущую правку в пул наработок. */
  const publish = async () => {
    const name = title.trim()
    if (!name) {
      setNote('впишите, что именно публикуете — это увидят другие копии')
      return
    }
    setBusy(true)
    try {
      const res = await api.genesPublish({ title: name })
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не опубликовал наработку')
        return
      }
      setNote(
        res.gene
          ? `наработка опубликована: ${res.gene}` +
            (res.pr ? ` (PR ${res.pr})` : '') +
            (res.local_only ? ' — локально, в пул не ушла' : '')
          : res.why || 'наработка опубликована'
      )
      setTitle('')
    } catch (err) {
      setNote(`публикация не вышла: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Выгрузить папку наработок — её можно отнести соседней копии. */
  const exportWorks = async () => {
    setBusy(true)
    try {
      const res = await api.genesExport()
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не выгрузил папку наработок')
        return
      }
      setNote(
        res.path
          ? `папка наработок выгружена: ${res.path}`
          : 'папка наработок выгружена (путь не пришёл)'
      )
    } catch (err) {
      setNote(`выгрузка не вышла: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  /** Внести папку наработок, принесённую соседней копией. */
  const importWorks = async () => {
    const path = importPath.trim()
    if (!path) {
      setNote('укажите папку с наработками другой копии')
      return
    }
    setBusy(true)
    try {
      const res = await api.genesImport(path)
      if (res.ok === false) {
        setNote(res.error || res.why || 'мост не внёс папку наработок')
        return
      }
      setNote(
        `внесено наработок: ${res.added?.length ?? res.files ?? 0}` +
          (res.skipped?.length ? `, пропущено: ${res.skipped.length}` : '')
      )
    } catch (err) {
      setNote(`внесение не вышло: ${String((err as Error).message || err)}`)
    } finally {
      setBusy(false)
    }
  }

  const exchange = exchangeView(cloneStatus)
  const steps = geneSteps(exchange.tone === 'ok', exchange.detail)
  const checklist = sendChecklist(guide)
  const sections = showText && guide?.ok ? guideSections(guide.text) : []

  return (
    <section>
      <div className="row head">
        <h2>Черенкование «МиниГена»</h2>
        <span className="muted small">
          Черенок — это отросток от своего корня: копия программы на другой
          машине, с общей родословной. Здесь — как её посадить и как передать
          наработки.
        </span>
      </div>

      {note && <div className="banner">{note}</div>}
      {guideError && <div className="banner bad">{guideError}</div>}

      <div className="card">
        <h3>Что отправить знакомому</h3>
        {guide && !guide.ok && (
          <div className="banner bad">
            {guide.error || 'инструкция не найдена'}
            {guide.why ? ` — ${guide.why}` : ''}
          </div>
        )}
        <ol className="muted small" style={{ paddingLeft: 18 }}>
          {checklist.map((item) => (
            <li key={item} style={{ marginBottom: 4 }}>
              {plainLine(item)}
            </li>
          ))}
        </ol>
        <div className="row">
          <button
            className="primary"
            onClick={() => void copyGuide()}
            disabled={!guide?.ok}
          >
            Скопировать инструкцию
          </button>
          <button className="ghost" onClick={() => void openGuide()} disabled={!guide?.ok}>
            Открыть файл инструкции
          </button>
          <button
            className="ghost"
            onClick={() => void openInstaller()}
            disabled={!guide?.installer_dir}
          >
            Открыть папку с установщиком
          </button>
          <button className="ghost" onClick={() => setShowText((value) => !value)} disabled={!guide?.ok}>
            {showText ? 'Скрыть инструкцию' : 'Показать инструкцию'}
          </button>
        </div>
        {guide?.ok && (
          <p className="muted small">
            Файл: {guide.path} (взят из: {guide.source})
          </p>
        )}
        {sections.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {sections.map((section, index) => (
              <div key={`${section.heading}-${index}`} style={{ marginBottom: 12 }}>
                {section.heading && <h4 style={{ margin: '0 0 4px' }}>{section.heading}</h4>}
                <pre className="muted small" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                  {section.lines.map(plainLine).join('\n')}
                </pre>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Передать наработки (гены)</h3>
        {steps.map((step) => (
          <div key={step.key} style={{ marginBottom: 12 }}>
            <p style={{ margin: '0 0 4px' }}>
              <strong>{step.title}</strong>
            </p>
            <p className="muted small" style={{ margin: '0 0 6px' }}>
              {step.tone === 'warn' ? '' : step.detail}
            </p>
            {step.tone === 'warn' && <div className="banner warn">{step.detail}</div>}
            {step.key === 'publish' && (
              <div className="row">
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="что публикуете (видят другие копии)"
                />
                <button className="ghost" onClick={() => void publish()} disabled={busy}>
                  Опубликовать
                </button>
              </div>
            )}
            {step.key === 'export' && (
              <div className="row">
                <button className="ghost" onClick={() => void exportWorks()} disabled={busy}>
                  Выгрузить папку
                </button>
              </div>
            )}
            {step.key === 'import' && (
              <div className="row">
                <input
                  value={importPath}
                  onChange={(event) => setImportPath(event.target.value)}
                  placeholder="путь к папке наработок другой копии"
                />
                <button className="ghost" onClick={() => void importWorks()} disabled={busy}>
                  Внести папку
                </button>
              </div>
            )}
            {step.key === 'pool' && (
              <div className="row">
                <button className="ghost" onClick={onOpenGenes}>
                  Открыть вкладку «Гены»
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Что важно помнить про вторую копию</h3>
        <ul className="muted small" style={{ paddingLeft: 18 }}>
          {COPY_RULES.map((rule) => (
            <li key={rule} style={{ marginBottom: 4 }}>
              {plainLine(rule)}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
