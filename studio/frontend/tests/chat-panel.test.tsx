import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useEffect, useRef } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider, useStudio } from '../src/state'
import type { TaskState, UserProfile } from '../src/api'
import ChatPanel from '../src/components/ChatPanel'
import Sidebar from '../src/components/Sidebar'

// Контракты GET /api/* для StudioProvider (loadAll при старте)
const API_FIXTURES: Record<string, unknown> = {
  '/api/config': {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    max_tokens: 1024,
    system_prompt: 'Ты — ассистент.',
  },
  '/api/dialogues': { active_id: null, dialogues: [] },
  '/api/memory': {
    active_id: null,
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': {
    models: [
      { id: 'qwen3.8-27b', context_limit: 32768 },
      { id: 'deepseek-v4-flash', context_limit: 16384 },
      { id: 'glm-5.3-flash', context_limit: 16384 },
    ],
  },
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

beforeEach(() => {
  localStorage.clear()
})

describe('ChatPanel — выбор модели из выпадающего списка', () => {
  it('дропдаун со всеми моделями /api/models, выбранная — текущая', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const select = (await screen.findByRole('combobox')) as HTMLSelectElement
    // ждём, когда список моделей дойдёт из /api/models
    await screen.findByRole('option', { name: 'glm-5.3-flash' })
    expect(Array.from(select.options).map((o) => o.value)).toEqual([
      'qwen3.8-27b',
      'deepseek-v4-flash',
      'glm-5.3-flash',
    ])
    expect(select.value).toBe('qwen3.8-27b')
  })

  it('выбор модели — POST /api/config {model}, дропдаун обновляется', async () => {
    const posted: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        if (init?.method === 'POST' && url === '/api/config') {
          posted.push(JSON.parse(String(init.body)))
          return jsonResponse({
            model: 'deepseek-v4-flash',
            temperature: 0.7,
            max_tokens: 1024,
            system_prompt: 'sp',
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const select = (await screen.findByRole('combobox')) as HTMLSelectElement
    await screen.findByRole('option', { name: 'deepseek-v4-flash' })
    fireEvent.change(select, { target: { value: 'deepseek-v4-flash' } })
    expect(posted).toEqual([{ model: 'deepseek-v4-flash' }])
    // config-обновление асинхронное: ждём, пока value дропдауна переключится
    await waitFor(() => expect(select.value).toBe('deepseek-v4-flash'))
  })

  it('модели из API недоступны (502) — текущая модель остаётся в списке', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url === '/api/models') {
          return new Response('unavailable', { status: 502 })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const select = (await screen.findByRole('combobox')) as HTMLSelectElement
    // /api/models упал — ждём текущую модель из конфига в списке
    await screen.findByRole('option', { name: 'qwen3.8-27b' })
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['qwen3.8-27b'])
    expect(select.value).toBe('qwen3.8-27b')
  })
})

describe('ChatPanel — чип модели под assistant-сообщением', () => {
  it('assistant с model → чип; без model → чипа нет', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Диалог', created: '2026-09-19', message_count: 4 }],
          })
        }
        if (url === '/api/dialogues/d1') {
          return jsonResponse({
            dialogue: {
              messages: [
                { role: 'user', content: 'привет' },
                { role: 'assistant', content: 'старый ответ' },
                { role: 'user', content: 'ещё раз' },
                { role: 'assistant', content: 'новый ответ', model: 'deepseek-v4-flash' },
              ],
            },
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('старый ответ')
    const chips = document.querySelectorAll('.msg-model-chip')
    // ровно один чип — у assistant-сообщения с model
    expect(chips).toHaveLength(1)
    expect(chips[0]).toHaveTextContent('deepseek-v4-flash')
    expect(chips[0].getAttribute('title')).toBe('Модель, которой выполнен запрос')
  })
})

describe('ChatPanel + Sidebar — перечитывание списка диалогов после chat done', () => {
  it('SSE done → GET /api/dialogues повторно; авто-title бэкенда появился в сайдбаре', async () => {
    const server = { autoTitled: false }
    let dialoguesGets = 0
    // SSE-поток: дельта + done (контракт дня 11)
    const sse =
      'data: {"type":"delta","text":"Привет"}\n\ndata: {"type":"done","answer":"Привет","usage":null,"request_id":1}\n\n'
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/chat') {
          // бэкенд после первого сообщения переименовал диалог
          server.autoTitled = true
          return new Response(sse, { headers: { 'Content-Type': 'text/event-stream' } })
        }
        if (method === 'GET' && url === '/api/dialogues') {
          dialoguesGets += 1
          const title = server.autoTitled ? 'Авто-заголовок' : 'Новый диалог'
          return jsonResponse({
            active_id: 'd1',
            dialogues: [
              { id: 'd1', title, created: '2026-09-19', message_count: server.autoTitled ? 2 : 0 },
            ],
          })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          return jsonResponse({ dialogue: { messages: [] } })
        }
        if (method === 'GET' && url === '/api/requests/1') {
          return jsonResponse({ id: 1, ts: 't', model: 'qwen3.8-27b', request: {}, usage: null, error: null })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <Sidebar />
        <ChatPanel />
      </StudioProvider>,
    )

    // после стартовой загрузки — одно GET /dialogues, сайдбар показывает «Новый диалог»
    await screen.findByRole('button', { name: /Новый диалог/ })
    await waitFor(() => expect(dialoguesGets).toBe(1))

    fireEvent.change(screen.getByPlaceholderText(/Сообщение…/), { target: { value: 'привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    // done → список диалогов перечитан, авто-title отобразился без перезагрузки
    // (одновременно в списке сайдбара и в шапке чата)
    await waitFor(() => expect(screen.getAllByText('Авто-заголовок')).toHaveLength(2))
    await waitFor(() => expect(dialoguesGets).toBe(2))
    expect(screen.queryByText('Новый диалог')).toBeNull()
  })
})

describe('ChatPanel — бейдж инициализации профиля в шапке (день 12)', () => {
  // Проба: ловит вкладку контекстной панели из состояния провайдера
  // (клик по бейджу должен установить её в «Профили»)
  let capturedTab = 'memory'
  function TabProbe() {
    const { state } = useStudio()
    capturedTab = state.contextTab
    return null
  }

  // loadAll-контракты + активный диалог d1 с заданным профилем
  function stubProfileFetch(profile: UserProfile) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Диалог', created: '2026-09-19', message_count: 0, profile }],
          })
        }
        if (url === '/api/dialogues/d1') {
          return jsonResponse({ dialogue: { messages: [] } })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
  }

  it('pending — бейдж «Профиль не заполнен», клик → вкладка «Профили»', async () => {
    stubProfileFetch({ status: 'pending', interview: false, name: '', role: '', tone: '', taboos: '' })
    capturedTab = 'memory'
    render(
      <StudioProvider>
        <TabProbe />
        <ChatPanel />
      </StudioProvider>,
    )
    const badge = await screen.findByRole('button', { name: 'Профиль не заполнен' })
    expect(badge).toHaveAttribute('title', 'Открыть вкладку «Профили»')
    expect(badge.className).toContain('pending')
    fireEvent.click(badge)
    await waitFor(() => expect(capturedTab).toBe('profile'))
  })

  it('declined — бейдж «Профиль отключён» (приглушённый)', async () => {
    stubProfileFetch({ status: 'declined', interview: false, name: '', role: '', tone: '', taboos: '' })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const badge = await screen.findByRole('button', { name: 'Профиль отключён' })
    expect(badge.className).toContain('declined')
  })

  it('active — бейджа в шапке нет', async () => {
    stubProfileFetch({ status: 'active', interview: false, name: 'Иван', role: '', tone: '', taboos: '' })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Диалог') // дождались загрузки активного диалога
    expect(screen.queryByRole('button', { name: /профиль/i })).toBeNull()
  })
})

// ── день 13b: режимы чат/задача, карточка процесса в ленте ──

const TASK13B: TaskState = {
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
  work_steps: [{ name: 'A', status: 'in_progress', output: null, ts: null }],
  context_snapshot: null,
  description: 'Сделать кнопку',
  instruction: '',
  retries: 0,
  error: null,
  updated: null,
}

// SSE-поток, который НЕ закрывается — задача «живая» (taskRunning true)
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

// loadAll-контракты + активный диалог d1 (messages + опциональный task)
function stubDialogueFetch(messages: unknown[], task?: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      if (url === '/api/dialogues') {
        return jsonResponse({
          active_id: 'd1',
          dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, ...(task ? { task } : {}) }],
        })
      }
      if (url === '/api/dialogues/d1') {
        return jsonResponse({ dialogue: { messages } })
      }
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
}

describe('ChatPanel — тумблер режимов чат/задача (день 13b)', () => {
  it('две кнопки «Чат»/«Задача», активная подсвечена; клик → chatMode + localStorage', async () => {
    stubDialogueFetch([])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const chatBtn = (await screen.findByRole('tab', { name: 'Чат' })) as HTMLButtonElement
    const taskBtn = screen.getByRole('tab', { name: 'Задача' }) as HTMLButtonElement
    expect(chatBtn.className).toContain('active')
    expect(taskBtn.className).not.toContain('active')
    fireEvent.click(taskBtn)
    await waitFor(() => expect(taskBtn.className).toContain('active'))
    expect(localStorage.getItem('studio.chatMode')).toBe('task')
    // инпут переключился в режим задачи
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Опишите задачу… (Enter — запустить пайплайн)')
  })

  it('taskMode + нет задачи — плейсхолдер «Опишите задачу…»', async () => {
    localStorage.setItem('studio.chatMode', 'task')
    stubDialogueFetch([])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Задача' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Опишите задачу… (Enter — запустить пайплайн)')
  })

  it('chatMode + нет задачи — плейсхолдер «Сообщение…»', async () => {
    stubDialogueFetch([])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Чат' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Сообщение… (Enter — отправить, Shift+Enter — перенос)')
  })

  it('taskMode + paused — «Инструкция для агентов…», кнопка «Сохранить»', async () => {
    localStorage.setItem('studio.chatMode', 'task')
    stubDialogueFetch([], { ...TASK13B, stage: 'paused' })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Задача' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Инструкция для агентов… (Enter — сохранить)')
    expect(ta.disabled).toBe(false)
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeTruthy()
  })

  it('taskMode + failed — инпут заблокирован, «Повтор» в карточке', async () => {
    localStorage.setItem('studio.chatMode', 'task')
    stubDialogueFetch([], { ...TASK13B, stage: 'failed', error: 'сбой' })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Задача' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Задача упала — «Повтор» в карточке')
    expect(ta.disabled).toBe(true)
  })

  it('taskMode + running — инпут и тумблер заблокированы, «Задача выполняется…»', async () => {
    localStorage.setItem('studio.chatMode', 'task')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/task/run') {
          return openSse([
            'data: {"type":"agent_spawned","stage":"execution","agent":"Исполнитель"}\n\n',
          ])
        }
        if (url.startsWith('/api/task?')) {
          return jsonResponse({ task: TASK13B })
        }
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, task: TASK13B }],
          })
        }
        if (url === '/api/dialogues/d1') {
          return jsonResponse({ dialogue: { messages: [] } })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <RunProbe />
        <ChatPanel />
      </StudioProvider>,
    )
    // живая карточка хвостом ленты: бейдж «Выполняется» = стрим открыт
    await screen.findByText('Выполняется')
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Задача выполняется…')
    expect(ta.disabled).toBe(true)
    expect((screen.getByRole('tab', { name: 'Чат' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('tab', { name: 'Задача' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('ChatPanel — карточка процесса в ленте (день 13b)', () => {
  it('user(task_id) → bubble + TaskCard; stage-сообщение не рендерится как bubble', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'Сделай кнопку', task_id: 't_1' },
      { role: 'assistant', content: 'вывод плана', model: 'm', task_id: 't_1', task_stage: 'planning' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    expect(await screen.findByText('Сделай кнопку')).toBeTruthy()
    // карточка: заголовок из user-маркера
    expect(await screen.findByText('Задача: Сделай кнопку')).toBeTruthy()
    // вывод стадии — НЕ bubble в ленте (он — в секции карточки)
    const bubbles = Array.from(container.querySelectorAll('.msg-text')).map((el) => el.textContent)
    expect(bubbles).not.toContain('вывод плана')
    expect(bubbles).toContain('Сделай кнопку')
    expect(container.querySelectorAll('.msg').length).toBe(1)
    // но в секции карточки вывод на месте
    expect(screen.getByText('вывод плана')).toBeTruthy()
  })

  it('две задачи (2 task_id) — 2 карточки', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'Задача первая', task_id: 't_1' },
      { role: 'assistant', content: 'план1', model: 'm', task_id: 't_1', task_stage: 'planning' },
      { role: 'user', content: 'Задача вторая', task_id: 't_2' },
      { role: 'assistant', content: 'план2', model: 'm', task_id: 't_2', task_stage: 'planning' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Задача: Задача первая')
    await screen.findByText('Задача: Задача вторая')
    expect(container.querySelectorAll('.task-card')).toHaveLength(2)
  })
})

describe('ChatPanel — отправка в режиме задачи (день 13b)', () => {
  it('taskMode без задачи — /api/task/start + /api/task/run (не chat)', async () => {
    const posted: { url: string; body?: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        const body = init?.body ? JSON.parse(String(init.body)) : undefined
        if (method === 'POST') posted.push({ url, body })
        if (method === 'POST' && url === '/api/task/start') {
          return jsonResponse({ task: { ...TASK13B, stage: 'planning' } })
        }
        if (method === 'POST' && url === '/api/task/run') {
          return new Response(
            'data: {"type":"task_done","answer":"готово"}\n\n',
            { headers: { 'Content-Type': 'text/event-stream' } },
          )
        }
        if (method === 'GET' && url === '/api/task?dialogue_id=d1') {
          return jsonResponse({ task: { ...TASK13B, stage: 'done' } })
        }
        if (method === 'GET' && url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0 }],
          })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          return jsonResponse({
            dialogue: {
              messages: [
                { role: 'user', content: 'Сделай кнопку', task_id: 't_9' },
                { role: 'assistant', content: 'готово', model: 'm', task_id: 't_9' },
              ],
            },
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const taskBtn = (await screen.findByRole('tab', { name: 'Задача' })) as HTMLButtonElement
    fireEvent.click(taskBtn)
    await waitFor(() => expect(taskBtn.className).toContain('active'))

    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'Сделай кнопку' } })
    fireEvent.click(screen.getByRole('button', { name: 'Запустить' }))

    await waitFor(() => expect(posted.some((p) => p.url === '/api/task/run')).toBe(true))
    expect(posted.some((p) => p.url === '/api/chat')).toBe(false)
    const start = posted.find((p) => p.url === '/api/task/start')
    expect(start?.body).toEqual({ dialogue_id: 'd1', description: 'Сделай кнопку' })
  })

  it('taskMode + paused — /api/task/instruction', async () => {
    const posted: { url: string; body?: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST') {
          posted.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
          if (url === '/api/task/instruction') {
            return jsonResponse({ task: { ...TASK13B, stage: 'paused', instruction: 'используй Kotlin' } })
          }
        }
        if (method === 'GET' && url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, task: { ...TASK13B, stage: 'paused' } }],
          })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          return jsonResponse({ dialogue: { messages: [] } })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    localStorage.setItem('studio.chatMode', 'task')
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Задача' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'используй Kotlin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() =>
      expect(posted).toContainEqual({ url: '/api/task/instruction', body: { dialogue_id: 'd1', text: 'используй Kotlin' } }),
    )
  })
})
