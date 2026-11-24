import { describe, expect, it } from 'vitest'
import { describeUpdate } from '../src/renderer/src/tabs/update-view'
import type { ProgramVersion, UpdateState } from '../src/shared/types'

/**
 * Обновление программы должно быть наглядным (заказ владельца 24.11:
 * «как мне запустить процесс обновления из графического интерфейса
 * программы? Сделай процесс обновления наглядным»).
 *
 * Главное правило: раскатка меняет файлы на диске, а открытое окно работает
 * на старом коде. Окно обязано это замечать и говорить, что нужен перезапуск
 * (метка сборки — коммит, вшитый в бандл при сборке).
 */

function version(commit: string): ProgramVersion {
  return {
    commit,
    installed_at: 1_700_000_000_000,
    source: 'C:/repo',
    installed: true,
    running_from: 'C:/МиниГен'
  }
}

function state(over: Partial<UpdateState> = {}): UpdateState {
  return {
    running: false,
    steps: [],
    installed: version('aaaaaaa'),
    available: { commit: 'aaaaaaa', behind: 0, repo: 'C:/repo', remote: 'origin' },
    last: {},
    repo: 'C:/repo',
    ...over
  }
}

describe('состояние обновления', () => {
  it('совпадающие метки сборки и установленной версии ⇒ всё актуально', () => {
    const verdict = describeUpdate(version('aaaaaaa'), 'aaaaaaa', state())
    expect(verdict.tone).toBe('ok')
    expect(verdict.needRestart).toBe(false)
    expect(verdict.text).toContain('актуальна')
  })

  it('окно старше файлов на диске ⇒ нужен перезапуск', () => {
    const verdict = describeUpdate(version('bbbbbbb'), 'aaaaaaa', state())
    expect(verdict.needRestart).toBe(true)
    expect(verdict.tone).toBe('warn')
    expect(verdict.text).toContain('перезапусти')
  })

  it('есть новые коммиты в репозитории ⇒ предлагает обновление', () => {
    const verdict = describeUpdate(
      version('aaaaaaa'),
      'aaaaaaa',
      state({ available: { commit: 'ccccccc', behind: 3, repo: 'C:/repo', remote: 'origin' } })
    )
    expect(verdict.tone).toBe('warn')
    expect(verdict.text).toContain('ccccccc')
  })

  it('идущее обновление важнее всего остального', () => {
    const verdict = describeUpdate(version('aaaaaaa'), 'bbbbbbb', state({ running: true }))
    expect(verdict.text).toContain('Идёт обновление')
    expect(verdict.needRestart).toBe(false)
  })

  it('неизвестная версия — это плохо, а не «всё хорошо»', () => {
    const verdict = describeUpdate(null, '', state())
    expect(verdict.tone).toBe('bad')
  })
})
