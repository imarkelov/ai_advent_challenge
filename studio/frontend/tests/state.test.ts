import { describe, expect, it } from 'vitest'
import { appendDelta, finishAssistant, initialState, reducer, type StudioState } from '../src/state'

// Базовое состояние: чистый reducer-тест без React
const base: StudioState = { ...initialState() }

describe('appendDelta — дописывание дельты ответа', () => {
  it('создаёт assistant-сообщение, когда сообщений нет', () => {
    expect(appendDelta([], 'Пр')).toEqual([{ role: 'assistant', content: 'Пр' }])
  })

  it('дописывает в последнее assistant-сообщение', () => {
    const msgs = [
      { role: 'user' as const, content: 'привет' },
      { role: 'assistant' as const, content: 'Пр' },
    ]
    expect(appendDelta(msgs, 'ивет')).toEqual([
      { role: 'user', content: 'привет' },
      { role: 'assistant', content: 'Привет' },
    ])
  })

  it('после user-сообщения начинает новое assistant', () => {
    const msgs = [{ role: 'user' as const, content: 'привет' }]
    expect(appendDelta(msgs, 'ок')).toEqual([
      { role: 'user', content: 'привет' },
      { role: 'assistant', content: 'ок' },
    ])
  })
})

describe('finishAssistant — авторитетный ответ из done', () => {
  it('заменяет хвост-накопленный ответ полным ответом', () => {
    const msgs = [{ role: 'assistant' as const, content: 'частично' }]
    expect(finishAssistant(msgs, 'полный ответ')).toEqual([{ role: 'assistant', content: 'полный ответ' }])
  })

  it('пустой ответ ничего не добавляет', () => {
    const msgs = [{ role: 'user' as const, content: 'привет' }]
    expect(finishAssistant(msgs, '')).toEqual(msgs)
  })
})

describe('reducer — чистые переходы состояния', () => {
  it('user-message: добавляет сообщение и включает стриминг', () => {
    const s = reducer(base, { type: 'user-message', message: { role: 'user', content: 'привет' } })
    expect(s.messages).toEqual([{ role: 'user', content: 'привет' }])
    expect(s.streaming).toBe(true)
  })

  it('delta: дописывает в assistant-сообщение', () => {
    const s1 = reducer(base, { type: 'user-message', message: { role: 'user', content: 'привет' } })
    const s2 = reducer(s1, { type: 'delta', text: 'ок' })
    expect(s2.messages).toHaveLength(2)
    expect(s2.messages[1]).toEqual({ role: 'assistant', content: 'ок' })
    expect(s2.streaming).toBe(true)
  })

  it('done: завершает ответ и выключает стриминг', () => {
    const s1 = reducer(base, { type: 'user-message', message: { role: 'user', content: 'привет' } })
    const s2 = reducer(reducer(s1, { type: 'delta', text: 'по' }), { type: 'done', answer: 'полный ответ' })
    expect(s2.messages[1]).toEqual({ role: 'assistant', content: 'полный ответ' })
    expect(s2.streaming).toBe(false)
  })

  it('error-message: кладёт ошибку в чат и выключает стриминг', () => {
    const s = reducer(base, { type: 'error-message', text: 'Ошибка: сервер недоступен' })
    expect(s.messages).toEqual([{ role: 'assistant', content: 'Ошибка: сервер недоступен' }])
    expect(s.streaming).toBe(false)
  })

  it('refresh: обновление memory/tokens/requests после done', () => {
    const memory = {
      active_id: 1,
      dialogue: { message_count: 2, tokens_est: 10 },
      working: { entries: 0, tokens_est: 0, items: {} },
      long_term: { entries: 0, tokens_est: 0, items: {} },
    }
    const tokens = { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 }
    expect(base.memory).toBeNull()
    const s = reducer(base, { type: 'refresh', memory, tokens, requests: [] })
    expect(s.memory).toEqual(memory)
    expect(s.tokens).toEqual(tokens)
  })

  it('refresh с lastRequest: сохраняет полную запись запроса', () => {
    const detail = { id: 7, ts: 't', model: 'm', request: {}, usage: null, error: null }
    const s = reducer(base, {
      type: 'refresh',
      memory: null,
      tokens: null,
      requests: [],
      lastRequest: detail,
    })
    expect(s.lastRequest).toEqual(detail)
  })

  it('created: новый диалог становится активным, лента чистая', () => {
    const s = reducer(base, {
      type: 'created',
      dialogue: { id: 3, title: 'Диалог 3', created: '2026-09-19', message_count: 0 },
      activeId: 3,
    })
    expect(s.activeId).toBe(3)
    expect(s.dialogues).toHaveLength(1)
    expect(s.messages).toEqual([])
  })

  it('show-requests: переключает видимость журнала', () => {
    expect(reducer(base, { type: 'show-requests', on: false }).showRequests).toBe(false)
    expect(reducer(base, { type: 'show-requests', on: true }).showRequests).toBe(true)
  })
})
