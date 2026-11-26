import { describe, expect, it } from 'vitest'
import type {
  CloneStatus,
  CopyFirstRunSnapshot,
  CopyOffer,
  GeneSuggestions
} from '../src/shared/types'
import {
  adoptConfirmText,
  answerConfirmText,
  exchangeView,
  firstRunView,
  localOfferView,
  modeConfirmText,
  modeRows,
  modeView,
  newWorksPhrase,
  newWorksView,
  suggestionRows
} from '../src/renderer/src/tabs/copy-view'

/**
 * Режимы копии и первый запуск (заказ владельца 25.11, промт #131).
 *
 * Проверяем то, что владелец увидит: неизвестное состояние называется
 * неизвестным (а не «работает»), локальный режим честно говорит, что обмена
 * с копиями нет, вопрос первого запуска не выдумывается, а подтверждения
 * называют PR, репозиторий и обмен.
 */

function cloneStatus(over: Partial<CloneStatus> = {}): CloneStatus {
  return {
    instance_id: 'mg-693f8fdd',
    family: 'minigen',
    generation: 2,
    clone_dir: 'C:/ProgramData/MiniGen/clone',
    clone_exists: true,
    pool_dir: 'C:/ProgramData/MiniGen/genepool',
    pool_exists: true,
    copy_repo: 'Valstan/minigen-693f8fdd',
    upstream: 'Valstan/biblio',
    branch: 'master',
    dirty: false,
    mode: 'shared',
    mode_label: 'Папка в общем репозитории',
    exchange: {
      available: true,
      transport: 'пул наработок',
      pool: 'C:/ProgramData/MiniGen/genepool',
      pool_exists: true,
      why: 'обмен идёт через пул наработок'
    },
    local_repo: { path: 'C:/ProgramData/MiniGen/local-repo', exists: true, commit: 'abc1234', branch: 'master' },
    first_run: {
      asked: true,
      agreed: true,
      mode: 'shared',
      answered_at: Date.UTC(2026, 10, 24, 9, 45, 53),
      instance: 'mg-693f8fdd'
    },
    ...over
  }
}

function offer(over: Partial<CopyOffer> = {}): CopyOffer {
  return {
    title: 'Регистрировать папку копии в репозитории программы?',
    repository: 'Valstan/biblio',
    folder: 'copies/mg-693f8fdd',
    gives: [
      'правки программы и PR в общий репозиторий — их увидит владелец',
      'обмен наработками с другими копиями (пул генов)'
    ],
    if_refuse: [
      'программа работает как обычно: чат, области, демоны, правки',
      'PR в общий репозиторий не открываются, обмен с другими копиями недоступен'
    ],
    who: 'репозиторий принадлежит владельцу; согласие даётся на этой машине',
    modes: [
      { id: 'own', label: 'Свой репозиторий на GitHub', hint: 'свой репозиторий' },
      { id: 'shared', label: 'Папка в общем репозитории', hint: 'общий репозиторий' },
      { id: 'local', label: 'Только локально, без сети', hint: 'локально, без сети' }
    ],
    ...over
  }
}

function firstRun(over: Partial<CopyFirstRunSnapshot> = {}): CopyFirstRunSnapshot {
  return {
    first_run: {
      asked: false,
      agreed: false,
      mode: '',
      answered_at: 0,
      instance: 'mg-693f8fdd'
    },
    offer: offer(),
    mode: 'local',
    mode_label: 'Только локально, без сети',
    exchange: {
      available: false,
      transport: 'нет',
      pool: '',
      pool_exists: false,
      why: 'локальный режим: сети нет — обмен с другими копиями недоступен'
    },
    ...over
  }
}

function suggestions(over: Partial<GeneSuggestions> = {}): GeneSuggestions {
  return {
    suggestions: [
      {
        id: 'g-1',
        title: 'Разбор заявки',
        summary: 'Модуль разбора заявок',
        author: 'mg-abc123',
        created_at: Date.UTC(2026, 10, 24, 9, 45, 53),
        files: ['tools/minigen-gui/src/renderer/src/tabs/AgentsTab.tsx'],
        score: 0.9,
        why: 'похоже на ваш модуль агентов',
        fits: true
      },
      {
        id: 'g-2',
        title: '',
        summary: '',
        author: '',
        created_at: 0,
        files: [],
        score: 0.2,
        why: '',
        fits: false
      }
    ],
    fits: 1,
    my_files: ['tools/minigen-gui/src/renderer/src/tabs/AgentsTab.tsx'],
    transport: 'пул наработок',
    available: true,
    why: '',
    ...over
  }
}

describe('режимы копии и первый запуск', () => {
  it('пустой статус — честное «неизвестно», а не «работает»', () => {
    const mode = modeView(null)
    expect(mode.tone).toBe('bad')
    expect(mode.detail).toContain('мост не ответил')

    const exchange = exchangeView(null)
    expect(exchange.tone).toBe('bad')
    expect(exchange.label).toBe('состояние неизвестно')

    const rows = modeRows(null)
    expect(rows).toHaveLength(4)
    expect(rows.every((row) => row.tone === 'bad')).toBe(true)
    expect(rows.map((row) => row.label)).toEqual([
      'Режим',
      'Обмен наработками',
      'Локальный репозиторий',
      'Родословная'
    ])
    expect(rows[0].detail).toContain('мост не ответил')
  })

  it('известный режим — тон «ок» и подпись режима', () => {
    const view = modeView(cloneStatus())
    expect(view.tone).toBe('ok')
    expect(view.label).toBe('Папка в общем репозитории')
    expect(view.detail).toContain('общий репозиторий')
  })

  it('обмен доступен — тон «ок», недоступен — честное «внимание»', () => {
    const alive = exchangeView(cloneStatus())
    expect(alive.tone).toBe('ok')
    expect(alive.label).toBe('обмен доступен')
    expect(alive.detail).toContain('пул наработок')

    // ⚠️ Локальный режим: сети нет — обмен с другими копиями недоступен.
    const local = exchangeView(
      cloneStatus({
        mode: 'local',
        mode_label: 'Только локально, без сети',
        exchange: {
          available: false,
          transport: 'нет',
          pool: '',
          pool_exists: false,
          why: 'локальный режим: сети нет — обмен с другими копиями недоступен'
        }
      })
    )
    expect(local.tone).toBe('warn')
    expect(local.label).toBe('обмен недоступен')
    expect(local.detail).toContain('сети нет')
    expect(local.detail).toContain('обмен')
  })

  it('строки состояния показывают режим, обмен, локальный репозиторий и родословную', () => {
    const rows = modeRows(cloneStatus())
    expect(rows[0].detail).toBe('Папка в общем репозитории')
    expect(rows[1].detail).toContain('обмен доступен')
    expect(rows[2].detail).toContain('local-repo')
    expect(rows[3].detail).toBe('mg-693f8fdd')
  })

  it('первый запуск: вопрос не задан — показываем сам вопрос', () => {
    const view = firstRunView(firstRun())
    expect(view.asked).toBe(false)
    expect(view.title).toContain('Регистрировать')
    expect(view.repository).toBe('Valstan/biblio')
    expect(view.folder).toBe('copies/mg-693f8fdd')
    expect(view.gives.length).toBeGreaterThan(0)
    expect(view.ifRefuse.length).toBeGreaterThan(0)
    expect(view.who).toContain('владельцу')
    expect(view.modes).toHaveLength(3)
  })

  it('первый запуск: вопрос задан — вопроса больше нет', () => {
    const view = firstRunView(firstRun({ first_run: { asked: true, agreed: true, mode: 'shared', answered_at: 1, instance: 'mg-693f8fdd' } }))
    expect(view.asked).toBe(true)
  })

  it('снимка нет — «неизвестно», а не пустой вопрос', () => {
    const view = firstRunView(null)
    expect(view.asked).toBe(false)
    expect(view.title).toBe('—')
    expect(view.repository).toBe('—')
    expect(view.folder).toBe('—')
    expect(view.who).toBe('—')
    expect(view.gives).toEqual([])
    expect(view.ifRefuse).toEqual([])
    expect(view.modes).toEqual([])
  })

  it('подсказки: подходящие — с fits, неподходящие — без, пустое — «—»', () => {
    const rows = suggestionRows(suggestions())
    expect(rows).toHaveLength(2)
    expect(rows[0].fits).toBe(true)
    expect(rows[0].gene).toBe('g-1')
    expect(rows[0].title).toBe('Разбор заявки')
    expect(rows[0].author).toBe('mg-abc123')
    expect(rows[0].why).toContain('похоже')
    expect(rows[0].files).toHaveLength(1)
    expect(rows[0].created).toContain('2026')

    expect(rows[1].fits).toBe(false)
    expect(rows[1].title).toBe('—')
    expect(rows[1].author).toBe('—')
    expect(rows[1].why).toBe('—')
    expect(rows[1].created).toBe('—')
    expect(rows[1].files).toEqual([])

    expect(suggestionRows(null)).toEqual([])
  })

  it('подтверждение внедрения: что внедряем, от кого, через ветку и PR', () => {
    const row = suggestionRows(suggestions())[0]
    const text = adoptConfirmText(row)
    expect(text).toContain('Разбор заявки')
    expect(text).toContain('mg-abc123')
    expect(text).toContain('PR')
    expect(text).toContain('репозиторий')
    expect(text).toContain('Решение за вами')
    expect(text).toContain('Продолжить?')
  })

  it('подтверждение смены режима: называет режим и что изменится', () => {
    const own = modeConfirmText(offer().modes[0])
    expect(own).toContain('Свой репозиторий на GitHub')
    expect(own).toContain('создастся репозиторий в вашем GitHub-аккаунте')

    const shared = modeConfirmText(offer().modes[1])
    expect(shared).toContain('общий репозиторий программы')
    expect(shared).toContain('PR')

    const local = modeConfirmText(offer().modes[2])
    expect(local).toContain('локальный репозиторий')
    expect(local).toContain('PR не откроется')
    expect(local).toContain('обмен с копиями будет недоступен')
  })

  it('ответ на вопрос первого запуска: согласие и отказ называют последствия', () => {
    const yes = answerConfirmText(true, offer())
    expect(yes).toContain('Valstan/biblio')
    expect(yes).toContain('copies/mg-693f8fdd')
    expect(yes).toContain('PR')
    expect(yes).toContain('обмен наработками')

    const no = answerConfirmText(false, offer())
    expect(no).toContain('Отказаться')
    expect(no).toContain('PR')
    expect(no).toContain('обмен с другими копиями недоступен')
    expect(no).toContain('не ломает программу')
  })
})

describe('уведомление о новых наработках', () => {
  it('новые — те, которых копия ещё не видела', () => {
    const view = newWorksView(suggestions(), ['g-1'])
    expect(view.count).toBe(1)
    expect(view.genes).toEqual(['g-2'])
    expect(view.text).toContain('найдена 1 новая наработка')
  })

  it('все показанные — новые, если ничего не помним', () => {
    const view = newWorksView(suggestions(), [])
    expect(view.count).toBe(2)
    expect(view.fits).toBe(1)
    expect(view.text).toContain('найдено 2 новые наработки')
    expect(view.text).toContain('1 совпадает с вашей работой')
  })

  it('форма слова — по-русски (1 / 2-4 / 5+)', () => {
    expect(newWorksPhrase(1)).toBe('найдена 1 новая наработка')
    expect(newWorksPhrase(3)).toBe('найдено 3 новые наработки')
    expect(newWorksPhrase(5)).toBe('найдено 5 новых наработок')
    expect(newWorksPhrase(11)).toBe('найдено 11 новых наработок')
    expect(newWorksPhrase(21)).toBe('найдено 21 новых наработок')
  })

  it('виденное повторно не предлагается (шум — не сигнал)', () => {
    const view = newWorksView(suggestions(), ['g-1', 'g-2'])
    expect(view.count).toBe(0)
    expect(view.text).toBe('')
  })

  it('нет данных моста — уведомления нет, а не «найдено 0»', () => {
    expect(newWorksView(null, []).count).toBe(0)
    expect(newWorksView(null, []).text).toBe('')
  })
})

describe('локальный режим: окно предлагает выход, а не молчит', () => {
  const localStatus = (): CloneStatus =>
    cloneStatus({
      mode: 'local',
      mode_label: 'Только локально, без сети',
      exchange: {
        available: false,
        transport: 'нет',
        pool: '',
        pool_exists: false,
        why: 'локальный режим: сети нет — обмен с другими копиями недоступен'
      }
    })

  it('в локальном режиме предлагает включить режим с репозиторием', () => {
    const view = localOfferView(localStatus())
    expect(view.local).toBe(true)
    expect(view.text).toContain('недоступен')
    expect(view.text).toContain('папка в общем репозитории')
    expect(view.text).toContain('Почему сейчас нет')
  })

  it('в других режимах предлагать нечего — смена режима не поможет', () => {
    expect(localOfferView(cloneStatus({ mode: 'shared' })).local).toBe(false)
    expect(localOfferView(cloneStatus({ mode: 'own' })).text).toBe('')
  })

  it('нет ответа моста — предложения нет, а не выдуманный локальный режим', () => {
    expect(localOfferView(null).local).toBe(false)
  })
})
