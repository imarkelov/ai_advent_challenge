import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import RequestsTab from '../src/components/RequestsTab'

// Mock-запрос: содержит ИЗВЕСТНЫЙ параметр (temperature)
// и НЕИЗВЕСТНЫЙ (gpustack_route)
const LAST_REQUEST = {
  id: 7,
  ts: '2026-09-19T10:00:00',
  model: 'qwen3.8-27b',
  request: {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    messages: [{ role: 'user', content: 'Привет, как дела?' }],
    gpustack_route: 'local',
  },
  usage: { prompt_tokens: 42, completion_tokens: 17, total_tokens: 59 },
  error: null,
}

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
  '/api/requests': {
    requests: [
      { id: 7, ts: LAST_REQUEST.ts, model: LAST_REQUEST.model, total_tokens: 59, error: null },
    ],
  },
  '/api/requests/7': LAST_REQUEST,
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function mockApi(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input).replace(/^https?:\/\/[^/]+/, '')
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
}

beforeEach(() => {
  localStorage.clear()
})

describe('RequestsTab — аннотированный журнал запросов', () => {
  it('известный параметр — пояснение из глоссария, неизвестный — fallback', async () => {
    mockApi()
    render(
      <StudioProvider>
        <RequestsTab />
      </StudioProvider>,
    )
    // Известный параметр temperature: пояснение из PARAM_GLOSSARY
    expect(await screen.findByText(/случайность ответа/)).toBeInTheDocument()
    // Неизвестный параметр gpustack_route: точная fallback-пометка
    expect(screen.getByText('(доп. параметр, без описания)')).toBeInTheDocument()
    // Маркировка имён: known/unknown
    expect(screen.getByText('temperature').className).toContain('known')
    expect(screen.getByText('gpustack_route').className).toContain('unknown')
  })

  it('messages[] раскрывается как «role: первые N символов»', async () => {
    mockApi()
    render(
      <StudioProvider>
        <RequestsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText(/user: Привет, как дела\?/)).toBeInTheDocument()
  })

  it('usage-карточка показывает расход токенов с пояснениями', async () => {
    mockApi()
    render(
      <StudioProvider>
        <RequestsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText(/расход токенов/)).toBeInTheDocument()
    expect(screen.getByText('total_tokens').className).toContain('known')
    // пояснение для total_tokens из глоссария
    expect(screen.getByText(/итого за запрос/)).toBeInTheDocument()
  })

  it('журнал скрыт, когда тумблер выключен', async () => {
    mockApi()
    localStorage.setItem('studio.showRequests', '0')
    render(
      <StudioProvider>
        <RequestsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText('Журнал запросов скрыт')).toBeInTheDocument()
    expect(screen.queryByText('gpustack_route')).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })

  it('журнал запросов: строки #id · ts · total', async () => {
    mockApi()
    render(
      <StudioProvider>
        <RequestsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText('#7 · 2026-09-19T10:00:00 · 59')).toBeInTheDocument()
  })
})
