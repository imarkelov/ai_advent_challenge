import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
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
