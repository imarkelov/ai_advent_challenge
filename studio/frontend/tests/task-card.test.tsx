import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, useEffect, useRef } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider, useStudio } from '../src/state'
import type { Message } from '../src/state'
import TaskCard, { STAGE_LABELS, taskFromMarkers } from '../src/components/TaskCard'
import type { TaskPlanEntry, TaskState } from '../src/api'

// Контракты GET /api/* для StudioProvider (loadAll при старте)
const API_FIXTURES: Record<string, unknown> = {
  '/api/config': { model: 'qwen3.8-27b', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
  '/api/dialogues': { active_id: null, dialogues: [] },
  '/api/memory': {
    active_id: null,
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
    toggles: { st: true, wm: true, lt: true },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}
function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

// ── фикстуры задачи (схема дня 13b) ──

function entry(
  step: number,
  agent: TaskPlanEntry['agent'],
  status: TaskPlanEntry['status'],
  extra: Partial<TaskPlanEntry> = {},
): TaskPlanEntry {
  return { step, agent, status, output: null, verdict: null, spawn_ts: null, ts: null, ...extra }
}

function makeTask(partial: Partial<TaskState> = {}): TaskState {
  return {
    active: true,
    task_id: 't_1',
    stage: 'execution',
    current_step: 2,
    total_steps: 4,
    expected_action: 'agent_response',
    plan: [
      entry(1, 'planning', 'completed', { output: '["A","B"]' }),
      entry(2, 'execution', 'in_progress'),
      entry(3, 'validation', 'pending'),
      entry(4, 'done', 'pending'),
    ],
    work_steps: [
      { name: 'A', status: 'completed', output: 'рез A', ts: null },
      { name: 'B', status: 'in_progress', output: null, ts: null },
    ],
    context_snapshot: null,
    description: 'Сделать кнопку',
    instruction: '',
    retries: 0,
    error: null,
    updated: null,
    ...partial,
  }
}

// Задача для live-сценариев: execution в работе, work-шаг B (index 1) выполняется
const LIVE_TASK = makeTask()

// SSE-поток, который НЕ закрывается — задача «живая» (taskRunning остаётся true)
function openSse(frames: string[]): Response {
  const enc = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(enc.encode(f))
    },
  })
  return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
}

// Проба: запускает пайплайн, когда загрузился активный диалог
// (SSE /api/task/run остаётся открытым — taskRunning true)
function RunProbe() {
  const { state, runTask } = useStudio()
  const started = useRef(false)
  useEffect(() => {
    if (state.activeId && !started.current) {
      started.current = true
      void runTask()
    }
  }, [state.activeId, runTask])
  return null
}

// fetch-стуб для live-сценариев: активный диалог d1 с LIVE_TASK + открытый SSE run
function stubLiveFetch(record: string[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url === '/api/task/run') {
        record.push('run')
        return openSse([
          'data: {"type":"agent_spawned","stage":"execution","agent":"Исполнитель"}\n\n',
          'data: {"type":"step_updated","index":1,"name":"B","status":"in_progress"}\n\n',
          'data: {"type":"step_delta","index":1,"text":"делаю шаг B"}\n\n',
        ])
      }
      if (method === 'POST' && url === '/api/task/pause') {
        record.push('pause')
        return jsonResponse({ task: LIVE_TASK })
      }
      if (method === 'POST' && url === '/api/task/resume') {
        record.push('resume')
        return jsonResponse({ task: LIVE_TASK })
      }
      if (url.startsWith('/api/task?')) {
        return jsonResponse({ task: LIVE_TASK })
      }
      if (url === '/api/dialogues') {
        return jsonResponse({
          active_id: 'd1',
          dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, task: LIVE_TASK }],
        })
      }
      if (url === '/api/dialogues/d1') {
        return jsonResponse({ dialogue: { messages: [] } })
      }
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
}

function stubDefaultFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) =>
      jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
  )
}

beforeEach(() => {
  localStorage.clear()
})

describe('TaskCard — заголовок и статус-бейдж', () => {
  it('описание задачи видно в заголовке', async () => {
    stubDefaultFetch()
    render(<StudioProvider><TaskCard task={makeTask()} live={false} /></StudioProvider>)
    expect(await screen.findByText('Задача: Сделать кнопку')).toBeTruthy()
  })

  it('done → бейдж «Готово»', async () => {
    stubDefaultFetch()
    render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'done',
            current_step: 4,
            plan: [
              entry(1, 'planning', 'completed', { output: 'п' }),
              entry(2, 'execution', 'completed', { output: 'р' }),
              entry(3, 'validation', 'completed', { output: 'в' }),
              entry(4, 'done', 'completed', { output: 'ф' }),
            ],
          })}
          live={false}
        />
      </StudioProvider>,
    )
    expect(await screen.findByText('Готово')).toBeTruthy()
  })

  it('paused → бейдж «Пауза»', async () => {
    stubDefaultFetch()
    render(<StudioProvider><TaskCard task={makeTask({ stage: 'paused' })} live={false} /></StudioProvider>)
    expect(await screen.findByText('Пауза')).toBeTruthy()
  })

  it('failed → бейдж «Ошибка»', async () => {
    stubDefaultFetch()
    render(
      <StudioProvider>
        <TaskCard task={makeTask({ stage: 'failed', error: 'модель не ответила' })} live={false} />
      </StudioProvider>,
    )
    expect(await screen.findByText('Ошибка')).toBeTruthy()
    expect(screen.getByText('модель не ответила')).toBeTruthy()
  })

  it('pipeline-стадия + running → бейдж «Выполняется»', async () => {
    stubLiveFetch()
    render(
      <StudioProvider>
        <RunProbe />
        <TaskCard task={LIVE_TASK} live />
      </StudioProvider>,
    )
    expect(await screen.findByText('Выполняется')).toBeTruthy()
  })
})

describe('TaskCard — прогресс', () => {
  it('«Этап N/4: <label>» по current_step и стадии', async () => {
    stubDefaultFetch()
    render(<StudioProvider><TaskCard task={makeTask()} live={false} /></StudioProvider>)
    expect(await screen.findByText('Этап 2/4: Исполнение')).toBeTruthy()
  })

  it('прогресс-бар — ширина по числу completed записей плана', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider><TaskCard task={makeTask()} live={false} /></StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    // 1 из 4 записей плана completed (planning)
    expect(container.querySelector('.task-card-bar-fill')?.style.width).toBe('25%')
  })
})

describe('TaskCard — секции plan[]', () => {
  it('4 секции: иконка + подпись + бейдж статуса; output стадии', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider><TaskCard task={makeTask()} live={false} /></StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    const sections = container.querySelectorAll('.task-agent')
    expect(sections).toHaveLength(4)
    // подписи стадий
    expect(screen.getAllByText('Планирование').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Исполнение').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Валидация').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Завершение').length).toBeGreaterThanOrEqual(1)
    // бейджи статусов
    expect(screen.getByText('готово')).toBeTruthy()
    expect(screen.getByText('выполняется')).toBeTruthy()
    expect(screen.getAllByText('ожидает')).toHaveLength(2)
    // output planning-секции
    expect(screen.getByText('["A","B"]')).toBeTruthy()
  })

  it('execution-секция: чек-лист work-шагов (✓/⏳/○) + output', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider><TaskCard task={makeTask()} live={false} /></StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    const checks = container.querySelectorAll('.task-check')
    expect(checks).toHaveLength(2)
    expect(container.querySelector('.task-check.completed .task-check-name')?.textContent).toBe('A')
    expect(container.querySelector('.task-check.in_progress .task-check-name')?.textContent).toBe('B')
    expect(screen.getByText('рез A')).toBeTruthy()
  })

  it('validation-секция: output + вердикт-чип «прошла»', async () => {
    stubDefaultFetch()
    render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'done',
            current_step: 4,
            plan: [
              entry(1, 'planning', 'completed', { output: 'п' }),
              entry(2, 'execution', 'completed', { output: 'р' }),
              entry(3, 'validation', 'completed', { output: 'ок, план выполнен', verdict: 'pass' }),
              entry(4, 'done', 'completed', { output: 'финал' }),
            ],
          })}
          live={false}
        />
      </StudioProvider>,
    )
    expect(await screen.findByText('ок, план выполнен')).toBeTruthy()
    expect(screen.getByText('прошла')).toBeTruthy()
    expect(screen.getByText('финал')).toBeTruthy()
  })
})

describe('TaskCard — авто-развёртывание секций', () => {
  it('live-карточка: in_progress-запись развёрнута, остальные свёрнуты', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'planning',
            current_step: 1,
            plan: [
              entry(1, 'planning', 'in_progress'),
              entry(2, 'execution', 'pending'),
              entry(3, 'validation', 'pending'),
              entry(4, 'done', 'pending'),
            ],
          })}
          live
        />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    const sections = Array.from(container.querySelectorAll('.task-agent'))
    expect(sections[0].open).toBe(true)
    expect(sections[1].open).toBe(false)
    expect(sections[2].open).toBe(false)
    expect(sections[3].open).toBe(false)
  })

  it('все completed (не live) — все секции свёрнуты', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'done',
            current_step: 4,
            plan: [
              entry(1, 'planning', 'completed', { output: 'п' }),
              entry(2, 'execution', 'completed', { output: 'р' }),
              entry(3, 'validation', 'completed', { output: 'в' }),
              entry(4, 'done', 'completed', { output: 'ф' }),
            ],
          })}
          live={false}
        />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    for (const s of container.querySelectorAll('.task-agent')) {
      expect(s.open).toBe(false)
    }
  })

  it('failed — упавшая (невыполненная) секция развёрнута', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'failed',
            current_step: 2,
            error: 'сбой',
            plan: [
              entry(1, 'planning', 'completed', { output: 'п' }),
              entry(2, 'execution', 'in_progress'),
              entry(3, 'validation', 'pending'),
              entry(4, 'done', 'pending'),
            ],
          })}
          live
        />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    const sections = Array.from(container.querySelectorAll('.task-agent'))
    expect(sections[1].open).toBe(true)
  })
})

describe('TaskCard — кнопки действий', () => {
  it('running + pipeline: «Пауза», клик → POST /api/task/pause', async () => {
    const record: string[] = []
    stubLiveFetch(record)
    render(
      <StudioProvider>
        <RunProbe />
        <TaskCard task={LIVE_TASK} live />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'Пауза' })
    fireEvent.click(btn)
    await waitFor(() => expect(record).toContain('pause'))
    expect(record).toContain('run')
  })

  it('paused: «Продолжить», клик → POST /api/task/resume', async () => {
    const record: string[] = []
    stubLiveFetch(record)
    render(
      <StudioProvider>
        <TaskCard task={makeTask({ stage: 'paused' })} live />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'Продолжить' })
    fireEvent.click(btn)
    await waitFor(() => expect(record).toContain('resume'))
  })

  it('failed: «Повтор», клик → POST /api/task/resume', async () => {
    const record: string[] = []
    stubLiveFetch(record)
    render(
      <StudioProvider>
        <TaskCard task={makeTask({ stage: 'failed', error: 'сбой' })} live />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'Повтор' })
    fireEvent.click(btn)
    await waitFor(() => expect(record).toContain('resume'))
  })

  it('done — кнопок нет', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'done',
            current_step: 4,
            plan: [
              entry(1, 'planning', 'completed', { output: 'п' }),
              entry(2, 'execution', 'completed', { output: 'р' }),
              entry(3, 'validation', 'completed', { output: 'в' }),
              entry(4, 'done', 'completed', { output: 'ф' }),
            ],
          })}
          live
        />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    expect(container.querySelectorAll('button')).toHaveLength(0)
  })

  it('не live (история) — кнопок нет даже на paused/failed', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider>
        <TaskCard task={makeTask({ stage: 'paused' })} live={false} />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    expect(container.querySelectorAll('button')).toHaveLength(0)
  })
})

describe('TaskCard — живой вывод work-шага', () => {
  it('liveStep (step_delta) — бокс с текстом в execution-секции', async () => {
    stubLiveFetch()
    const { container } = render(
      <StudioProvider>
        <RunProbe />
        <TaskCard task={LIVE_TASK} live />
      </StudioProvider>,
    )
    await waitFor(() => {
      const live = container.querySelector('.task-live')
      expect(live).toBeTruthy()
      expect(live?.textContent).toContain('делаю шаг B')
    })
  })

  it('без liveStep — бокса нет', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider><TaskCard task={LIVE_TASK} live /></StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    expect(container.querySelector('.task-live')).toBeNull()
  })
})

describe('taskFromMarkers — восстановление истории из маркеров', () => {
  const messages: Message[] = [
    { role: 'user', content: 'Сделать кнопку', task_id: 't_1' },
    { role: 'assistant', content: '["A","B"]', model: 'm', task_id: 't_1', task_stage: 'planning' },
    { role: 'assistant', content: 'рез A', model: 'm', task_id: 't_1', task_stage: 'execution', task_step: 'A' },
    { role: 'assistant', content: 'план выполнен', model: 'm', task_id: 't_1', task_stage: 'validation' },
    { role: 'assistant', content: 'финальный ответ', model: 'm', task_id: 't_1' },
    // чужое сообщение без task_id
    { role: 'user', content: 'привет' },
    { role: 'assistant', content: 'здравствуйте' },
  ]

  it('полная история: stage done, 4 записи plan completed, work_steps, description', () => {
    const t = taskFromMarkers(messages, 't_1')
    expect(t.stage).toBe('done')
    expect(t.description).toBe('Сделать кнопку')
    expect(t.task_id).toBe('t_1')
    expect(t.plan).toHaveLength(4)
    expect(t.plan.every((e) => e.status === 'completed')).toBe(true)
    expect(t.work_steps.map((w) => w.name)).toEqual(['A'])
    expect(t.work_steps[0].output).toBe('рез A')
    expect(t.plan[0].output).toBe('["A","B"]')
    expect(t.plan[3].output).toBe('финальный ответ')
  })

  it('неполная история (до validation): stage по первой невыполненной', () => {
    const t = taskFromMarkers(messages.slice(0, 4), 't_1')
    expect(t.plan.filter((e) => e.status === 'completed').length).toBe(3)
    expect(t.stage).not.toBe('done')
    expect(t.plan[3].status).toBe('pending')
  })

  it('бэкворд: маркеры без task_id не ломают карточку (деградированное состояние)', () => {
    const old: Message[] = [
      { role: 'user', content: 'старый запрос' },
      { role: 'assistant', content: 'вывод', model: 'm', task_stage: 'planning' },
    ]
    const t = taskFromMarkers(old, 't_1')
    // маркеров t_1 нет — пустое состояние, рендер без краша
    expect(t.description).toBe('')
    expect(t.plan.every((e) => e.status === 'pending')).toBe(true)
    expect(t.work_steps).toEqual([])
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider><TaskCard task={t} live={false} /></StudioProvider>,
    )
    // карточка рендерится без краша; описание пустое
    expect(container.querySelector('.task-card-title')?.textContent).toBe('Задача: ')
    expect(container.querySelectorAll('.task-agent')).toHaveLength(4)
  })
})

describe('TaskCard — время работы и токены (день 13b)', () => {
  const USAGE = { prompt: 1000, completion: 234, total: 1234 }

  it('(a) completed-стадия: «спавн N с назад · работа Nс · T токенов»', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-20T12:00:00'))
    try {
      stubDefaultFetch()
      render(
        <StudioProvider>
          <TaskCard
            task={makeTask({
              stage: 'done',
              current_step: 4,
              plan: [
                entry(1, 'planning', 'completed', {
                  output: 'п',
                  spawn_ts: '2026-09-20 11:59:55',
                  duration_s: 12,
                  usage: USAGE,
                }),
                entry(2, 'execution', 'completed', { output: 'р' }),
                entry(3, 'validation', 'completed', { output: 'в' }),
                entry(4, 'done', 'completed', { output: 'ф' }),
              ],
            })}
            live={false}
          />
        </StudioProvider>,
      )
      // ru-RU: группировка разрядов в DOM — NBSP, но RTL нормализует текст
      // элемента, а строковый matcher сравнивается БЕЗ нормализации →
      // в ожидаемой строке обычный пробел
      expect(
        screen.getByText('спавн 5 с назад · работа 12с · 1 234 токенов'),
      ).toBeTruthy()
    } finally {
      vi.useRealTimers()
      vi.unstubAllGlobals()
    }
  })

  it('(a2) completed-стадия без spawn_ts (старые маркеры): без «спавн», ≥60с → «М мин С с»', () => {
    stubDefaultFetch()
    render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            stage: 'done',
            current_step: 4,
            plan: [
              entry(1, 'planning', 'completed', {
                output: 'п',
                duration_s: 90,
                usage: { prompt: 10, completion: 113, total: 123 },
              }),
              entry(2, 'execution', 'completed', { output: 'р' }),
              entry(3, 'validation', 'completed', { output: 'в' }),
              entry(4, 'done', 'completed', { output: 'ф' }),
            ],
          })}
          live={false}
        />
      </StudioProvider>,
    )
    expect(screen.getByText('работа 1 мин 30 с · 123 токенов')).toBeTruthy()
  })

  it('(b) completed work-шаг: строка показывает «45с · 812 токенов»', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider>
        <TaskCard
          task={makeTask({
            work_steps: [
              {
                name: 'A', status: 'completed', output: 'рез A', ts: null,
                duration_s: 45,
                usage: { prompt: 100, completion: 712, total: 812 },
              },
              { name: 'B', status: 'in_progress', output: null, ts: null },
            ],
          })}
          live={false}
        />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    const meta = container.querySelector('.task-check.completed .task-check-meta')
    expect(meta?.textContent).toBe('45с · 812 токенов')
  })

  // act() в React 19 требует IS_REACT_ACT_ENVIRONMENT (флаг ставится локально)
  it('(c) live-стадия: время работы от spawn_ts, тикает с useNow (fake timers)', () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-20T12:00:00'))
    try {
      stubDefaultFetch()
      const { container } = render(
        <StudioProvider>
          <TaskCard
            task={makeTask({
              stage: 'planning',
              current_step: 1,
              plan: [
                entry(1, 'planning', 'in_progress', {
                  spawn_ts: '2026-09-20 11:59:50',
                }),
                entry(2, 'execution', 'pending'),
                entry(3, 'validation', 'pending'),
                entry(4, 'done', 'pending'),
              ],
            })}
            live
          />
        </StudioProvider>,
      )
      const age = container.querySelector('.task-agent-age')
      expect(age?.textContent).toBe('спавн 10 с назад · работа 10с')
      act(() => {
        vi.advanceTimersByTime(5000)
      })
      expect(age?.textContent).toBe('спавн 15 с назад · работа 15с')
    } finally {
      vi.useRealTimers()
      vi.unstubAllGlobals()
    }
  })

  it('(c2) live work-шаг: локальный таймер с момента in_progress (fake timers)', () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-20T12:00:00'))
    try {
      stubDefaultFetch()
      const { container } = render(
        <StudioProvider><TaskCard task={LIVE_TASK} live /></StudioProvider>,
      )
      // B (in_progress) — таймер запущен; A (completed, без usage) — метки нет
      expect(container.querySelector('.task-check.completed .task-check-meta')).toBeNull()
      act(() => {
        vi.advanceTimersByTime(30000)
      })
      const meta = container.querySelector('.task-check.in_progress .task-check-meta')
      expect(meta?.textContent).toBe('30с')
    } finally {
      vi.useRealTimers()
      vi.unstubAllGlobals()
    }
  })

  it('(d) taskFromMarkers: task_usage/task_duration → usage/duration_s, карточка их показывает', () => {
    const msgs: Message[] = [
      { role: 'user', content: 'Запрос', task_id: 't_9' },
      {
        role: 'assistant', content: 'план', model: 'm', task_id: 't_9',
        task_stage: 'planning',
        task_usage: { prompt: 10, completion: 20, total: 30 }, task_duration: 7,
      },
      {
        role: 'assistant', content: 'рез A', model: 'm', task_id: 't_9',
        task_stage: 'execution', task_step: 'A',
        task_usage: { prompt: 100, completion: 200, total: 300 }, task_duration: 45,
      },
      {
        role: 'assistant', content: 'всё ок', model: 'm', task_id: 't_9',
        task_stage: 'validation',
        task_usage: { prompt: 5, completion: 5, total: 10 }, task_duration: 3,
      },
      {
        role: 'assistant', content: 'финал', model: 'm', task_id: 't_9',
        task_usage: { prompt: 1, completion: 2, total: 3 }, task_duration: 2,
      },
    ]
    const t = taskFromMarkers(msgs, 't_9')
    // стадии: planning/validation — по своим сообщениям, done — по финальному
    expect(t.plan[0].usage).toEqual({ prompt: 10, completion: 20, total: 30 })
    expect(t.plan[0].duration_s).toBe(7)
    expect(t.plan[2].usage).toEqual({ prompt: 5, completion: 5, total: 10 })
    expect(t.plan[2].duration_s).toBe(3)
    expect(t.plan[3].usage).toEqual({ prompt: 1, completion: 2, total: 3 })
    expect(t.plan[3].duration_s).toBe(2)
    // work-шаги — по execution-сообщениям с task_step
    expect(t.work_steps[0].usage).toEqual({ prompt: 100, completion: 200, total: 300 })
    expect(t.work_steps[0].duration_s).toBe(45)

    // восстановленная карточка показывает время и токены
    stubDefaultFetch()
    render(<StudioProvider><TaskCard task={t} live={false} /></StudioProvider>)
    expect(screen.getByText('работа 7с · 30 токенов')).toBeTruthy()
    const doneRow = screen.getAllByText('работа 2с · 3 токенов')
    expect(doneRow.length).toBe(1)
  })

  it('(e) legacy-задача без usage/duration/spawn_ts — рендер без меток и краха', async () => {
    stubDefaultFetch()
    const { container } = render(
      <StudioProvider><TaskCard task={makeTask()} live={false} /></StudioProvider>,
    )
    await screen.findByText('Задача: Сделать кнопку')
    expect(container.querySelectorAll('.task-check-meta')).toHaveLength(0)
    expect(container.querySelectorAll('.task-agent-age')).toHaveLength(0)
    expect(container.querySelectorAll('.task-agent')).toHaveLength(4)
  })
})

describe('STAGE_LABELS — 6 стадий unified FSM', () => {
  it('все 6 подписей на месте', () => {
    expect(STAGE_LABELS).toEqual({
      planning: 'Планирование',
      execution: 'Исполнение',
      validation: 'Валидация',
      done: 'Завершение',
      paused: 'Пауза',
      failed: 'Ошибка',
    })
  })
})
