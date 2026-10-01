// Токен-гейдж в шапке чата (TokenGauge): SVG-кольцо + компактное число
// (short form) + tooltip с полной токен-статистикой (по :hover, чистый
// CSS — в DOM строки tooltip всегда, видимость — на CSS). Данные — из
// state (memory/tokens/config), без новых API-вызовов.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
    // без warn при малом заполнении
    expect(container.querySelector('.token-gauge.warn')).toBeNull()
  })

  it('tooltip в DOM: строки диалога/запроса/сессии/лимита/модели', async () => {
    stubFetch({
      last: { prompt: 1000, completion: 200, reasoning: 120, total: 1240 },
      session: { prompt: 5000, completion: 2500, total: 7500 },
      context_limit: 32768,
    })
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
    const tip = gauge.querySelector('.token-gauge-tip')
    expect(tip).toBeTruthy()
    // строки tooltip: подписи и значения текущего диалога (ждём loadAll)
    expect(await screen.findByText('Диалог (оценка)')).toBeInTheDocument()
    expect(await screen.findByText('1234 · 8 сообщений')).toBeInTheDocument()
    expect(
      await screen.findByText('prompt 1000 / reasoning 120 / total 1240'),
    ).toBeInTheDocument()
    expect(
      await screen.findByText('prompt 5000 / completion 2500 / total 7500'),
    ).toBeInTheDocument()
    expect(await screen.findByText('1240 / 32768')).toBeInTheDocument()
    // модель из конфига (в tooltip; в дропдауне такое же имя — смотрим строку)
    await waitFor(() => expect(tip?.textContent).toContain('qwen3.8-27b'))
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

  it('данных нет (last: null) — «—», заполнение 0, без warn', async () => {
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
    // ждём загрузку: строка лимита «— / 32768» (last null, limit из API)
    expect(await screen.findByText('— / 32768')).toBeInTheDocument()
    expect(gauge).toHaveAttribute('aria-valuenow', '0')
    expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('—')
    expect(container.querySelector('.token-gauge.warn')).toBeNull()
  })

  it('сам по себе (вне ChatPanel) — строки tooltip при пустых данных', async () => {
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
    // лимит 0 — «— / —» (ждём loadAll, чтобы исключить «до загрузки»)
    expect(await screen.findByText('— / —')).toBeInTheDocument()
    const tip = gauge.querySelector('.token-gauge-tip')
    expect(tip).toBeTruthy()
    expect(tip?.textContent).toContain('Диалог (оценка)')
    // модель — из загруженного конфига
    await waitFor(() => expect(tip?.textContent).toContain('qwen3.8-27b'))
  })
})
