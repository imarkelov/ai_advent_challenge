import { describe, expect, it } from 'vitest'
import type { TaskState, UserProfile } from '../src/api'
import {
  activeProfileOf,
  activeTaskOf,
  appendDelta,
  finishAssistant,
  initialState,
  reducer,
  tasksFrom,
  type DialogueMeta,
  type StudioState,
} from '../src/state'

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
      toggles: { st: true, wm: true, lt: true },
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
      dialogue: { id: 'd3', title: 'Диалог 3', created: '2026-09-19', message_count: 0 },
      activeId: 'd3',
    })
    expect(s.activeId).toBe('d3')
    expect(s.dialogues).toHaveLength(1)
    expect(s.messages).toEqual([])
  })

  it('show-requests: переключает видимость журнала', () => {
    expect(reducer(base, { type: 'show-requests', on: false }).showRequests).toBe(false)
    expect(reducer(base, { type: 'show-requests', on: true }).showRequests).toBe(true)
  })

  it('models: хранит список моделей для дропдауна', () => {
    const models = [
      { id: 'qwen3.8-27b', context_limit: 32768 },
      { id: 'deepseek-v4-flash', context_limit: 16384 },
    ]
    expect(base.models).toEqual([])
    const s = reducer(base, { type: 'models', models })
    expect(s.models).toEqual(models)
  })

  it('config: обновление конфига (например, после выбора модели)', () => {
    const s = reducer(base, {
      type: 'config',
      config: { model: 'deepseek-v4-flash', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
    })
    expect(s.config?.model).toBe('deepseek-v4-flash')
  })
})

describe('reducer — профиль пользователя (день 12)', () => {
  const profileActive: UserProfile = {
    status: 'active',
    interview: false,
    name: 'Иван',
    role: 'backend',
    tone: 'кратко',
    taboos: 'мат',
  }
  const profilePending: UserProfile = {
    status: 'pending',
    interview: false,
    name: '',
    role: '',
    tone: '',
    taboos: '',
  }
  const profileDeclined: UserProfile = {
    status: 'declined',
    interview: false,
    name: '',
    role: '',
    tone: '',
    taboos: '',
  }
  const memory = {
    active_id: 'd1',
    dialogue: { message_count: 1, tokens_est: 5 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
    toggles: { st: true, wm: true, lt: true },
  }
  const tokens = { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 }

  const d1: DialogueMeta = { id: 'd1', title: 'Первый', created: '2026-01-01', message_count: 1, profile: profileActive }
  const d2: DialogueMeta = { id: 'd2', title: 'Второй', created: '2026-01-02', message_count: 0, profile: profilePending }

  function loadedAction(dialogues: DialogueMeta[], activeId: string | null) {
    return {
      type: 'loaded' as const,
      config: { model: 'm', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
      dialogues,
      activeId,
      memory,
      tokens,
      requests: [],
    }
  }

  it('loaded: profiles наполняются из списка диалогов, activeProfileOf — профиль активного', () => {
    const s = reducer(base, loadedAction([d1, d2], 'd1'))
    expect(s.profiles).toEqual({ d1: profileActive, d2: profilePending })
    expect(activeProfileOf(s)).toEqual(profileActive)
  })

  it('loaded: диалог без поля profile — нет записи, activeProfileOf null (бэкворд)', () => {
    const old: DialogueMeta = { id: 'd1', title: 'Старый', created: '2026-01-01', message_count: 1 }
    const s = reducer(base, loadedAction([old], 'd1'))
    expect(s.profiles).toEqual({})
    expect(activeProfileOf(s)).toBeNull()
  })

  it('activeProfileOf: null, когда активного диалога нет', () => {
    const s = reducer(base, loadedAction([d1], null))
    expect(s.profiles).toEqual({ d1: profileActive })
    expect(activeProfileOf(s)).toBeNull()
  })

  it('profile-set: обновляет профиль активного — activeProfileOf пересчитан', () => {
    const s1 = reducer(base, loadedAction([d1, d2], 'd1'))
    const s2 = reducer(s1, { type: 'profile-set', id: 'd1', profile: profileDeclined })
    expect(s2.profiles['d1']).toEqual(profileDeclined)
    expect(activeProfileOf(s2)).toEqual(profileDeclined)
  })

  it('profile-set: чужой (неактивный) диалог — activeProfileOf не меняется', () => {
    const s1 = reducer(base, loadedAction([d1, d2], 'd1'))
    const s2 = reducer(s1, { type: 'profile-set', id: 'd2', profile: profileDeclined })
    expect(s2.profiles['d2']).toEqual(profileDeclined)
    expect(activeProfileOf(s2)).toEqual(profileActive)
  })

  it('dialogues-refresh: перечитывает profiles из нового списка', () => {
    const s1 = reducer(base, loadedAction([d1, d2], 'd1'))
    const refreshed: DialogueMeta = { ...d1, profile: profileDeclined }
    const s2 = reducer(s1, { type: 'dialogues-refresh', dialogues: [refreshed, d2] })
    expect(activeProfileOf(s2)).toEqual(profileDeclined)
  })
})

// ── задача: tasksFrom / activeTaskOf (день 13) ──

const taskA: TaskState = {
  active: true, stage: 'planning', paused: false,
  description: 'Сделать кнопку', instruction: '', stages: {},
  retries: 0, error: null, updated: null,
}

describe('tasksFrom', () => {
  it('извлекает task из диалогов, пропускает записи без поля', () => {
    const out = tasksFrom([
      { id: 'a', title: '', created: '', message_count: 0, task: taskA },
      { id: 'b', title: '', created: '', message_count: 0 },
    ])
    expect(out.a).toEqual(taskA)
    expect(out.b).toBeUndefined()
  })
})

describe('activeTaskOf', () => {
  it('возвращает task активного диалога или null', () => {
    const st = { activeId: 'a', tasks: { a: taskA } }
    expect(activeTaskOf(st)).toEqual(taskA)
    expect(activeTaskOf({ activeId: 'b', tasks: { a: taskA } })).toBeNull()
    expect(activeTaskOf({ activeId: null, tasks: {} })).toBeNull()
  })
})
