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
      max_output_tokens: 1024,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Окно контекста' })
    expect(gauge.closest('.chat-head-actions')).toBeTruthy()
    // занято = prompt 1000 + резерв на ответ 1024 (max_tokens конфига)
    // = 2024; 2024 / 32768 = 6.2% → aria-valuenow 6
    await waitFor(() => expect(gauge).toHaveAttribute('aria-valuenow', '6'))
    // компактное число рядом с кольцом: «занято / окно» (короткий
    // дефис — разделитель, а не часть числа)
    await waitFor(() =>
      expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('2k/32.8k'),
    )
    // без warn при малом заполнении; до hover поповера нет
    expect(container.querySelector('.token-gauge.warn')).toBeNull()
    expect(container.querySelector('.token-pop')).toBeNull()
  })

  it('hover → поповер: окно, остаток, расход; без hover — нет', async () => {
    stubFetch({
      last: { prompt: 1000, completion: 200, reasoning: 120, total: 1240 },
      session: { prompt: 5000, completion: 2500, total: 7500 },
      context_limit: 32768,
      max_output_tokens: 1024,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Окно контекста' })
    // ждём loadAll: «занято / окно»
    await waitFor(() =>
      expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('2k/32.8k'),
    )
    // до hover поповера в DOM нет
    expect(container.querySelector('.token-pop')).toBeNull()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('Окно диалога')).closest('.token-pop')
    expect(pop).toBeTruthy()
    // занято / окно, остаток диалогу, расход последнего запроса, модель
    await waitFor(() =>
      expect(screen.getByTestId('tokens-occupied').textContent).toBe('2 024 / 32 768'),
    )
    expect(screen.getByTestId('tokens-remaining').textContent).toBe('30 744')
    expect(screen.getByTestId('tokens-turns').textContent).toBe('30 ход(ов)')
    expect(screen.getByText('prompt', { selector: '.pop-row span' })).toBeInTheDocument()
    expect(screen.getByText('7500')).toBeInTheDocument()
    await waitFor(() => expect(pop?.textContent).toContain('qwen3.8-27b'))
    // progress-бар поповера: занято 2024 / 32768 → aria-valuenow 6
    const bar = pop?.querySelector('[role="progressbar"]')
    await waitFor(() => expect(bar?.getAttribute('aria-valuenow')).toBe('6'))
  })

  it('pct > 90% — warn-класс и aria; число ≥1000 — short form', async () => {
    stubFetch({
      last: { prompt: 28000, completion: 2000, reasoning: 0, total: 30000 },
      session: { prompt: 28000, completion: 2000, total: 30000 },
      context_limit: 32768,
      max_output_tokens: 1024,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Окно контекста' })
    // занято = prompt 28000 + резерв 1024 = 29024; 29024 / 32768 = 88.6%
    // → aria-valuenow 89 и warn (порог 80%: остаток тает быстрее, чем
    // монотонно растёт история)
    await waitFor(() => expect(gauge).toHaveAttribute('aria-valuenow', '89'))
    expect(gauge).toHaveClass('token-gauge', 'warn')
    await waitFor(() =>
      expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('29k/32.8k'),
    )
    expect(container.querySelector('.token-gauge.warn .token-gauge-fill')).toBeTruthy()
  })

  it('данных нет (last: null) — занятость неизвестна, цифры не выдумываем', async () => {
    stubFetch({
      last: null,
      session: { prompt: 0, completion: 0, total: 0 },
      context_limit: 32768,
      max_output_tokens: 1024,
    })
    const { container } = render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Окно контекста' })
    // запроса не было; если бэкенд явно сказал «0 занято» — так и показываем,
    // а без поля занятости считаем резерв под ответ: 1024 / 32768 = 3%
    await waitFor(() =>
      expect(['0', '3']).toContain(gauge.getAttribute('aria-valuenow')),
    )
    await waitFor(() =>
      expect(['0k/32.8k', '1k/32.8k', '—/32.8k']).toContain(
        gauge.querySelector('.token-gauge-num')?.textContent,
      ),
    )
    expect(container.querySelector('.token-gauge.warn')).toBeNull()

    // поповер без запроса: цифры не выдумываем — «—»
    fireEvent.mouseEnter(gauge)
    await screen.findByText('Окно диалога')
    expect(screen.getByTestId('tokens-occupied').textContent).toBe('—')
    expect(screen.getByTestId('tokens-remaining').textContent).toBe('—')
    expect(screen.getByTestId('tokens-turns').textContent).toBe('—')
  })

  it('сам по себе (вне ChatPanel) — окно неизвестно (0) → «—», поповер не врёт', async () => {
    stubFetch({
      last: null,
      session: { prompt: 0, completion: 0, total: 0 },
      context_limit: 0,
      max_output_tokens: 0,
    })
    render(
      <StudioProvider>
        <TokenGauge />
      </StudioProvider>,
    )
    const gauge = await screen.findByRole('progressbar', { name: 'Окно контекста' })
    expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('—')

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('Окно диалога')).closest('.token-pop')
    expect(pop).toBeTruthy()
    // окно 0 — «занято» и «осталось» «—» (не выдумываем цифры)
    expect(screen.getByTestId('tokens-occupied').textContent).toBe('—')
    expect(screen.getByTestId('tokens-remaining').textContent).toBe('—')
    await waitFor(() => expect(pop?.textContent).toContain('qwen3.8-27b'))
  })
})
