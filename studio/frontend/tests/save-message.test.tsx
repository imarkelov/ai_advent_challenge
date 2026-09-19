// ChatPanel → «в память»: модалка «Сохранить в память» — текст сообщения,
// key, select ровно из 2 слоёв (working/longterm, слоя «Диалог (ST)» нет),
// «Сохранить» → POST /api/memory/{layer} {key, value}.
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
  '/api/dialogues': {
    active_id: 'd1',
    dialogues: [{ id: 'd1', title: 'Т', created: '2026-01-01', message_count: 1 }],
  },
  '/api/dialogues/d1': {
    dialogue: { messages: [{ role: 'user', content: 'Напиши ТЗ' }] },
  },
  '/api/memory': {
    active_id: 'd1',
    dialogue: { message_count: 1, tokens_est: 5 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  // обязательно: иначе loadAll достанет {ok:true} и state.models станет undefined
  '/api/models': { models: [] },
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

describe('ChatPanel — «в память» (SaveMessageModal)', () => {
  it('кнопка «в память» открывает модалку с текстом сообщения; select ровно 2 опции', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    // сообщение диалога d1
    await screen.findByText('Напиши ТЗ')
    fireEvent.click(screen.getByTitle('Сохранить в память'))
    // textarea модалки содержит текст сообщения
    const ta = (await waitFor(() => container.querySelector('.modal textarea'))) as HTMLTextAreaElement
    expect(ta.value).toBe('Напиши ТЗ')
    // ровно 2 слоя: working / longterm (без «Диалог (ST)»)
    const sel = container.querySelector('.modal select') as HTMLSelectElement
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(['working', 'longterm'])
    expect(sel.value).toBe('working')
  })

  it('key + longterm + «Сохранить» → POST /api/memory/longterm {key, value}', async () => {
    const posted: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        if (init?.method === 'POST' && url.startsWith('/api/memory/')) {
          posted.push({ url, body: JSON.parse(String(init.body)) })
          return jsonResponse({ ok: true })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('Напиши ТЗ')
    fireEvent.click(screen.getByTitle('Сохранить в память'))
    await waitFor(() => expect(container.querySelector('.modal textarea')).toBeTruthy())
    const keyInput = container.querySelector('.modal input') as HTMLInputElement
    const sel = container.querySelector('.modal select') as HTMLSelectElement
    fireEvent.change(keyInput, { target: { value: 'ТЗ' } })
    fireEvent.change(sel, { target: { value: 'longterm' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() =>
      expect(posted).toEqual([{ url: '/api/memory/longterm', body: { key: 'ТЗ', value: 'Напиши ТЗ' } }]),
    )
  })
})
