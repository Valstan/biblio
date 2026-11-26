import { describe, expect, it } from 'vitest'
import type { PromptRecord, PromptsSnapshot } from '../src/shared/types'
import {
  archiveActionLabel,
  archiveConfirmText,
  autoEligible,
  dispatchConfirmText,
  filterRows,
  markConfirmText,
  promptRows,
  storeView,
  targetsFor,
  unarchiveConfirmText,
  validateDraft,
  viewLabel,
  visibleRows
} from '../src/renderer/src/tabs/prompts-view'
import { TABS } from '../src/renderer/src/tabs/tabs'

/**
 * Вкладка «Хранилище промтов» в окне «МиниГена» (заказ владельца от 24.11,
 * раздел КАРМАНА «MiniGen»).
 *
 * Проверяем то, что владелец увидит: вкладка **есть** в окне, промт и план
 * различаются, неизвестные поля показываются как «—», состояние хранилища
 * честное, поиск работает, а отправка идёт только в область своего проекта
 * и только с подтверждением.
 */

function prompt(over: Partial<PromptRecord> = {}): PromptRecord {
  return {
    id: 'p-1',
    project: 'biblio',
    title: 'Разбор заявки',
    body: 'Возьми заявку из раздела biblio и разбери её по пунктам.',
    kind: 'prompt',
    archived: false,
    createdAt: '2026-11-24T10:00:00Z',
    source: 'local',
    ...over
  }
}

function snapshot(over: Partial<PromptsSnapshot> = {}): PromptsSnapshot {
  return {
    store: {
      kind: 'local',
      label: 'в базе МиниГена',
      location: '',
      ok: true,
      reason: '',
      sections: ['biblio', 'MiniGen']
    },
    prompts: [prompt()],
    targets: [
      { slug: 'p-biblio', title: 'biblio', section: 'biblio', busy: false },
      { slug: 'p-minigen', title: 'MiniGen', section: 'MiniGen', busy: true }
    ],
    auto_dispatch: false,
    ...over
  }
}

describe('вкладка «Хранилище промтов»', () => {
  it('вкладка есть в окне', () => {
    expect(TABS.map((item) => item.label)).toContain('Промты')
    expect(TABS.map((item) => item.id)).toContain('prompts')
  })

  it('промт и план различаются, а поля записи видны', () => {
    const rows = promptRows(
      snapshot({
        prompts: [
          prompt(),
          prompt({ id: 'p-2', kind: 'plan', title: 'План недели' })
        ]
      })
    )
    expect(rows).toHaveLength(2)
    expect(rows[0].kind).toBe('промт')
    expect(rows[1].kind).toBe('план')
    expect(rows[0].project).toBe('biblio')
    expect(rows[0].title).toBe('Разбор заявки')
    expect(rows[0].preview).toContain('разбери её')
    expect(rows[0].missing).toEqual([])
  })

  it('пустые поля — «—», а не выдумка', () => {
    const rows = promptRows(
      snapshot({
        prompts: [prompt({ project: '', title: '', body: '', createdAt: '' })]
      })
    )
    expect(rows[0].title).toBe('—')
    expect(rows[0].project).toBe('—')
    expect(rows[0].preview).toBe('—')
    expect(rows[0].created).toBe('—')
    expect(rows[0].missing).toEqual(['название', 'раздел', 'текст'])
  })

  it('пустой снимок — пустой список, а не ошибка', () => {
    expect(promptRows(snapshot({ prompts: [] }))).toEqual([])
    expect(promptRows(null)).toEqual([])
  })

  it('состояние хранилища: работает, недоступно и неизвестно — разные', () => {
    const alive = storeView(snapshot().store)
    expect(alive.tone).toBe('ok')
    expect(alive.label).toBe('в базе МиниГена')
    expect(alive.detail).toContain('в базе «МиниГена»')

    const closed = storeView({
      kind: 'karman',
      label: 'на внешнем ресурсе',
      location: 'https://example.invalid/karman',
      ok: false,
      reason: 'нет связи с внешним ресурсом',
      sections: []
    })
    expect(closed.tone).toBe('warn')
    expect(closed.label).toBe('хранилище недоступно')
    expect(closed.detail).toContain('нет связи')

    const localDown = storeView({
      kind: 'local',
      label: 'в базе МиниГена',
      location: '',
      ok: false,
      reason: 'база не читается',
      sections: []
    })
    expect(localDown.tone).toBe('bad')
    expect(localDown.detail).toContain('база не читается')

    const unknown = storeView(null)
    expect(unknown.tone).toBe('bad')
    expect(unknown.label).toBe('хранилище не спросили')
    expect(unknown.detail).toContain('мост не ответил')
  })

  it('поиск: по названию, по тексту и пустой запрос', () => {
    const rows = promptRows(
      snapshot({
        prompts: [
          prompt(),
          prompt({ id: 'p-2', title: 'План недели', body: 'Собрать заметки' })
        ]
      })
    )
    expect(filterRows(rows, '')).toHaveLength(2)
    expect(filterRows(rows, '   ')).toHaveLength(2)
    expect(filterRows(rows, 'ПЛАН')).toHaveLength(1)
    expect(filterRows(rows, 'заметки')[0].title).toBe('План недели')
    expect(filterRows(rows, 'biblio')).toHaveLength(2)
    expect(filterRows(rows, 'нетакого')).toHaveLength(0)
  })

  it('область — только та, чей раздел совпадает с разделом промта', () => {
    const row = promptRows(snapshot())[0]
    const targets = snapshot().targets
    const mine = targetsFor(row, targets)
    expect(mine.map((item) => item.slug)).toEqual(['p-biblio'])

    // регистр не важен
    const upper = promptRows(snapshot({ prompts: [prompt({ project: 'BIBLIO' })] }))[0]
    expect(targetsFor(upper, targets).map((item) => item.slug)).toEqual(['p-biblio'])

    // чужой раздел — пусто: задание уехало бы не туда
    const other = promptRows(snapshot({ prompts: [prompt({ project: 'нет-такого' })] }))[0]
    expect(targetsFor(other, targets)).toEqual([])
  })

  it('область подходит и по своему слагу (живой случай: у «minigen» раздел в конфиге другой)', () => {
    const row = promptRows(snapshot({ prompts: [prompt({ project: 'MiniGen' })] }))[0]
    const minigen = { slug: 'minigen', title: 'МиниГен', section: 'biblio', busy: false }
    expect(targetsFor(row, [minigen]).map((item) => item.slug)).toEqual(['minigen'])
  })

  it('автораздача — только для промтов, помеченных владельцем', () => {
    const rows = promptRows(
      snapshot({ prompts: [prompt(), prompt({ id: 'p-2', auto: true })] })
    )
    expect(autoEligible(rows[0])).toBe(false)
    expect(autoEligible(rows[1])).toBe(true)
  })

  it('опасные действия называют область и последствия и просят подтверждения', () => {
    const row = promptRows(snapshot())[0]
    const target = snapshot().targets[0]

    const dispatch = dispatchConfirmText(row, target)
    expect(dispatch).toContain('Разбор заявки')
    expect(dispatch).toContain('p-biblio')
    expect(dispatch).toContain('biblio')
    expect(dispatch).toContain('очки аккаунта')
    expect(dispatch).toContain('Продолжить?')

    const archive = archiveConfirmText(row)
    expect(archive).toContain('Разбор заявки')
    expect(archive).toContain('Архив')
    expect(archive).toContain('Продолжить?')

    const mark = markConfirmText(row, true)
    expect(mark).toContain('Разбор заявки')
    expect(mark).toContain('раздавать автоматически')
    expect(mark).toContain('Продолжить?')

    const unmark = markConfirmText(row, false)
    expect(unmark).toContain('Продолжить?')
  })

  it('черновик: нужен раздел, заголовок и текст', () => {
    const empty = validateDraft({ project: '', title: '', body: '' })
    expect(empty).toHaveLength(3)
    expect(empty[0]).toContain('раздел')
    expect(empty[1]).toContain('заголовок')
    expect(empty[2]).toContain('текст')

    expect(validateDraft({ project: 'biblio', title: 'Заявка', body: 'Текст' })).toEqual([])
    expect(validateDraft({ project: 'biblio project', title: 'Заявка', body: 'Текст' })[0]).toContain(
      'раздел'
    )
  })

  it('архив виден отдельно, и промт из него можно вернуть', () => {
    const rows = promptRows(
      snapshot({
        prompts: [prompt(), prompt({ id: 'p-2', title: 'Сделанный', archived: true })]
      })
    )
    expect(visibleRows(rows, 'live').map((row) => row.id)).toEqual(['p-1'])
    expect(visibleRows(rows, 'archived').map((row) => row.id)).toEqual(['p-2'])
    expect(visibleRows(rows, 'all')).toHaveLength(2)

    expect(archiveActionLabel(rows[0])).toBe('В архив')
    expect(archiveActionLabel(rows[1])).toBe('Вернуть из архива')

    const back = unarchiveConfirmText(rows[1])
    expect(back).toContain('Сделанный')
    expect(back).toContain('снова попадёт в выдачу')
    expect(back).toContain('Продолжить?')
  })

  it('вид выдачи назван прямо — окно не гадает, что показывает', () => {
    expect(viewLabel('live')).toBe('живые')
    expect(viewLabel('archived')).toBe('архив')
    expect(viewLabel('all')).toBe('живые и архив')
  })
})
