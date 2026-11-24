import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

/**
 * Главный чат программы называется «Управляющий» (заказ владельца 24.11),
 * и встроенная нейросеть знает, кто она и как работает: её системный промпт
 * — knowledge-pack, а значит роль и рабочие рецепты должны быть там.
 */

const src = join(__dirname, '../src')
const docs = join(__dirname, '../docs')

function read(...parts: string[]): string {
  return readFileSync(join(...parts), 'utf8')
}

describe('имя главного чата', () => {
  it('вкладка называется «Управляющий»', () => {
    const app = read(src, 'renderer', 'src', 'App.tsx')
    expect(app).toContain("{ id: 'chat', label: 'Управляющий' }")
  })

  it('заголовок в чате — «Управляющий»', () => {
    const chat = read(src, 'renderer', 'src', 'tabs', 'ChatTab.tsx')
    expect(chat).toContain('<h2>Управляющий</h2>')
  })
})

describe('knowledge-pack (системный промпт нейросети)', () => {
  const pack = read(docs, 'knowledge-pack.md')

  it('называет роль: Ты — Управляющий', () => {
    expect(pack).toContain('## Ты — Управляющий')
  })

  it('объясняет, как проверять и чинить (заказ: «проверь сеть, минимаксы»)', () => {
    expect(pack).toContain('## Проверка и починка: как это делается')
    expect(pack).toContain('network.check')
    expect(pack).toContain('areas.state')
  })

  it('говорит, что программа обновляется по решению владельца, а не нейросетью', () => {
    expect(pack).toContain('rollout.apply')
  })
})
