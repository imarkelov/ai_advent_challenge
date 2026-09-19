import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import ChatPanel from '../src/components/ChatPanel'

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
