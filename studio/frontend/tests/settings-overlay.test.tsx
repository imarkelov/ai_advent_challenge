// Overlay настроек: панель «Контекст» (Память/Профили/Инварианты)
// открывается кнопкой «⚙ Настройки» в шапке чата, закрывается кнопкой «×»
// и кликом по фону; бейдж профиля открывает overlay сразу на «Профили».
// Вкладка «MCP» вынесена в отдельный overlay (McpOverlay, свой тест).
// Офлайн: stub fetch (контракты loadAll).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../src/App'

const PROFILE_PENDING = { status: 'pending', interview: false, name: '', role: '', tone: '', taboos: '' }

// loadAll-контракты StudioProvider: активный диалог d1 с pending-профилем
// (бейдж «Профиль не заполнен» виден в шапке чата)
const FIXTURES: Record<string, unknown> = {
  '/api/config': {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    max_tokens: 1024,
    system_prompt: 'Ты — ассистент.',
  },
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
  '/api/dialogues': {
    active_id: 'd1',
    dialogues: [{ id: 'd1', title: 'Диалог', created: '2026-09-22', message_count: 0, profile: PROFILE_PENDING }],
  },
  '/api/dialogues/d1': { dialogue: { messages: [] } },
  '/api/memory': {
    active_id: 'd1',
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
    toggles: { st: true, wm: true, lt: true },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/invariants': { invariants: [] },
  '/api/mcp/servers': { servers: [] },
  '/api/mcp/tools': { tools: [] },
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

function overlayEl(): HTMLElement | null {
  return document.querySelector('.settings-overlay')
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => jsonResponse(FIXTURES[normalizeUrl(input)] ?? { ok: true })),
  )
})

describe('Settings overlay — открытие и закрытие', () => {
  it('кнопка «⚙ Настройки» в шапке чата; overlay скрыт в начале (вкладок нет)', async () => {
    render(<App />)
    const settings = await screen.findByRole('button', { name: 'Настройки' })
    expect(settings).toHaveClass('settings-toggle')
    // шапка чата — верхний правый угол экрана
    expect(settings.closest('.chat-head')).toBeTruthy()
    expect(overlayEl()).toBeTruthy()
    expect(overlayEl()).not.toHaveClass('open')
    // закрытый overlay — вкладки панели «Контекст» в DOM нет
    for (const label of ['Память', 'Профили', 'Инварианты']) {
      expect(screen.queryByRole('tab', { name: label })).toBeNull()
    }
    expect(screen.queryByRole('button', { name: 'Закрыть настройки' })).toBeNull()
  })

  it('клик по «⚙» → overlay открывается: 3 вкладки + кнопка «×»; «×» закрывает', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Настройки' }))
    await waitFor(() => expect(overlayEl()).toHaveClass('open'))
    for (const label of ['Память', 'Профили', 'Инварианты']) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy()
    }
    // открыта вкладка по умолчанию — «Память»
    expect(screen.getByRole('tab', { name: 'Память' })).toHaveAttribute('aria-selected', 'true')
    const close = screen.getByRole('button', { name: 'Закрыть настройки' })
    expect(close).toBeTruthy()

    fireEvent.click(close)
    await waitFor(() => expect(overlayEl()).not.toHaveClass('open'))
    expect(screen.queryByRole('tab', { name: 'Память' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Закрыть настройки' })).toBeNull()

    // повторный клик по «⚙» — снова открывает (toggle)
    fireEvent.click(screen.getByRole('button', { name: 'Настройки' }))
    await waitFor(() => expect(overlayEl()).toHaveClass('open'))
    expect(screen.getByRole('tab', { name: 'Память' })).toBeTruthy()
  })

  it('клик по фону (backdrop) закрывает overlay', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Настройки' }))
    await screen.findByRole('tab', { name: 'Память' })
    const backdrop = document.querySelector('.settings-backdrop')
    expect(backdrop).toBeTruthy()
    fireEvent.click(backdrop as HTMLElement)
    await waitFor(() => expect(overlayEl()).not.toHaveClass('open'))
    expect(screen.queryByRole('tab', { name: 'Память' })).toBeNull()
  })

  it('бейдж «Профиль не заполнен» → overlay открывается на вкладке «Профили» (aria-selected)', async () => {
    render(<App />)
    const badge = await screen.findByRole('button', { name: 'Профиль не заполнен' })
    fireEvent.click(badge)
    await waitFor(() => expect(overlayEl()).toHaveClass('open'))
    expect(screen.getByRole('tab', { name: 'Профили' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Память' })).toHaveAttribute('aria-selected', 'false')
  })
})
