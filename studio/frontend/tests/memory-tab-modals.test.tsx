// MemoryTab — кнопки «Правила» и «Системный промпт»: модалка правил (GET /api/rules),
// модалка промпта (textarea из config, «Сохранить» → POST /api/config).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import MemoryTab from '../src/components/MemoryTab'

// Контракты GET /api/* для StudioProvider (loadAll при старте) + /api/rules
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
  '/api/rules': {
    system_prompt: 'Ты — ассистент «Студии».',
    memory_rule: 'Правило памяти: пункты памяти — устойчивые ограничения.',
    rule_active: true,
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

describe('MemoryTab — модалки «Правила» и «Системный промпт»', () => {
  it('«Правила» — системный промпт, статус «Активно» и текст memory_rule из /api/rules', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    render(
      <StudioProvider>
        <MemoryTab />
      </StudioProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Правила' }))
    // системный промпт из фикстуры /api/rules
    expect(await screen.findByText('Ты — ассистент «Студии».')).toBeInTheDocument()
    // rule_active: true → статус «Активно»
    expect(screen.getByText('Активно — в памяти есть записи')).toBeInTheDocument()
    // текст memory_rule
    expect(screen.getByText('Правило памяти: пункты памяти — устойчивые ограничения.')).toBeInTheDocument()
  })

  it('«Системный промпт» — textarea с value из config', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    const { container } = render(
      <StudioProvider>
        <MemoryTab />
      </StudioProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Системный промпт' }))
    // textarea модалки — единственный textarea на странице
    const ta = (await waitFor(() => container.querySelector('textarea'))) as HTMLTextAreaElement
    expect(ta.value).toBe('Ты — ассистент.')
  })

  it('«Системный промпт»: изменить текст + «Сохранить» → POST /api/config {system_prompt}', async () => {
    const posted: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        if (init?.method === 'POST' && url === '/api/config') {
          posted.push(JSON.parse(String(init.body)))
          return jsonResponse({
            model: 'qwen3.8-27b',
            temperature: 0.7,
            max_tokens: 1024,
            system_prompt: 'новый',
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    const { container } = render(
      <StudioProvider>
        <MemoryTab />
      </StudioProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Системный промпт' }))
    const ta = (await waitFor(() => container.querySelector('textarea'))) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: 'новый' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(posted).toEqual([{ system_prompt: 'новый' }]))
  })
})
