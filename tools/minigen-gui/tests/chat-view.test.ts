import { describe, expect, it } from 'vitest'
import type { ChatMessage, ToolCall } from '../src/shared/types'
import {
  appendAssistant,
  appendMessage,
  callDetails,
  callsSummary,
  greetingMessage,
  pendingArgs,
  restoreMessages
} from '../src/renderer/src/tabs/chat-view'

/**
 * Логика чата «Управляющего» (заказ владельца от 25.11, промт #129).
 *
 * Проверяем то, что владелец видит: вызовы инструментов свёрнуты, подробности
 * открываются по требованию и **целиком** (обрезанного JSON быть не должно),
 * а переписка после перезапуска окна собирается из истории моста честно.
 */

function call(over: Partial<ToolCall> = {}): ToolCall {
  return {
    tool: 'network.check',
    args: { slug: 'minigen' },
    result: { ok: true, latency_ms: 42 },
    ...over
  }
}

describe('свёртка вызовов инструментов', () => {
  it('без вызовов свёртки нет', () => {
    expect(callsSummary([])).toBe('')
  })

  it('русские формы: 1 действие, 2-4 действия, 5+ действий', () => {
    expect(callsSummary([call()])).toBe('Что сделано: 1 действие')
    expect(callsSummary([call(), call()])).toBe('Что сделано: 2 действия')
    expect(callsSummary([call(), call(), call()])).toBe('Что сделано: 3 действия')
    expect(callsSummary([call(), call(), call(), call()])).toBe('Что сделано: 4 действия')
    expect(callsSummary(new Array(5).fill(call()))).toBe('Что сделано: 5 действий')
    expect(callsSummary(new Array(11).fill(call()))).toBe('Что сделано: 11 действий')
    expect(callsSummary(new Array(21).fill(call()))).toBe('Что сделано: 21 действие')
  })
})

describe('подробности вызова', () => {
  it('аргументы и результат — полные, без обрезки', () => {
    const long = 'x'.repeat(2000)
    const details = callDetails({
      tool: 'journal.read',
      args: { contains: long, limit: 1000 },
      result: { lines: long }
    })
    expect(details.tool).toBe('journal.read')
    expect(details.args).toContain(long)
    expect(details.args.length).toBeGreaterThan(2000)
    expect(details.result).toContain(long)
    expect(details.result.length).toBeGreaterThan(2000)
  })

  it('строковый результат — как есть', () => {
    expect(callDetails(call({ result: 'готово' })).result).toBe('готово')
  })

  it('имя инструмента и пустой результат не выдумываются', () => {
    const details = callDetails({ tool: 'areas.state', args: {}, result: undefined })
    expect(details.tool).toBe('areas.state')
    expect(details.args).toBe('{}')
    expect(details.result).toBe('')
  })
})

describe('подтверждение опасного действия', () => {
  it('аргументы — в свёртке: владелец видит, что именно выполняется', () => {
    const pending = {
      tool: 'code.commit',
      args: { branch: 'fix/defect', message: 'починил дефект' },
      reason: 'коммит требует подтверждения владельца',
      call_id: 't1'
    }
    const text = pendingArgs(pending)
    expect(text).toContain('fix/defect')
    expect(text).toContain('починил дефект')
  })

  it('аргументы пустые — не выдумываются', () => {
    expect(
      pendingArgs({ tool: 'code.pr', args: {}, reason: '', call_id: 't2' })
    ).toBe('{}')
  })
})

describe('восстановление переписки из истории моста', () => {
  it('пустые реплики — мусор: их убираем, порядок сохраняем, приветствие первым', () => {
    const stored: ChatMessage[] = [
      { role: 'user', text: 'проверь сеть' },
      { role: 'assistant', text: '', calls: [] },
      { role: 'assistant', text: 'проверил: сеть есть', calls: [call()] },
      { role: 'system', text: '   ' }
    ]
    const restored = restoreMessages(stored)
    expect(restored).toHaveLength(3)
    expect(restored[0].text).toBe(greetingMessage().text)
    expect(restored[0].role).toBe('system')
    expect(restored[1].text).toBe('проверь сеть')
    expect(restored[2].text).toBe('проверил: сеть есть')
  })

  it('пустая история — только приветствие, а не пустота', () => {
    expect(restoreMessages([])).toEqual([greetingMessage()])
  })

  it('приветствие не дублируется, если мост его вернул', () => {
    const restored = restoreMessages([greetingMessage(), { role: 'user', text: 'привет' }])
    expect(restored).toHaveLength(2)
    expect(restored[0].text).toBe(greetingMessage().text)
    expect(restored[1].text).toBe('привет')
  })
})

describe('сборка реплик', () => {
  it('реплика владельца дописывается в конец', () => {
    const list = appendMessage([greetingMessage()], { role: 'user', text: 'привет' })
    expect(list).toHaveLength(2)
    expect(list[1].role).toBe('user')
    expect(list[1].text).toBe('привет')
  })

  it('ответ моста: текст и вызовы — одной репликой', () => {
    const list = appendAssistant([greetingMessage()], {
      status: 'done',
      text: 'проверил сеть',
      calls: [call()]
    })
    expect(list).toHaveLength(2)
    expect(list[1].role).toBe('assistant')
    expect(list[1].text).toBe('проверил сеть')
    expect(list[1].calls).toHaveLength(1)
  })

  it('сбой нейросети — честная системная реплика с текстом ошибки', () => {
    const list = appendAssistant([greetingMessage()], {
      status: 'unavailable',
      text: '',
      calls: [],
      error: 'нет сети'
    })
    expect(list).toHaveLength(2)
    expect(list[1].role).toBe('system')
    expect(list[1].text).toContain('нет сети')
  })

  it('сбой без текста ошибки — тоже честно, без выдумки', () => {
    const list = appendAssistant([greetingMessage()], {
      status: 'error',
      text: '',
      calls: []
    })
    expect(list).toHaveLength(2)
    expect(list[1].role).toBe('system')
    expect(list[1].text).toContain('Нейросеть недоступна')
  })
})
