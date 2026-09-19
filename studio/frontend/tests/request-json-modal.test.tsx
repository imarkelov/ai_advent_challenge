// RequestsTab → «Полный запрос (JSON)»: resizable-модалка с pretty-JSON
// (pre содержит '"model"' и id модели) и кнопка «Скопировать».
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import RequestsTab from '../src/components/RequestsTab'

// Mock-запрос: известный (temperature) + неизвестный (gpustack_route) параметр
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

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

beforeEach(() => {
  localStorage.clear()
})

describe('RequestsTab — «Полный запрос (JSON)»', () => {
  it('модалка с pretty-JSON и кнопкой «Скопировать»', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    const { container } = render(
      <StudioProvider>
        <RequestsTab />
      </StudioProvider>,
    )
    // карточка lastRequest (#7)
    await screen.findByText('#7 · 2026-09-19T10:00:00 · 59')
    fireEvent.click(screen.getByRole('button', { name: 'Полный запрос (JSON)' }))

    // pre содержит '"model"' и id модели
    const pre = await screen.findByText(/"model": "qwen3\.8-27b"/)
    expect(pre).toBeInTheDocument()
    expect(pre.className).toContain('json-pre')
    expect(pre.textContent).toContain('"model"')
    expect(pre.textContent).toContain('qwen3.8-27b')

    // resizable-модалка: grip + кнопка «Скопировать» в футере
    expect(container.querySelector('.modal-grip')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Скопировать' })).toBeInTheDocument()
  })
})
