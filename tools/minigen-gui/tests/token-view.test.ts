import { describe, expect, it } from 'vitest'
import type {
  CloneStatus,
  CopyTokenCheck,
  CopyTokenRequest,
  CopyTokenStatus
} from '../src/shared/types'
import {
  accessCheckText,
  issueConfirmText,
  maskToken,
  requestConfirmText,
  requestTime,
  tokenIssueView,
  tokenRequestResultText,
  tokenRequestRows,
  tokenSetConfirmText,
  tokenSetResultText,
  tokenStateView,
  tokenStatusRows
} from '../src/renderer/src/tabs/token-view'

/**
 * Токен копии (заказ владельца 25.11, промт #130).
 *
 * Проверяем то, что владелец увидит: токена нет — так и сказано «нет»,
 * подтверждение под свою папку отличается от неподтверждённого, чужая папка —
 * это отказ, заявка помечена «своя» или «чужая», а подтверждения называют
 * папку, токен и репозиторий.
 *
 * ⚠️ Значение токена в переписку и в отчёты не ходит: в заявках его нет, и
 * показывается оно один раз отдельным блоком.
 */

function status(over: Partial<CopyTokenStatus> = {}): CopyTokenStatus {
  return {
    has_token: true,
    token_id: 'a1b2c3d4e5f6',
    masked: '••••••••7KxQ',
    path: 'C:/Users/valstan/AppData/Local/minigen-gui/credentials/copy-token.txt',
    source: 'file',
    instance_id: 'mg-693f8fdd',
    folder: 'copies/mg-693f8fdd/',
    issued_at: Date.UTC(2026, 10, 25, 9, 45, 53),
    bound: true,
    reason: 'токен выдан под папку copies/mg-693f8fdd/',
    ...over
  }
}

function request(over: Partial<CopyTokenRequest> = {}): CopyTokenRequest {
  return {
    id: 'req-1',
    instance_id: 'mg-693f8fdd',
    name: 'копия на ноутбуке',
    folder: 'copies/mg-693f8fdd/',
    family: 'minigen',
    generation: 2,
    fork_commit: '29729fa',
    key_id: 'k3y9d0c1a2b3',
    signed_at: Date.UTC(2026, 10, 25, 9, 45, 53),
    signature: 'deadbeef',
    relation: 'чужая копия (отпечаток не закреплён)',
    signature_state: 'подпись предъявлена, отпечаток не закреплён',
    created: '2026-11-25T09:45:53.000Z',
    archived: false,
    ...over
  }
}

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
    mode: 'own',
    mode_label: 'Свой репозиторий на GitHub',
    exchange: {
      available: true,
      transport: 'пул наработок',
      pool: 'C:/ProgramData/MiniGen/genepool',
      pool_exists: true,
      why: 'обмен идёт через пул наработок'
    },
    local_repo: { path: 'C:/ProgramData/MiniGen/local-repo', exists: true },
    first_run: {
      asked: true,
      agreed: true,
      mode: 'own',
      answered_at: Date.UTC(2026, 10, 24, 9, 45, 53),
      instance: 'mg-693f8fdd'
    },
    ...over
  }
}

describe('токен копии: состояние и карточка «GitHub»', () => {
  it('токена нет — честно «нет», а не пусто и не «есть»', () => {
    const view = tokenStateView(status({ has_token: false, bound: false, reason: 'токена нет — запросите его у владельца' }))
    expect(view.tone).toBe('warn')
    expect(view.label).toBe('токена нет')
    expect(view.detail).toContain('запросите его у владельца')

    const rows = tokenStatusRows(status({ has_token: false, bound: false, token_id: '', path: '' }))
    const token = rows.find((row) => row.label === 'Токен')
    expect(token?.value).toBe('нет')
    const where = rows.find((row) => row.label === 'Где лежит токен')
    expect(where?.value).toBe('токена нет')
  })

  it('токен подтверждён под свою папку — тон «ок»', () => {
    const view = tokenStateView(status())
    expect(view.tone).toBe('ok')
    expect(view.label).toBe('токен подтверждён')
    expect(view.detail).toContain('copies/mg-693f8fdd/')

    const rows = tokenStatusRows(status())
    const bound = rows.find((row) => row.label === 'Привязка токена')
    expect(bound?.value).toBe('подтверждён под свою папку')
  })

  it('токен не подтверждён — об этом сказано прямо', () => {
    const view = tokenStateView(
      status({ bound: false, reason: 'токен выдан другой копии (mg-777) — своей папке он не подходит' })
    )
    expect(view.tone).toBe('bad')
    expect(view.label).toBe('токен не подтверждён')
    expect(view.detail).toContain('другой копии')

    const rows = tokenStatusRows(status({ bound: false }))
    const bound = rows.find((row) => row.label === 'Привязка токена')
    expect(bound?.value).toBe('не подтверждён')
  })

  it('мост не ответил — состояние неизвестно, а не «нет»', () => {
    const view = tokenStateView(null)
    expect(view.tone).toBe('bad')
    expect(view.detail).toContain('мост не ответил')

    const rows = tokenStatusRows(null)
    expect(rows.find((row) => row.label === 'Токен')?.value).toBe('—')
  })

  it('строки карточки «GitHub»: режим, путь, репозиторий, папка, токен', () => {
    const rows = tokenStatusRows(status(), cloneStatus())
    const byLabel = (label: string): string =>
      rows.find((row) => row.label === label)?.value ?? '—'

    expect(byLabel('Режим работы копии')).toBe('Свой репозиторий на GitHub')
    expect(byLabel('Путь до репозитория')).toContain('MiniGen/clone')
    expect(byLabel('Репозиторий копии')).toBe('Valstan/minigen-693f8fdd')
    expect(byLabel('Папка копии в общем репозитории')).toBe('copies/mg-693f8fdd/')
    expect(byLabel('Где лежит токен')).toContain('вне репозитория')
    expect(byLabel('Отпечаток токена')).toBe('a1b2c3d4e5f6')
  })

  it('в чужом режиме своего репозитория нет — «—», а не выдумка', () => {
    const rows = tokenStatusRows(status(), cloneStatus({ mode: 'shared', mode_label: 'Папка в общем репозитории' }))
    const repo = rows.find((row) => row.label === 'Репозиторий копии')
    expect(repo?.value).toBe('—')
    expect(rows.find((row) => row.label === 'Режим работы копии')?.value).toBe(
      'Папка в общем репозитории'
    )
  })

  it('маска значения: видны только последние четыре знака', () => {
    expect(maskToken('abcdefgh1234')).toBe('••••••••1234')
    expect(maskToken('')).toBe('')
    expect(maskToken('abc')).toBe('••••••••')
  })

  it('выпущенный токен: значение есть один раз, в маске — по умолчанию', () => {
    const view = tokenIssueView({
      ok: true,
      token: 'secret-value-1234',
      token_id: 'a1b2c3d4e5f6',
      instance_id: 'mg-693f8fdd',
      folder: 'copies/mg-693f8fdd/',
      issued_at: Date.UTC(2026, 10, 25, 9, 45, 53),
      path: 'C:/Users/valstan/AppData/Local/minigen-gui/credentials/copy-token.txt',
      why: 'токен выпущен для папки copies/mg-693f8fdd/ — отдайте его копии'
    })
    expect(view.tone).toBe('ok')
    expect(view.token).toBe('secret-value-1234')
    expect(maskToken(view.token)).not.toContain('secret')
  })

  it('выпуск не удался — об этом сказано текстом', () => {
    const failed = tokenIssueView({ ok: false, error: 'папка не найдена' })
    expect(failed.tone).toBe('bad')
    expect(failed.text).toBe('папка не найдена')
    expect(failed.token).toBe('')

    const silent = tokenIssueView(null)
    expect(silent.tone).toBe('bad')
    expect(silent.text).toContain('мост не ответил')
  })
})

describe('токен копии: заявки', () => {
  it('заявка «своя» — помечена своей, «чужая» — чужой', () => {
    const own = tokenRequestRows([
      request({
        relation: 'своя копия (эта же программа)',
        signature_state: 'подпись не проверяется: чужая подпись'
      })
    ])
    expect(own[0].relation).toBe('своя копия')
    expect(own[0].tone).toBe('ok')
    expect(own[0].relation_detail).toContain('своя копия')

    const foreign = tokenRequestRows([
      request({ relation: 'чужая копия (закреплённая)', signature_state: 'отпечаток совпадает с закреплённым' })
    ])
    expect(foreign[0].relation).toBe('чужая копия')
    expect(foreign[0].tone).toBe('warn')
  })

  it('чужая копия с несовпавшим отпечатком — тон «плохо»', () => {
    const rows = tokenRequestRows([
      request({
        relation: 'чужая копия (отпечаток не совпал — возможна подделка)',
        signature_state: 'отпечаток не совпал с закреплённым'
      })
    ])
    expect(rows[0].relation).toBe('чужая копия')
    expect(rows[0].tone).toBe('bad')
    expect(rows[0].signature).toContain('не совпал')
  })

  it('строка заявки: кто прислал, папка, когда — пустое «—»', () => {
    const rows = tokenRequestRows([
      request({ name: '', instance_id: 'mg-693f8fdd', folder: '', created: '' })
    ])
    expect(rows[0].who).toBe('mg-693f8fdd')
    expect(rows[0].folder).toBe('—')
    expect(rows[0].when).toBe('—')
    expect(tokenRequestRows(null)).toEqual([])
  })

  it('дата заявки: разобралась — по Москве, не разобралась — как есть', () => {
    expect(requestTime('2026-11-25T09:45:53.000Z')).toContain('2026')
    expect(requestTime('вчера вечером')).toBe('вчера вечером')
    expect(requestTime('')).toBe('—')
  })

  it('заявка не ушла — мост сказал почему', () => {
    expect(tokenRequestResultText({ ok: true, why: 'заявка отправлена в раздел MiniGen-tokens — ждём токен от владельца' })).toContain(
      'ждём токен от владельца'
    )
    expect(tokenRequestResultText({ ok: false, error: 'раздел не заведён' })).toBe('раздел не заведён')
    expect(tokenRequestResultText({ ok: true })).toContain('ждём токен от владельца')
  })

  it('сохранение токена: принят и подтверждён — отдельно от «не подтвердился»', () => {
    expect(
      tokenSetResultText({ ok: true, why: 'токен принят и подтверждён: он выдан под папку copies/mg-693f8fdd/' })
    ).toContain('подтверждён')
    expect(
      tokenSetResultText({ ok: true, bound: false })
    ).toContain('не прошло')
    expect(tokenSetResultText({ ok: false, error: 'пустой токен' })).toBe('пустой токен')
  })
})

describe('токен копии: проверка доступа', () => {
  it('доступ есть — тон «ок»', () => {
    const check: CopyTokenCheck = {
      ok: true,
      folder: 'copies/mg-693f8fdd/',
      token_id: 'a1b2c3d4e5f6',
      reason: 'доступ есть: токен подтверждён под папку copies/mg-693f8fdd/'
    }
    const view = accessCheckText(check)
    expect(view.tone).toBe('ok')
    expect(view.text).toContain('доступ есть')
  })

  it('чужая папка — отказ, а не «доступ частичный»', () => {
    const check: CopyTokenCheck = {
      ok: false,
      folder: 'copies/mg-777/',
      token_id: 'a1b2c3d4e5f6',
      reason: 'токен выдан под папку copies/mg-693f8fdd/, а просили copies/mg-777/ — чужая папка, доступ закрыт'
    }
    const view = accessCheckText(check)
    expect(view.tone).toBe('bad')
    expect(view.text).toContain('чужая папка')
    expect(view.text).toContain('доступ закрыт')
  })

  it('токена нет — доступа нет; мост не ответил — не проверяли', () => {
    const none = accessCheckText({
      ok: false,
      folder: 'copies/mg-693f8fdd/',
      token_id: '',
      reason: 'токена нет — доступа нет'
    })
    expect(none.tone).toBe('bad')
    expect(none.text).toContain('доступа нет')

    const silent = accessCheckText(null)
    expect(silent.tone).toBe('bad')
    expect(silent.text).toContain('мост не ответил')
  })
})

describe('токен копии: подтверждения', () => {
  it('выпуск токена: папка, токен, репозиторий и «покажут один раз»', () => {
    const text = issueConfirmText(request())
    expect(text).toContain('токен')
    expect(text).toContain('папку')
    expect(text).toContain('репозиторий')
    expect(text).toContain('copies/mg-693f8fdd/')
    expect(text).toContain('один раз')
    expect(text).toContain('копии')
    expect(text).toContain('Продолжить?')
  })

  it('закрепление копии: отпечаток и предупреждение о подделке', () => {
    const text = requestConfirmText(request())
    expect(text).toContain('токен')
    expect(text).toContain('папку')
    expect(text).toContain('отпечаток')
    expect(text).toContain('подделк')
    expect(text).toContain('Продолжить?')
  })

  it('сохранение токена: стор вне репозитория и «в чат не надо»', () => {
    const text = tokenSetConfirmText()
    expect(text).toContain('токен')
    expect(text).toContain('папку')
    expect(text).toContain('репозиторий')
    expect(text).toContain('вне репозитория')
    expect(text).toContain('чат')
    expect(text).toContain('Продолжить?')
  })
})
