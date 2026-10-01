// Токен-гейдж в шапке чата (TokenGauge): SVG-кольцо + компактное число
// (short form). По hover — TokenPopover (лимит + progress-бар, последний
// usage, сессионные токены, модель). Данные — из state (tokens/config),
// без новых API-вызовов.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import ChatPanel from '../src/components/ChatPanel'
import TokenGauge from '../src/components/TokenGauge'

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

// Контракты GET /api/* для StudioProvider (loadAll); /api/tokens — из параметра
const BASE_FIXTURES: Record<string, unknown> = {
  '/api/config': {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    max_tokens: 1024,
    system_prompt: 'Ты — ассистент.',
  },
  '/api/dialogues': { active_id: null, dialogues: [] },
  '/api/memory': {
    active_id: null,
    dialogue: { message_count: 8, tokens_est: 1234 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/requests': { requests: [] },
  '/api/models': {
    models: [
      { id: 'qwen3.8-27b', context_limit: 32768 },
      { id: 'deepseek-v4-flash', context_limit: 16384 },
    ],
  },
}

function stubFetch(tokens: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      if (url === '/api/tokens') return jsonResponse(tokens)
      return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
    }),
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('TokenGauge — в шапке чата', () => {
  it('рендерится в .chat-head-actions; число — short form, aria = pct', async () => {
    stubFetch({
      last: { prompt: 1000, completion: 200, reasoning: 120, total: 1240 },
      session: { prompt: 1000, completion: 200, total: 1200 },
      context_limit: 32768,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
    expect(gauge.closest('.chat-head-actions')).toBeTruthy()
    // ждём загрузку токенов: 1240 / 32768 = 0.0378 → aria-valuenow 4
    await waitFor(() => expect(gauge).toHaveAttribute('aria-valuenow', '4'))
    // компактное число рядом с кольцом: «1.2k»
    expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('1.2k')
    // без warn при малом заполнении; до hover поповера нет
    expect(container.querySelector('.token-gauge.warn')).toBeNull()
    expect(container.querySelector('.token-pop')).toBeNull()
  })

  it('hover → поповер открывается с данными (usage, сессия, лимит, модель); без hover — нет', async () => {
    stubFetch({
      last: { prompt: 1000, completion: 200, reasoning: 120, total: 1240 },
      session: { prompt: 5000, completion: 2500, total: 7500 },
      context_limit: 32768,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
    // ждём loadAll: число «1.2k» из загруженных токенов
    await waitFor(() => expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('1.2k'))
    // до hover поповера в DOM нет
    expect(container.querySelector('.token-pop')).toBeNull()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    expect(pop).toBeTruthy()
    // строки: последний запрос (total/лимит), usage, сессия, модель
    expect(screen.getByText('1240 / 32768')).toBeInTheDocument()
    expect(screen.getByText('prompt', { selector: '.pop-row span' })).toBeInTheDocument()
    expect(screen.getByText('7500')).toBeInTheDocument()
    await waitFor(() => expect(pop?.textContent).toContain('qwen3.8-27b'))
    // progress-бар поповера: 1240 / 32768 → aria-valuenow 4
    const bar = pop?.querySelector('[role="progressbar"]')
    expect(bar?.getAttribute('aria-valuenow')).toBe('4')
  })

  it('pct > 90% — warn-класс и aria; число ≥1000 — short form', async () => {
    stubFetch({
      last: { prompt: 28000, completion: 2000, reasoning: 0, total: 30000 },
      session: { prompt: 28000, completion: 2000, total: 30000 },
      context_limit: 32768,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
    // 30000 / 32768 = 0.9156 → warn + aria-valuenow 92
    await waitFor(() => expect(gauge).toHaveAttribute('aria-valuenow', '92'))
    expect(gauge).toHaveClass('token-gauge', 'warn')
    expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('30k')
    expect(container.querySelector('.token-gauge.warn .token-gauge-fill')).toBeTruthy()
  })

  it('данных нет (last: null) — «—», заполнение 0, без warn; поповер — «—»', async () => {
    stubFetch({
      last: null,
      session: { prompt: 0, completion: 0, total: 0 },
      context_limit: 32768,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
    expect(gauge).toHaveAttribute('aria-valuenow', '0')
    expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('—')
    expect(container.querySelector('.token-gauge.warn')).toBeNull()

    // поповер при пустом usage: «последний запрос» — «—», за сессию — 0
    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    expect(pop).toBeTruthy()
    const rows = pop?.querySelectorAll('.pop-row b')
    expect(rows?.[0]?.textContent).toBe('—')
    expect(rows?.[4]?.textContent).toBe('0')
  })

  it('сам по себе (вне ChatPanel) — гейдж и поповер при пустых данных', async () => {
    stubFetch({
      last: null,
      session: { prompt: 0, completion: 0, total: 0 },
      context_limit: 0,
    })
    render(
      <StudioProvider>
        <TokenGauge />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
    expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('—')

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    expect(pop).toBeTruthy()
    // лимит 0 — «последний запрос» «—»; модель — из загруженного конфига
    const rows = pop?.querySelectorAll('.pop-row b')
    expect(rows?.[0]?.textContent).toBe('—')
    await waitFor(() => expect(pop?.textContent).toContain('qwen3.8-27b'))
  })
})
