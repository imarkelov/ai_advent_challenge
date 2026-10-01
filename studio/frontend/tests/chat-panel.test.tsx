import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useEffect, useRef } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider, useStudio } from '../src/state'
import type { McpServer, McpTool, RagContext, TaskState, UserProfile } from '../src/api'
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

describe('ChatPanel — дропдаун модели в нижней строке ввода', () => {
  it('model-select — в .chat-input, в шапке (.chat-head) его нет', async () => {
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
    expect(select).toHaveClass('model-select')
    // дропдаун перенесён из шапки в строку ввода (правее кнопки отправки)
    expect(select.closest('.chat-input')).toBeTruthy()
    expect(select.closest('.chat-head')).toBeNull()
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

describe('ChatPanel — бейдж нарушения инварианта (день 14)', () => {
  it('SSE invariant_violation (до done) → бейдж с паттернами в шапке чата', async () => {
    // SSE-поток: дельта + invariant_violation + done (контракт дня 14)
    const sse =
      'data: {"type":"delta","text":"Отказ"}\n\n' +
      'data: {"type":"invariant_violation","patterns":["lang","format"]}\n\n' +
      'data: {"type":"done","answer":"Отказ","usage":null,"request_id":1}\n\n'
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/chat') {
          return new Response(sse, { headers: { 'Content-Type': 'text/event-stream' } })
        }
        if (method === 'GET' && url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Диалог', created: '2026-09-19', message_count: 2 }],
          })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          // день 19: done → reloadDialogue перечитывает авторитетные сообщения
          // (tool-карточки) — отдаём реальную ленту, не пустой массив
          return jsonResponse({
            dialogue: {
              messages: [
                { role: 'user', content: 'напиши по-английски списком' },
                { role: 'assistant', content: 'Отказ' },
              ],
            },
          })
        }
        if (method === 'GET' && url === '/api/requests/1') {
          return jsonResponse({ id: 1, ts: 't', model: 'qwen3.8-27b', request: {}, usage: null, error: null })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )

    // ждём, пока загрузится активный диалог (иначе placeholder «сначала создайте диалог»)
    const input = await screen.findByPlaceholderText(/Сообщение…/)
    fireEvent.change(input, { target: { value: 'напиши по-английски списком' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    // ответ застримился, бейдж нарушения появился в шапке с паттернами
    await screen.findByText('Отказ')
    const badge = await screen.findByText(/Нарушен инвариант/)
    expect(badge).toHaveTextContent('lang')
    expect(badge).toHaveTextContent('format')
  })

  it('без события invariant_violation бейджа нет', async () => {
    const sse =
      'data: {"type":"delta","text":"Ок"}\n\ndata: {"type":"done","answer":"Ок","usage":null,"request_id":1}\n\n'
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/chat') {
          return new Response(sse, { headers: { 'Content-Type': 'text/event-stream' } })
        }
        if (method === 'GET' && url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Диалог', created: '2026-09-19', message_count: 2 }],
          })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          // день 19: done → reloadDialogue перечитывает авторитетные сообщения
          // (tool-карточки) — отдаём реальную ленту, не пустой массив
          return jsonResponse({
            dialogue: {
              messages: [
                { role: 'user', content: 'привет' },
                { role: 'assistant', content: 'Ок' },
              ],
            },
          })
        }
        if (method === 'GET' && url === '/api/requests/1') {
          return jsonResponse({ id: 1, ts: 't', model: 'qwen3.8-27b', request: {}, usage: null, error: null })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const input = await screen.findByPlaceholderText(/Сообщение…/)
    fireEvent.change(input, { target: { value: 'привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await screen.findByText('Ок')
    expect(screen.queryByText(/Нарушен инвариант/)).toBeNull()
  })
})

describe('ChatPanel — бейдж инициализации профиля в шапке (день 12)', () => {
  // Проба: ловит вкладку + состояние overlay настроек из состояния провайдера
  // (клик по бейджу должен открыть overlay на вкладке «Профили»)
  let capturedTab = 'memory'
  let capturedSettingsOpen = false
  function TabProbe() {
    const { state } = useStudio()
    capturedTab = state.contextTab
    capturedSettingsOpen = state.settingsOpen
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

  it('pending — бейдж «Профиль не заполнен», клик → overlay настроек на вкладке «Профили»', async () => {
    stubProfileFetch({ status: 'pending', interview: false, name: '', role: '', tone: '', taboos: '' })
    capturedTab = 'memory'
    capturedSettingsOpen = false
    render(
      <StudioProvider>
        <TabProbe />
        <ChatPanel />
      </StudioProvider>,
    )
    const badge = await screen.findByRole('button', { name: 'Профиль не заполнен' })
    expect(badge).toHaveAttribute('title', 'Открыть настройки на вкладке «Профили»')
    expect(badge.className).toContain('pending')
    fireEvent.click(badge)
    await waitFor(() => expect(capturedSettingsOpen).toBe(true))
    expect(capturedTab).toBe('profile')
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
  it('две кнопки «Диалог»/«Проект», активная подсвечена; клик → chatMode + localStorage', async () => {
    stubDialogueFetch([])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const chatBtn = (await screen.findByRole('tab', { name: 'Диалог' })) as HTMLButtonElement
    const taskBtn = screen.getByRole('tab', { name: 'Проект' }) as HTMLButtonElement
    expect(chatBtn.className).toContain('active')
    expect(taskBtn.className).not.toContain('active')
    fireEvent.click(taskBtn)
    await waitFor(() => expect(taskBtn.className).toContain('active'))
    expect(localStorage.getItem('studio.chatMode')).toBe('task')
    // инпут переключился в режим проекта
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Опишите проект… (Enter — запустить пайплайн)')
  })

  it('taskMode + нет задачи — плейсхолдер «Опишите проект…»', async () => {
    localStorage.setItem('studio.chatMode', 'task')
    stubDialogueFetch([])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Проект' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.placeholder).toBe('Опишите проект… (Enter — запустить пайплайн)')
  })

  it('chatMode + нет задачи — плейсхолдер «Сообщение…»', async () => {
    stubDialogueFetch([])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByRole('tab', { name: 'Диалог' })
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
    await screen.findByRole('tab', { name: 'Проект' })
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
    await screen.findByRole('tab', { name: 'Проект' })
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
    expect((screen.getByRole('tab', { name: 'Диалог' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('tab', { name: 'Проект' }) as HTMLButtonElement).disabled).toBe(true)
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
    const taskBtn = (await screen.findByRole('tab', { name: 'Проект' })) as HTMLButtonElement
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
    await screen.findByRole('tab', { name: 'Проект' })
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'используй Kotlin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() =>
      expect(posted).toContainEqual({ url: '/api/task/instruction', body: { dialogue_id: 'd1', text: 'используй Kotlin' } }),
    )
  })
})

// ── AI Pulse: строка «модель думает» между отправкой и первым токеном ──

describe('ChatPanel — AI Pulse: строка «модель думает»', () => {
  // loadAll-контракты + активный диалог d1; POST /api/chat — открытый
  // SSE-поток (не закрывается → state.streaming остаётся true)
  function stubPulseFetch(chatResponse: Response) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/chat') return chatResponse
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0 }],
          })
        }
        if (url === '/api/dialogues/d1') return jsonResponse({ dialogue: { messages: [] } })
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
  }

  it('стрим открыт, в хвоте нет текста assistant — сфера + «Модель думает…» (каретки нет)', async () => {
    // поток без дельт: assistant-сообщение ещё не создано
    stubPulseFetch(openSse([]))
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    expect(await screen.findByText('Модель думает…')).toBeTruthy()
    expect(container.querySelector('.ai-pulse-row')).toBeTruthy()
    expect(container.querySelector('.ai-pulse-orb')).toBeTruthy()
    // текста ещё нет — каретка в .msg-text не рендерится
    expect(container.querySelector('.caret')).toBeNull()
  })

  it('стрим открыт, в хвоте текст assistant — пульс-строки нет, только каретка', async () => {
    // первый дельта пришёл, но поток не закрыт (стриминг продолжается)
    stubPulseFetch(openSse(['data: {"type":"delta","text":"Привет"}\n\n']))
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'привет' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    await screen.findByText('Привет')
    expect(screen.queryByText('Модель думает…')).toBeNull()
    expect(container.querySelector('.ai-pulse-orb')).toBeNull()
    // стрим ещё идёт — каретка на стримирующемся сообщении
    await waitFor(() => expect(container.querySelector('.caret')).toBeTruthy())
  })

  it('стрим не активен — пульс-строки нет', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'привет' },
      { role: 'assistant', content: 'ответ' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('ответ')
    expect(screen.queryByText('Модель думает…')).toBeNull()
    expect(container.querySelector('.ai-pulse-orb')).toBeNull()
  })
})

// ── день 16: tool-loop — MCP-команды «/» в режиме чата ──

const FC: McpServer = {
  id: 'mcp_fc', name: 'Firecrawl', type: 'stdio',
  command: ['npx', '-y', 'firecrawl-mcp'], url: '', env: {},
  enabled: true, status: 'connected', error: null, tools_count: 2,
}
const GIT: McpServer = {
  id: 'mcp_g', name: 'Git', type: 'stdio',
  command: ['npx', '-y', 'git-mcp'], url: '', env: {},
  enabled: true, status: 'idle', error: null, tools_count: 0,
}
const FC_TOOLS: McpTool[] = [
  {
    server: 'mcp_fc', name: 'firecrawl_search', description: 'Web-поиск',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Запрос' },
        count: { type: 'number' },
        verbose: { type: 'boolean' },
      },
      required: ['query'],
    },
  },
  { server: 'mcp_fc', name: 'firecrawl_scrape', description: 'Скрапинг страницы', input_schema: { type: 'object' } },
]
// Однословное имя сервера (дефолт дня 20, 1 сервер = 1 тул) — регресс парсера «/»
const WEATHER: McpServer = {
  id: 'mcp_weather', name: 'weather', type: 'stdio',
  command: ['python', 'weather.py'], url: '', env: {},
  enabled: true, status: 'connected', error: null, tools_count: 1,
}
const WEATHER_TOOLS: McpTool[] = [
  {
    server: 'mcp_weather', name: 'get_weather', description: 'Текущая погода по городу',
    input_schema: {
      type: 'object',
      properties: { city: { type: 'string', description: 'Город (дефолт: Самара)' } },
      required: [],
    },
  },
]

// loadAll-контракты + активный диалог d1 + MCP-серверы/инструменты.
// handler перехватывает URL'ы, на которые нет фиксированного ответа (null — дальше)
function stubMcpFetch(
  handler?: (url: string, method: string, init?: RequestInit) => Response | null,
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      const method = init?.method ?? 'GET'
      if (handler) {
        const r = handler(url, method, init)
        if (r) return r
      }
      if (url === '/api/mcp/servers') return jsonResponse({ servers: [FC, GIT] })
      if (url === '/api/mcp/tools') return jsonResponse({ tools: FC_TOOLS })
      if (url === '/api/dialogues') {
        return jsonResponse({
          active_id: 'd1',
          dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0 }],
        })
      }
      if (url === '/api/dialogues/d1') return jsonResponse({ dialogue: { messages: [] } })
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
}

describe('ChatPanel — автодополнение MCP-команд «/» (день 16, tool-loop)', () => {
  it('«/» — дропдаун включённых серверов (tier-1)', async () => {
    stubMcpFetch()
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/' } })
    expect(await screen.findByRole('listbox')).toBeTruthy()
    expect(screen.getByText('Firecrawl')).toBeTruthy()
    expect(screen.getByText('Git')).toBeTruthy()
  })

  it('«/firecr» — только совпадающий сервер (Git скрыт)', async () => {
    stubMcpFetch()
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/firecr' } })
    await screen.findByRole('listbox')
    expect(screen.getByText('Firecrawl')).toBeTruthy()
    expect(screen.queryByText('Git')).toBeNull()
  })

  it('«/Git x» (tier-2) — сервер не подключён: строка-подсказка, выбор недоступен', async () => {
    stubMcpFetch()
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/Git x' } })
    await screen.findByRole('listbox')
    expect(await screen.findByText('Сервер не подключён — сначала подключите в настройках')).toBeTruthy()
    // в дропдауне нет выбираемых строк (option-роли у <select> модели не счи
    // там — ищем только внутри .ac-dropdown)
    expect(document.querySelectorAll('.ac-dropdown [role="option"]').length).toBe(0)
  })

  it('префикс инструмента (tier-2) — список; Enter — модалка формы', async () => {
    stubMcpFetch()
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/Firecrawl f' } })
    await screen.findByRole('listbox')
    expect(screen.getByText('firecrawl_search')).toBeTruthy()
    expect(screen.getByText('firecrawl_scrape')).toBeTruthy()
    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(await screen.findByText('MCP Firecrawl/firecrawl_search')).toBeTruthy()
  })

  it('«Вызвать» — POST .../tools/{tool} с аргументами формы (string/number/bool)', async () => {
    const posted: { url: string; body: unknown }[] = []
    stubMcpFetch((url, method, init) => {
      if (method === 'POST' && url === '/api/mcp/servers/mcp_fc/tools/firecrawl_search') {
        posted.push({ url, body: JSON.parse(String(init?.body)) })
        return jsonResponse({ ok: true })
      }
      return null
    })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/Firecrawl firecrawl_search' } })
    fireEvent.keyDown(ta, { key: 'Enter' })
    // модалка: заголовок + описание инструмента (описание дублируется в
    // открытом дропдауне — ищем по вхождению, а не единственности)
    await screen.findByText('MCP Firecrawl/firecrawl_search')
    expect(screen.getAllByText('Web-поиск').length).toBeGreaterThanOrEqual(1)
    // поля из input_schema: string→text, number→number, boolean→checkbox
    // доступное имя лейбла = «query * Запрос» (hint-описание внутри label)
    const query = screen.getByLabelText(/query \*/) as HTMLInputElement
    const count = screen.getByLabelText('count') as HTMLInputElement
    const verbose = screen.getByLabelText('verbose') as HTMLInputElement
    expect(query.type).toBe('text')
    expect(count.type).toBe('number')
    expect(verbose.type).toBe('checkbox')
    fireEvent.change(query, { target: { value: 'hello' } })
    fireEvent.change(count, { target: { value: '3' } })
    fireEvent.click(verbose)
    fireEvent.click(screen.getByRole('button', { name: 'Вызвать' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0].url).toBe('/api/mcp/servers/mcp_fc/tools/firecrawl_search')
    expect(posted[0].body).toEqual({
      dialogue_id: 'd1',
      arguments: { query: 'hello', count: 3, verbose: true },
    })
    // успех — модалка закрыта (результат придёт в ленте)
    await waitFor(() => expect(screen.queryByText('MCP Firecrawl/firecrawl_search')).toBeNull())
  })

  it('Enter при открытом дропдауне — сообщение НЕ отправляется (выбор строки)', async () => {
    let chatSent = false
    stubMcpFetch((url, method) => {
      if (method === 'POST' && url === '/api/chat') chatSent = true
      return null
    })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '/Firecrawl f' } })
    await screen.findByRole('listbox')
    fireEvent.keyDown(ta, { key: 'Enter' })
    // Enter выбрал подсвеченный инструмент: draft дополнен, модалка открыта
    expect(ta.value).toBe('/Firecrawl firecrawl_search')
    expect(screen.getByText('MCP Firecrawl/firecrawl_search')).toBeTruthy()
    expect(chatSent).toBe(false)
  })

  // Однословное имя сервера (дефолт дня 20 «weather»): парсер должен
  // разобрать «/weather tool» как сервер + тул («/weather» без тула — tier-1)
  it('однословное имя «/weather get» — сервер резолвится, список тулов (tier-2)', async () => {
    stubMcpFetch((url) => {
      if (url === '/api/mcp/servers') return jsonResponse({ servers: [FC, WEATHER] })
      if (url === '/api/mcp/tools') return jsonResponse({ tools: [...FC_TOOLS, ...WEATHER_TOOLS] })
      return null
    })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    // без тула — tier-1: строка сервера, не тул
    fireEvent.change(ta, { target: { value: '/weather' } })
    await screen.findByRole('listbox')
    expect(screen.queryByText('get_weather')).toBeNull()
    // тул после имени — tier-2: список тулов по префиксу
    fireEvent.change(ta, { target: { value: '/weather get' } })
    expect(await screen.findByText('get_weather')).toBeTruthy()
    // префикс тула — фильтрует: чужой тул не найден, дропдаун закрыт
    fireEvent.change(ta, { target: { value: '/weather create' } })
    expect(screen.queryByText('get_weather')).toBeNull()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('однословное имя: сервер из автодополнения, тул — модалка формы', async () => {
    stubMcpFetch((url) => {
      if (url === '/api/mcp/servers') return jsonResponse({ servers: [FC, WEATHER] })
      if (url === '/api/mcp/tools') return jsonResponse({ tools: [...FC_TOOLS, ...WEATHER_TOOLS] })
      return null
    })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const ta = (await screen.findByPlaceholderText(/Сообщение…/)) as HTMLTextAreaElement
    // tier-1: префикс «wea» — в списке только сервер weather
    fireEvent.change(ta, { target: { value: '/wea' } })
    await screen.findByRole('listbox')
    expect(screen.getByText('weather')).toBeTruthy()
    // Enter — выбор сервера, draft дополнен полным именем
    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(ta.value).toBe('/weather ')
    // tier-2: тул выбранной — модалка формы открывается
    fireEvent.change(ta, { target: { value: '/weather get_weather' } })
    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(await screen.findByText('MCP weather/get_weather')).toBeTruthy()
    const city = screen.getByLabelText(/city/) as HTMLInputElement
    expect(city.type).toBe('text')
  })
})

describe('ChatPanel — карточка результата MCP (день 16, tool-loop)', () => {
  it('сообщение с mcp_tool — .mcp-card (chip + вывод), без «в память»', async () => {
    stubMcpFetch((url, method) => {
      if (method === 'GET' && url === '/api/dialogues/d1') {
        return jsonResponse({
          dialogue: {
            messages: [
              { role: 'system', content: 'Результат: 42', mcp_tool: { server: 'mcp_fc', tool: 'firecrawl_search' } },
            ],
          },
        })
      }
      return null
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    expect(await screen.findByText('Результат: 42')).toBeTruthy()
    const card = container.querySelector('.mcp-card')
    expect(card).toBeTruthy()
    expect(card).toHaveTextContent('MCP Firecrawl/firecrawl_search')
    // не обычный bubble: в ленте нет .msg и кнопки «Сохранить в память»
    expect(container.querySelectorAll('.msg').length).toBe(0)
    expect(screen.queryByTitle('Сохранить в память')).toBeNull()
  })
})

describe('ChatPanel — «Шаги агента»: сворачиваемая группа tool-карточек (LLM tool-loop)', () => {
  it('группа свёрнута по умолчанию; шапка — суммарные ≈ N tok; ряды шагов — каждый со своим ≈ N tok и своим сворачиванием', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'Каков статус задачи TASK-42?' },
      {
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'task_get__get_task_details', arguments: '{"task_id": "TASK-42"}' } }],
      },
      { role: 'tool', content: 'TOOL-RESULT-123', tool_call_id: 'call_1', name: 'task_get__get_task_details' },
      { role: 'assistant', content: 'Финальный ответ: in_progress', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    // финальный assistant-ответ в ленте (обычный bubble)
    expect(await screen.findByText('Финальный ответ: in_progress')).toBeTruthy()
    // группа свёрнута: ленте цикла (подпись узла 🔧) + заголовок-кнопка
    // с суммарными токенами (аргументы 22 символа ≈ 10, результат 15 ≈ 7 → ≈ 17)
    const header = await screen.findByRole('button', { name: /🧩 Шаги агента/ })
    expect(header).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('task_get · get_task_details')).toBeTruthy()
    expect(screen.getAllByText(/≈ \d+ tok/)).toHaveLength(1)
    expect(screen.getByText('≈ 17 tok')).toBeTruthy()
    expect(container.querySelector('.step-row')).toBeNull()
    expect(screen.queryByText('🔧 task_get__get_task_details')).toBeNull()
    expect(screen.queryByText('TOOL-RESULT-123')).toBeNull()
    expect(screen.queryByText('{"task_id": "TASK-42"}')).toBeNull()
    // раскрытие группы: ряды шагов (chip + свои ≈ N tok) видны,
    // а payload в <pre> — НЕТ (ряды свёрнуты по умолчанию)
    fireEvent.click(header)
    await waitFor(() => expect(header).toHaveAttribute('aria-expanded', 'true'))
    const callRow = screen.getByRole('button', { name: /🔧 task_get__get_task_details/ })
    const resRow = screen.getByRole('button', { name: /↳ task_get__get_task_details/ })
    expect(callRow).toHaveAttribute('aria-expanded', 'false')
    expect(resRow).toHaveAttribute('aria-expanded', 'false')
    // свои оценки: аргументы ≈ 10, результат ≈ 7 (+ сумма 17 в заголовке)
    expect(screen.getAllByText(/≈ \d+ tok/)).toHaveLength(3)
    expect(screen.getByText('≈ 10 tok')).toBeTruthy()
    expect(screen.getByText('≈ 7 tok')).toBeTruthy()
    expect(container.querySelector('.tool-card-pre')).toBeNull()
    expect(screen.queryByText('{"task_id": "TASK-42"}')).toBeNull()
    // клик по ряду вызова — его payload виден; ряд результата остаётся свёрнут
    fireEvent.click(callRow)
    await waitFor(() => expect(callRow).toHaveAttribute('aria-expanded', 'true'))
    expect(screen.getByText('{"task_id": "TASK-42"}')).toBeTruthy()
    expect(screen.queryByText('TOOL-RESULT-123')).toBeNull()
    expect(resRow).toHaveAttribute('aria-expanded', 'false')
    // повторный клик по заголовку группы — всё сворачивается
    fireEvent.click(header)
    await waitFor(() => expect(container.querySelector('.step-row')).toBeNull())
    // вызов/результат — не «модель»-пузырями: .msg ровно 2 (user + финальный)
    const texts = Array.from(container.querySelectorAll('.msg-text')).map((el) => el.textContent)
    expect(texts).toContain('Финальный ответ: in_progress')
    expect(texts.some((t) => t && t.includes('TOOL-RESULT-123'))).toBe(false)
    expect(container.querySelectorAll('.msg').length).toBe(2)
  })

  it('внутри группы: заметка assistant (не сворачивается) + ряды шагов: chip, <pre> по клику на ряд', async () => {
    stubDialogueFetch([
      {
        role: 'assistant',
        content: 'Ищу данные по запросу',
        tool_calls: [{ id: 'c1', type: 'function', function: { name: 'search', arguments: '{"query":"ИИ"}' } }],
      },
      { role: 'tool', content: 'результат поиска', tool_call_id: 'c1', name: 'search' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const header = await screen.findByRole('button', { name: /🧩 Шаги агента/ })
    // группа свёрнута: заметки и JSON аргументов не видно;
    // подпись узла search в ленте цикла — видна
    expect(screen.queryByText('Ищу данные по запросу')).toBeNull()
    expect(screen.queryByText('{"query":"ИИ"}')).toBeNull()
    expect(screen.getByText('search')).toBeTruthy()
    // раскрытие группы: заметка видна сразу (не сворачивается),
    // ряды шагов свёрнуты — аргументы ещё не видны
    fireEvent.click(header)
    const note = await screen.findByText('Ищу данные по запросу')
    expect(note).toBeTruthy()
    expect(container.querySelector('.tool-card-note')).toBeTruthy()
    const callRow = screen.getByRole('button', { name: /🔧 search/ })
    const resRow = screen.getByRole('button', { name: /↳ search/ })
    expect(callRow).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('{"query":"ИИ"}')).toBeNull()
    // клик по ряду вызова — аргументы в <pre>
    fireEvent.click(callRow)
    const args = await screen.findByText('{"query":"ИИ"}')
    expect(args.closest('pre.tool-card-pre')).toBeTruthy()
    // клик по ряду результата — его вывод
    fireEvent.click(resRow)
    const out = await screen.findByText('результат поиска')
    expect(out.closest('pre.tool-card-pre')).toBeTruthy()
  })

  it('лента: счётчик вызовов «· 3»; подписи — в порядке вызовов (повторы видны)', async () => {
    stubDialogueFetch([
      { role: 'assistant', content: '', tool_calls: [{ id: 'a', type: 'function', function: { name: 'search', arguments: '{"query":"Самара"}' } }] },
      { role: 'tool', content: 'r1', tool_call_id: 'a', name: 'search' },
      { role: 'assistant', content: '', tool_calls: [{ id: 'b', type: 'function', function: { name: 'search', arguments: '{"query":"погода"}' } }] },
      { role: 'tool', content: 'r2', tool_call_id: 'b', name: 'search' },
      { role: 'assistant', content: '', tool_calls: [{ id: 'c', type: 'function', function: { name: 'summarize', arguments: '{"text":"…"}' } }] },
      { role: 'tool', content: 'r3', tool_call_id: 'c', name: 'summarize' },
      { role: 'assistant', content: 'Готово', model: 'qwen3.8-27b' },
    ])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    // три вызова (search, search, summarize) — одна группа, счётчик «· 3»
    expect(await screen.findByRole('button', { name: /🧩 Шаги агента · 3/ })).toBeTruthy()
    // лента: подписи в порядке вызовов — повтор (search) виден как отдельный узел
    const labels = screen.getAllByText(/^(search|summarize)$/)
    expect(labels.map((b) => b.textContent)).toEqual(['search', 'search', 'summarize'])
    // все 3 узла тулов со статусом ok (результаты пришли); 🧠 — 3 итерации
    // (каждый вызов — своё assistant-сообщение с tool_calls)
    const flow = labels[0].closest('.agent-flow') as HTMLElement
    expect(flow.querySelectorAll('.agent-flow-node--tool.agent-flow-node--ok')).toHaveLength(3)
    expect(flow.querySelectorAll('.agent-flow-node--brain')).toHaveLength(3)
    // суммарная оценка токенов группы (аргументы ≈ 8+8+5, результаты ≈ 1+1+1)
    expect(screen.getByText('≈ 24 tok')).toBeTruthy()
    expect(screen.getByText('Готово')).toBeTruthy()
  })

  it('бейдж без префикса — как есть; несколько тулов — уникальные бейджи', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'проверь' },
      {
        role: 'assistant',
        content: '',
        tool_calls: [
          { id: 'c1', type: 'function', function: { name: 'plain_tool', arguments: '{}' } },
          { id: 'c2', type: 'function', function: { name: 'digest_search__search', arguments: '{"query":"x"}' } },
        ],
      },
      { role: 'tool', content: 'r1', tool_call_id: 'c1', name: 'plain_tool' },
      { role: 'tool', content: 'r2', tool_call_id: 'c2', name: 'digest_search__search' },
      { role: 'assistant', content: 'готово', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('готово')
    // лента: имя без префикса «__» — как есть; с префиксом — «server · tool»
    expect(screen.getByText('plain_tool')).toBeTruthy()
    expect(screen.getByText('digest_search · search')).toBeTruthy()
    // ровно 2 узла тулов (повтор одного тула был бы отдельным узлом)
    expect(container.querySelectorAll('.agent-flow-node--tool')).toHaveLength(2)
  })
})

describe('ChatPanel — кнопка MCP в шапке чата (день 16)', () => {
  it('рендерит кнопку (aria-label="MCP"); клик → open-mcp (mcpOpen=true)', async () => {
    stubMcpFetch()
    let mcpOpen: boolean | null = null
    function Probe() {
      const { state } = useStudio()
      mcpOpen = state.mcpOpen
      return null
    }
    render(
      <StudioProvider>
        <ChatPanel />
        <Probe />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'MCP' })
    expect(btn).toHaveClass('mcp-toggle')
    // шапка чата — верхний правый угол экрана
    expect(btn.closest('.chat-head')).toBeTruthy()
    expect(mcpOpen).toBe(false)
    fireEvent.click(btn)
    await waitFor(() => expect(mcpOpen).toBe(true))
    // повторный клик — toggle (close-mcp)
    fireEvent.click(btn)
    await waitFor(() => expect(mcpOpen).toBe(false))
  })
})

describe('ChatPanel — «Шаги агента»: лента цикла агента (.agent-flow, день 20, уточнение)', () => {
  it('лента: 🧠 + 🔧 в порядке вызовов; результат пришёл — статус ok (✓)', async () => {
    stubDialogueFetch([
      {
        role: 'assistant',
        content: '',
        tool_calls: [
          { id: 'c1', type: 'function', function: { name: 'alpha__x', arguments: '{}' } },
          { id: 'c2', type: 'function', function: { name: 'beta__y', arguments: '{}' } },
        ],
      },
      { role: 'tool', content: 'r1', tool_call_id: 'c1', name: 'alpha__x' },
      { role: 'tool', content: 'r2', tool_call_id: 'c2', name: 'beta__y' },
      { role: 'assistant', content: 'готово', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('готово')
    // лента видна без раскрытия группы: узлы в хронологическом порядке
    const header = screen.getByRole('button', { name: /🧩 Шаги агента/ })
    expect(header).toHaveAttribute('aria-expanded', 'false')
    const flow = container.querySelector('.agent-flow')
    expect(flow).toBeTruthy()
    const labels = Array.from(flow.querySelectorAll('.agent-flow-label')).map((el) => el.textContent)
    expect(labels).toEqual(['Решение 1', 'alpha · x', 'beta · y'])
    // оба узла тулов — ok (✓), без ✗ и спиннера
    const tools = flow.querySelectorAll('.agent-flow-node--tool')
    expect(tools).toHaveLength(2)
    tools.forEach((t) => {
      expect(t).toHaveClass('agent-flow-node--ok')
      expect(t.textContent).toContain('✓')
      expect(t.textContent).not.toContain('✗')
      expect(t.querySelector('.agent-flow-spin')).toBeNull()
    })
    // 🧠 — нейтральный (стрим не активен: live=false)
    const brain = flow.querySelector('.agent-flow-node--brain')
    expect(brain).not.toHaveClass('agent-flow-node--live')
  })

  it('лента: вызов без результата — узел активен (спиннер, без ✓)', async () => {
    stubDialogueFetch([
      {
        role: 'assistant',
        content: '',
        tool_calls: [
          { id: 'c1', type: 'function', function: { name: 'weather__get_weather', arguments: '{}' } },
          { id: 'c2', type: 'function', function: { name: 'news__get_news', arguments: '{}' } },
        ],
      },
      // результата c2 нет (второй вызов ещё «в полёте»)
      { role: 'tool', content: 'r1', tool_call_id: 'c1', name: 'weather__get_weather' },
      { role: 'assistant', content: 'ок', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('ок')
    const flow = container.querySelector('.agent-flow')
    const tools = flow.querySelectorAll('.agent-flow-node--tool')
    expect(tools).toHaveLength(2)
    // первый вызов — результат есть (ok), второй — результата нет (active)
    expect(tools[0]).toHaveClass('agent-flow-node--ok')
    expect(tools[1]).toHaveClass('agent-flow-node--active')
    expect(tools[1].querySelector('.agent-flow-spin')).toBeTruthy()
    expect(tools[1].textContent).not.toContain('✓')
    expect(tools[1].textContent).not.toContain('✗')
  })

  it('лента: "error" в содержимом результата — узел error (✗)', async () => {
    stubDialogueFetch([
      {
        role: 'assistant',
        content: '',
        tool_calls: [
          { id: 'c1', type: 'function', function: { name: 'task_get__get_task_details', arguments: '{"task_id": "NOPE"}' } },
        ],
      },
      { role: 'tool', content: '{"error": "Задача не найдена: NOPE"}', tool_call_id: 'c1', name: 'task_get__get_task_details' },
      { role: 'assistant', content: 'ок', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('ок')
    const flow = container.querySelector('.agent-flow')
    const tool = flow.querySelector('.agent-flow-node--tool')
    expect(tool).toHaveClass('agent-flow-node--error')
    expect(tool.textContent).toContain('✗')
    expect(tool.textContent).not.toContain('✓')
    expect(tool.querySelector('.agent-flow-spin')).toBeNull()
  })

  it('лента: тул без префикса «__» — подпись как есть', async () => {
    stubDialogueFetch([
      {
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'c1', type: 'function', function: { name: 'plain_tool', arguments: '{}' } }],
      },
      { role: 'tool', content: 'r', tool_call_id: 'c1', name: 'plain_tool' },
      { role: 'assistant', content: 'ок', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('ок')
    const flow = container.querySelector('.agent-flow')
    const label = flow.querySelector('.agent-flow-node--tool .agent-flow-label')
    expect(label).toBeTruthy()
    expect(label.textContent).toBe('plain_tool')
  })

  it('лента: один тул в двух итерациях — два 🔧 с одной подписью и два 🧠', async () => {
    stubDialogueFetch([
      { role: 'assistant', content: '', tool_calls: [{ id: 'a', type: 'function', function: { name: 'digest_search__search', arguments: '{"query":"A"}' } }] },
      { role: 'tool', content: 'r1', tool_call_id: 'a', name: 'digest_search__search' },
      { role: 'assistant', content: '', tool_calls: [{ id: 'b', type: 'function', function: { name: 'digest_search__search', arguments: '{"query":"B"}' } }] },
      { role: 'tool', content: 'r2', tool_call_id: 'b', name: 'digest_search__search' },
      { role: 'assistant', content: 'готово', model: 'qwen3.8-27b' },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('готово')
    const flow = container.querySelector('.agent-flow')
    // 🧠: две итерации — «Решение 1» и «Решение 2»
    const brains = Array.from(flow.querySelectorAll('.agent-flow-node--brain .agent-flow-label'))
      .map((el) => el.textContent)
    expect(brains).toEqual(['Решение 1', 'Решение 2'])
    // 🔧: два узла с ОДНОЙ и той же подписью, оба ok
    const tools = flow.querySelectorAll('.agent-flow-node--tool')
    expect(tools).toHaveLength(2)
    tools.forEach((t) => {
      expect(t.querySelector('.agent-flow-label').textContent).toBe('digest_search · search')
      expect(t).toHaveClass('agent-flow-node--ok')
    })
  })

  it('live: последний 🧠 без результата после — pulse (стрим в полёте)', async () => {
    // SSE-стрим, который НЕ закрывается: ход «в полёте» (state.streaming=true),
    // tool-сообщения в ленте — из истории (группа заканчивается решением модели)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/chat') {
          return new Response(
            new ReadableStream<Uint8Array>({ start() { /* кадров нет, close нет */ } }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          )
        }
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0 }],
          })
        }
        if (url === '/api/dialogues/d1') {
          return jsonResponse({
            dialogue: {
              messages: [
                {
                  role: 'assistant',
                  content: '',
                  tool_calls: [{ id: 'c1', type: 'function', function: { name: 'weather__get_weather', arguments: '{}' } }],
                },
              ],
            },
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    // до отправки: 🧠 без пульса (стрим не активен), 🔧 — активен (нет результата)
    await screen.findByRole('button', { name: /🧩 Шаги агента/ })
    const brainOf = () => container.querySelector('.agent-flow-node--brain') as HTMLElement
    expect(brainOf()).not.toHaveClass('agent-flow-node--live')
    // отправляем сообщение — стрим остаётся открытым (streaming=true);
    // группа — последняя в ленте и заканчивается решением без результата
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'статус?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(brainOf()).toHaveClass('agent-flow-node--live'))
    // 🔧 при этом остаётся активным (результат ещё не пришёл)
    expect(container.querySelector('.agent-flow-node--tool')).toHaveClass('agent-flow-node--active')
  })
})

// ── День 21: инспектор RAG-контекста (реранкер) под assistant-сообщением ──

const RAG_CTX: RagContext = {
  recall_total: 50,
  reranked: true,
  chunks: [
    { rank: 1, file: 'a.md', section: 'Секция', score: 0.94, stage1_rank: 14, reranked: true, text: 'Текст чанка 1' },
    { rank: 2, file: 'b.md', section: '', score: 0.62, stage1_rank: 3, reranked: true, text: 'Текст чанка 2' },
    { rank: 3, file: 'c.md', section: 'Хвост', score: 0.41, stage1_rank: 42, reranked: true, text: 'Текст чанка 3' },
  ],
}

// Чанки для проверки цветов точек: green/yellow/red (reranked) + neutral (без реранка)
const RAG_DOT: RagContext = {
  recall_total: 10,
  reranked: false,
  chunks: [
    { rank: 1, file: 'a.md', section: '', score: 0.9, stage1_rank: null, reranked: true, text: 'g' },
    { rank: 2, file: 'b.md', section: '', score: 0.6, stage1_rank: null, reranked: true, text: 'y' },
    { rank: 3, file: 'c.md', section: '', score: 0.3, stage1_rank: null, reranked: true, text: 'r' },
    { rank: 4, file: 'd.md', section: '', score: 0.9, stage1_rank: null, reranked: false, text: 'n' },
  ],
}

describe('ChatPanel — инспектор RAG-контекста (день 21, реранкер)', () => {
  it('assistant с rag_context — свёрнутый toggle «(3 чанков из 50)», чанки скрыты', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос' },
      { role: 'assistant', content: 'Ответ с RAG', model: 'm', rag_context: RAG_CTX },
    ])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Ответ с RAG')
    const toggle = screen.getByRole('button', { name: /Показать извлечённый контекст RAG \(3 чанков из 50\)/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    // свёрнут: чанки не видны
    expect(screen.queryByText('Текст чанка 1')).toBeNull()
    expect(screen.queryByText(/\[Чанк #1\]/)).toBeNull()
  })

  it('клик по toggle — разворачивает: строка чанка #1 со score + (Reranked из #14)', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос' },
      { role: 'assistant', content: 'Ответ с RAG', model: 'm', rag_context: RAG_CTX },
    ])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Ответ с RAG')
    const toggle = screen.getByRole('button', { name: /Показать извлечённый контекст RAG/ })
    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'true'))
    expect(await screen.findByText(/\[Чанк #1\] Score: 0\.94 \(Reranked из #14\)/)).toBeInTheDocument()
    expect(screen.getByText('a.md · Секция')).toBeInTheDocument()
    expect(screen.getByText('Текст чанка 1')).toBeInTheDocument()
    // toggle переключился на «▲ Скрыть контекст RAG»
    expect(screen.getByRole('button', { name: /Скрыть контекст RAG/ })).toBeInTheDocument()
  })

  it('🚨 — только у чанка с stage1_rank > 20', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос' },
      { role: 'assistant', content: 'Ответ с RAG', model: 'm', rag_context: RAG_CTX },
    ])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Ответ с RAG')
    fireEvent.click(screen.getByRole('button', { name: /Показать извлечённый контекст RAG/ }))
    await screen.findByText('Текст чанка 1')
    // chunk #3 (stage1_rank 42 > 20) — сигнал; #1 (14) и #2 (3) — нет
    expect(screen.getByTitle('Реранкер поднял с позиции этапа 1 #42')).toHaveTextContent('🚨')
    expect(screen.queryByTitle('Реранкер поднял с позиции этапа 1 #14')).toBeNull()
    expect(screen.queryByTitle('Реранкер поднял с позиции этапа 1 #3')).toBeNull()
    // ровно один сигнал в развёрнутом списке
    expect(screen.getAllByText('🚨')).toHaveLength(1)
  })

  it('сообщение без rag_context — инспектора (toggle) нет', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'привет' },
      { role: 'assistant', content: 'обычный ответ', model: 'm' },
    ])
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('обычный ответ')
    expect(screen.queryByRole('button', { name: /контекст RAG/ })).toBeNull()
  })

  it('класс точки: green ≥0.8, yellow 0.5..0.8, red <0.5, neutral без реранка', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос' },
      { role: 'assistant', content: 'Ответ с RAG', model: 'm', rag_context: RAG_DOT },
    ])
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Ответ с RAG')
    fireEvent.click(screen.getByRole('button', { name: /Показать извлечённый контекст RAG/ }))
    await screen.findByText('g')
    const dots = Array.from(container.querySelectorAll('.rag-dot')).map((d) => d.className)
    expect(dots).toEqual([
      'rag-dot rag-dot-green',
      'rag-dot rag-dot-yellow',
      'rag-dot rag-dot-red',
      'rag-dot rag-dot-neutral',
    ])
  })
})
