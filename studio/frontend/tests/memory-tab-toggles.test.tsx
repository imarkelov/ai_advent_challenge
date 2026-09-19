// MemoryTab — тумблеры слоёв памяти (st/wm/lt) в заголовках карточек:
// 3 свитча по умолчанию включены, клик → POST /api/memory/toggles {layer, enabled},
// выключенный слой — класс `off` на карточке.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import MemoryTab from '../src/components/MemoryTab'

type Toggles = { st: boolean; wm: boolean; lt: boolean }

// Контракты GET /api/* для StudioProvider (loadAll при старте)
function fixtures(toggles?: Toggles): Record<string, unknown> {
  return {
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
      ...(toggles ? { toggles } : {}),
    },
    '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
    '/api/requests': { requests: [] },
  }
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

describe('MemoryTab — тумблеры слоёв памяти', () => {
  it('рендерит 3 свитча, все включены по умолчанию', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(fixtures()[normalizeUrl(input)] ?? { ok: true })),
    )
    render(
      <StudioProvider>
        <MemoryTab />
      </StudioProvider>,
    )
    const switches = await screen.findAllByRole('switch')
    expect(switches).toHaveLength(3)
    for (const s of switches) expect(s).toBeChecked()
  })

  it('клик по свитчу «Текущая задача» → POST /api/memory/toggles {layer:wm, enabled:false}', async () => {
    const posted: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        if (init?.method === 'POST' && url === '/api/memory/toggles') {
          posted.push(JSON.parse(String(init.body)))
          return jsonResponse({ toggles: { st: true, wm: false, lt: true } })
        }
        return jsonResponse(fixtures({ st: true, wm: true, lt: true })[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <MemoryTab />
      </StudioProvider>,
    )
    // порядок свитчей: Диалог (st), Текущая задача (wm), Долговременная (lt)
    const switches = await screen.findAllByRole('switch')
    fireEvent.click(switches[1])
    await waitFor(() => expect(posted).toEqual([{ layer: 'wm', enabled: false }]))
  })

  it('выключенный слой — карточка получает класс `off`', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(fixtures({ st: true, wm: false, lt: true })[normalizeUrl(input)] ?? { ok: true })),
    )
    const { container } = render(
      <StudioProvider>
        <MemoryTab />
      </StudioProvider>,
    )
    await screen.findAllByRole('switch')
    const card = (title: string) => container.querySelector(`section[title="${title}"]`)?.className
    await waitFor(() => expect(card('Working Memory')).toContain('off'))
    // остальные слои включены — без `off`
    expect(card('Short-Term Memory')).not.toContain('off')
    expect(card('Long-Term Memory')).not.toContain('off')
  })
})
