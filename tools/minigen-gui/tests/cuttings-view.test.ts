import { describe, expect, it } from 'vitest'
import {
  COPY_RULES,
  geneSteps,
  guideSections,
  plainLine,
  sendChecklist
} from '../src/renderer/src/tabs/cuttings-view'
import type { InstallGuide } from '../src/shared/types'

/**
 * «Черенки» — вкладка про размножение «МиниГена» (заказ владельца 26.11:
 * «не могу понять что мне отправить знакомому, чтобы он у себя установил
 * миниген тоже»).
 *
 * Главное правило вкладки: она отвечает на вопрос «**что отправить**», а не
 * просто показывает файл. Поэтому список собирается из живой инструкции, а
 * если инструкции нет — вкладка говорит об этом прямо и говорит, что делать.
 */

function guide(over: Partial<InstallGuide> = {}): InstallGuide {
  return {
    ok: true,
    title: 'Установка «МиниГена» на новый компьютер',
    text: '# Установка\n\n## 1. Что нужно\n\n- Node.js\n',
    path: 'D:/repo/tools/minigen-gui/docs/INSTALL.md',
    source: 'исходники',
    installer: 'D:/repo/tools/minigen-gui/install.ps1',
    installer_dir: 'D:/repo/tools/minigen-gui',
    repo: 'https://github.com/Valstan/biblio',
    ...over
  }
}

describe('что отправить знакомому', () => {
  it('список называет инструкцию, репозиторий и установщик', () => {
    const items = sendChecklist(guide())
    expect(items.join(' ')).toContain('INSTALL.md')
    expect(items.join(' ')).toContain('https://github.com/Valstan/biblio')
    expect(items.join(' ')).toContain('install.ps1')
  })

  it('в списке есть требования к машине знакомого', () => {
    const items = sendChecklist(guide()).join(' ')
    expect(items).toContain('Node.js')
    expect(items).toContain('Python')
    expect(items).toContain('gh auth login')
  })

  it('инструкции нет — список говорит, что сначала её надо найти', () => {
    const items = sendChecklist(guide({ ok: false })).join(' ')
    expect(items).toContain('Инструкции на машине нет')
  })

  it('инструкции нет совсем — список не выдумывает ничего', () => {
    expect(sendChecklist(null).join(' ')).toContain('Инструкции на машине нет')
  })
})

describe('показ инструкции в окне', () => {
  it('разделы инструкции разбираются по заголовкам', () => {
    const sections = guideSections('# Заголовок\n\n## Первый\n\n- раз\n\n## Второй\n\n- два\n')
    expect(sections.length).toBe(2)
    expect(sections[0].heading).toBe('Первый')
    expect(sections[1].heading).toBe('Второй')
    expect(sections[0].lines.join(' ')).toContain('- раз')
  })

  it('код внутри инструкции не теряется', () => {
    const sections = guideSections('## Установка\n\n```powershell\ninstall.ps1\n```\n')
    expect(sections[0].lines.join('\n')).toContain('install.ps1')
  })

  it('маркеры разметки убираются — текст читается', () => {
    expect(plainLine('**важно**: `install.ps1`')).toBe('важно: install.ps1')
  })
})

describe('передача наработок', () => {
  it('шагов четыре: публикация, выгрузка, внесение, чужие наработки', () => {
    const steps = geneSteps(true, '')
    expect(steps.map((step) => step.key)).toEqual([
      'publish',
      'export',
      'import',
      'pool'
    ])
  })

  it('обмен недоступен — об этом сказано прямо, а не спрятано', () => {
    const steps = geneSteps(false, 'копия локальная: пула нет')
    const note = steps[steps.length - 1]
    expect(note.key).toBe('note')
    expect(note.tone).toBe('warn')
    expect(note.detail).toContain('локальная')
  })
})

describe('что важно помнить', () => {
  it('правила про вторую копию на месте и говорят про отдельную машину', () => {
    const text = COPY_RULES.join(' ')
    expect(text).toContain('отдельная машина')
    expect(text).toContain('родословн')
    expect(text).toContain('GitHub')
  })
})
