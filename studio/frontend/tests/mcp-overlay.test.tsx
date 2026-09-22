// Overlay MCP (день 16): панель MCP-серверов открывается своей кнопкой
// (aria-label="MCP") в шапке чата — отдельно от настроек «⚙», той же
// обвязкой (классы .settings-overlay), закрывается кнопкой «×» и кликом
// по фону. Офлайн: stub fetch (контракты loadAll).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../src/App'

const FIXTURES: Record<string, unknown> = {
  '/api/config': {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    max_tokens: 1024,
    system_prompt: 'Ты — ассистент.',
  },
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
  '/api/dialogues': { active_id: null, dialogues: [] },
  '/api/memory': {
    active_id: null,
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/invariants': { invariants: [] },
  '/api/mcp/servers': {
    servers: [
      {
        id: 'mcp_a1', name: 'MockSrv', type: 'stdio',
        command: ['npx', '-y', 'mock-mcp'], url: '', env: {},
        enabled: true, status: 'idle', error: null, tools_count: 0,
      },
    ],
  },
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

// Оба overlay делят класс .settings-overlay: MCP-overlay ищем по
// dialog aria-label="MCP", settings — первый в DOM (рендерится раньше)
function mcpOverlayEl(): HTMLElement | null {
  const dlg = document.querySelector('aside.settings-panel[aria-label="MCP"]')
  return dlg ? (dlg.closest('.settings-overlay') as HTMLElement) : null
}

function settingsOverlayEl(): HTMLElement | null {
  return document.querySelector('.settings-overlay')
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => jsonResponse(FIXTURES[normalizeUrl(input)] ?? { ok: true })),
  )
})

describe('MCP overlay — открытие и закрытие', () => {
  it('кнопка «🧩» в шапке чата; overlay скрыт в начале (контента нет)', async () => {
    render(<App />)
    const mcp = await screen.findByRole('button', { name: 'MCP' })
    expect(mcp).toHaveClass('mcp-toggle')
    // шапка чата — верхний правый угол экрана
    expect(mcp.closest('.chat-head')).toBeTruthy()
    expect(mcpOverlayEl()).toBeTruthy()
    expect(mcpOverlayEl()).not.toHaveClass('open')
    // закрытый overlay — диалог и контент McpTab в DOM нет
    expect(screen.queryByRole('dialog', { name: 'MCP' })).toBeNull()
    expect(screen.queryByText('MCP-серверы')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Закрыть MCP' })).toBeNull()
  })

  it('клик по «🧩» → overlay открывается (McpTab виден); «×» закрывает; настройки при этом закрыты', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'MCP' }))
    await waitFor(() => expect(mcpOverlayEl()).toHaveClass('open'))
    expect(screen.getByRole('dialog', { name: 'MCP' })).toBeTruthy()
    // контент — панель MCP-серверов
    expect(screen.getByText('MCP-серверы')).toBeTruthy()
    expect(screen.getByText('MockSrv')).toBeTruthy()
    // overlay MCP не затрагивает overlay настроек
    expect(settingsOverlayEl()).not.toHaveClass('open')

    fireEvent.click(screen.getByRole('button', { name: 'Закрыть MCP' }))
    await waitFor(() => expect(mcpOverlayEl()).not.toHaveClass('open'))
    expect(screen.queryByRole('dialog', { name: 'MCP' })).toBeNull()
    expect(screen.queryByText('MCP-серверы')).toBeNull()

    // повторный клик по «🧩» — снова открывает (toggle)
    fireEvent.click(screen.getByRole('button', { name: 'MCP' }))
    await waitFor(() => expect(mcpOverlayEl()).toHaveClass('open'))
    expect(screen.getByText('MockSrv')).toBeTruthy()
  })

  it('клик по фону (backdrop) закрывает overlay', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'MCP' }))
    await screen.findByRole('dialog', { name: 'MCP' })
    const backdrop = mcpOverlayEl()?.querySelector('.settings-backdrop')
    expect(backdrop).toBeTruthy()
    fireEvent.click(backdrop as HTMLElement)
    await waitFor(() => expect(mcpOverlayEl()).not.toHaveClass('open'))
    expect(screen.queryByText('MCP-серверы')).toBeNull()
  })
})
