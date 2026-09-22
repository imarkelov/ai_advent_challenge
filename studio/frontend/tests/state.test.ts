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

// ── задача: tasksFrom / activeTaskOf (день 13, схема дня 13b) ──

const taskA: TaskState = {
  active: true,
  task_id: 't_a1',
  stage: 'planning',
  current_step: 1,
  total_steps: 4,
  expected_action: 'agent_response',
  plan: [],
  work_steps: [],
  context_snapshot: null,
  description: 'Сделать кнопку',
  instruction: '',
  retries: 0,
  error: null,
  updated: null,
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

// ── день 13b: режимы ввода (chatMode) + step-события (taskLive) ──

describe('reducer — день 13b: chatMode, task-step, task-step-delta', () => {
  const taskB: TaskState = {
    active: true,
    task_id: 't_1',
    stage: 'execution',
    current_step: 2,
    total_steps: 4,
    expected_action: 'agent_response',
    plan: [
      { step: 1, agent: 'planning', status: 'completed', output: 'план', verdict: null, spawn_ts: null, ts: null },
      { step: 2, agent: 'execution', status: 'in_progress', output: null, verdict: null, spawn_ts: null, ts: null },
      { step: 3, agent: 'validation', status: 'pending', output: null, verdict: null, spawn_ts: null, ts: null },
      { step: 4, agent: 'done', status: 'pending', output: null, verdict: null, spawn_ts: null, ts: null },
    ],
    work_steps: [
      { name: 'A', status: 'in_progress', output: null, ts: null },
      { name: 'B', status: 'pending', output: null, ts: null },
    ],
    context_snapshot: null,
    description: 'Сделать кнопку',
    instruction: '',
    retries: 0,
    error: null,
    updated: null,
  }
  const withTask: StudioState = { ...base, activeId: 'a', tasks: { a: taskB } }

  it('chatMode: начальное значение — «chat», action меняет режим', () => {
    expect(initialState().chatMode).toBe('chat')
    expect(reducer(base, { type: 'chat-mode', mode: 'task' }).chatMode).toBe('task')
    expect(reducer(base, { type: 'chat-mode', mode: 'chat' }).chatMode).toBe('chat')
  })

  it('task-step: in_progress — сбрасывает taskLive и обновляет шаг', () => {
    const s = reducer(withTask, { type: 'task-step', index: 0, name: 'A', status: 'in_progress' })
    expect(s.taskLive).toEqual({ index: 0, text: '' })
    expect(s.tasks.a.work_steps[0].status).toBe('in_progress')
  })

  it('task-step: completed — taskLive null, output и статус записаны', () => {
    const s1 = reducer(withTask, { type: 'task-step', index: 0, name: 'A', status: 'in_progress' })
    const s2 = reducer(s1, { type: 'task-step', index: 0, name: 'A', status: 'completed', output: 'рез A' })
    expect(s2.taskLive).toBeNull()
    expect(s2.tasks.a.work_steps[0]).toEqual({ name: 'A', status: 'completed', output: 'рез A', ts: null })
  })

  it('task-step: чужой индекс — остальные шаги не трогает', () => {
    const s = reducer(withTask, { type: 'task-step', index: 1, name: 'B', status: 'in_progress' })
    expect(s.tasks.a.work_steps[0]).toEqual({ name: 'A', status: 'in_progress', output: null, ts: null })
    expect(s.tasks.a.work_steps[1].status).toBe('in_progress')
    expect(s.taskLive).toEqual({ index: 1, text: '' })
  })

  it('task-step: нет задачи у активного диалога — состояние не меняется', () => {
    const s = reducer(
      { ...withTask, activeId: 'x' },
      { type: 'task-step', index: 0, name: 'A', status: 'in_progress' },
    )
    expect(s).toEqual({ ...withTask, activeId: 'x' })
  })

  it('task-step-delta: дописывает живой вывод по индексу', () => {
    const s1 = reducer(withTask, { type: 'task-step', index: 0, name: 'A', status: 'in_progress' })
    const s2 = reducer(s1, { type: 'task-step-delta', index: 0, text: 'ку' })
    const s3 = reducer(s2, { type: 'task-step-delta', index: 0, text: 'сок' })
    expect(s3.taskLive).toEqual({ index: 0, text: 'кусок' })
  })

  it('task-step-delta: чужой индекс — новый бокс (текст прежнего не смешивается)', () => {
    const s1 = reducer(withTask, { type: 'task-step', index: 0, name: 'A', status: 'in_progress' })
    const s2 = reducer(s1, { type: 'task-step-delta', index: 0, text: 'ку' })
    const s3 = reducer(s2, { type: 'task-step-delta', index: 1, text: 'x' })
    expect(s3.taskLive).toEqual({ index: 1, text: 'x' })
  })

  it('task-running: просто флаг (поле taskCurrentStage в схеме дня 13b нет)', () => {
    const s = reducer(withTask, { type: 'task-running', on: true })
    expect(s.taskRunning).toBe(true)
    expect(s.taskLive).toBeNull()
  })
})

describe('reducer — overlay настроек: settingsOpen', () => {
  it('initialState: settingsOpen=false (overlay закрыт при старте)', () => {
    expect(base.settingsOpen).toBe(false)
    expect(initialState().settingsOpen).toBe(false)
  })

  it('open-settings: settingsOpen=true; вкладка не меняется без payload', () => {
    const s = reducer({ ...base, contextTab: 'mcp' }, { type: 'open-settings' })
    expect(s.settingsOpen).toBe(true)
    expect(s.contextTab).toBe('mcp')
  })

  it('open-settings с payload: открывает overlay И переключает вкладку', () => {
    const s = reducer(base, { type: 'open-settings', tab: 'profile' })
    expect(s.settingsOpen).toBe(true)
    expect(s.contextTab).toBe('profile')
  })

  it('close-settings: settingsOpen=false, текущая вкладка запоминается', () => {
    const s1 = reducer(base, { type: 'open-settings', tab: 'invariants' })
    const s2 = reducer(s1, { type: 'close-settings' })
    expect(s2.settingsOpen).toBe(false)
    expect(s2.contextTab).toBe('invariants')
  })
})

describe('reducer — инварианты (день 14): флаг нарушения invariant_violation', () => {
  it('invariant-violation: ставит transient-флаг с паттернами (default null)', () => {
    expect(base.invariantViolation).toBeNull()
    const s = reducer(base, { type: 'invariant-violation', patterns: ['lang', 'format'] })
    expect(s.invariantViolation).toEqual(['lang', 'format'])
  })

  it('done: флаг сохраняется (событие приходит до done, бейдж виден после ответа)', () => {
    const s1 = reducer(base, { type: 'user-message', message: { role: 'user', content: 'x' } })
    const s2 = reducer(s1, { type: 'invariant-violation', patterns: ['lang'] })
    const s3 = reducer(s2, { type: 'done', answer: 'Отказ…' })
    expect(s3.invariantViolation).toEqual(['lang'])
    expect(s3.streaming).toBe(false)
  })

  it('user-message: сбрасывает флаг (новая очередь)', () => {
    const s1 = reducer(base, { type: 'invariant-violation', patterns: ['lang'] })
    const s2 = reducer(s1, { type: 'user-message', message: { role: 'user', content: 'ещё' } })
    expect(s2.invariantViolation).toBeNull()
  })
})
