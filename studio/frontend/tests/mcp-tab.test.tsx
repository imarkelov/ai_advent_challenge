// McpTab (день 16): вкладка «MCP» — список серверов {name, type, status,
// error, tools_count}, подключение (POST /api/mcp/servers/{id}/connect),
// инструменты подключённых серверов (GET /api/mcp/tools), добавление
// (POST /api/mcp/servers, env — KEY=VALUE по строкам), удаление
// (DELETE /api/mcp/servers/{id}). Офлайн: stub fetch.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import ContextPanel from '../src/components/ContextPanel'
import McpTab from '../src/components/McpTab'
import type { McpServer, McpTool } from '../src/api'

// Контракты GET /api/* для StudioProvider (loadAll при старте) — без /api/mcp/*
// (те отдаются из изменяемого «серверного» хранилища внутри стаба)
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
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/invariants': { invariants: [] },
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

// fetch-стаб: «серверное» хранилище MCP-серверов (POST/DELETE/connect
// меняют его, GET читает текущее состояние); connect помечает сервер
// connected + даёт ему 2 mock-инструмента
function stubFetch(
  servers: McpServer[],
  onMutate?: (method: string, url: string, body: unknown) => void,
) {
  const store: McpServer[] = servers.map((s) => ({ ...s }))
  const tools: McpTool[] = []
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = normalizeUrl(input)
    const method = init?.method ?? 'GET'
    if (method === 'POST' && url === '/api/mcp/servers') {
      const body = JSON.parse(String(init.body)) as Partial<McpServer>
      onMutate?.('POST', url, body)
      const created: McpServer = {
        id: 'mcp-new',
        name: body.name ?? '',
        type: (body.type as McpServer['type']) ?? 'stdio',
        command: body.command ?? [],
        url: body.url ?? '',
        env: body.env ?? {},
        enabled: body.enabled ?? true,
        status: 'idle',
        error: null,
        tools_count: 0,
      }
      store.push(created)
      return jsonResponse({ server: created }, 201)
    }
    const cm = url.match(/^\/api\/mcp\/servers\/([^/]+)\/connect$/)
    if (method === 'POST' && cm) {
      onMutate?.('POST', url, null)
      const id = decodeURIComponent(cm[1])
      const i = store.findIndex((x) => x.id === id)
      if (i >= 0) {
        store[i] = { ...store[i], status: 'connected', error: null, tools_count: 2 }
        tools.push(
          { server: id, name: 'mock_echo', description: 'Эхо-инструмент', input_schema: { type: 'object' } },
          { server: id, name: 'mock_ping', description: 'Пинг', input_schema: { type: 'object' } },
        )
      }
      return jsonResponse({ server: store[i] })
    }
    if (method === 'DELETE' && url.startsWith('/api/mcp/servers/')) {
      onMutate?.('DELETE', url, null)
      const id = decodeURIComponent(url.slice('/api/mcp/servers/'.length))
      const i = store.findIndex((x) => x.id === id)
      if (i >= 0) store.splice(i, 1)
      return jsonResponse({ ok: true })
    }
    if (url === '/api/mcp/servers') return jsonResponse({ servers: store })
    if (url === '/api/mcp/tools') return jsonResponse({ tools })
    return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
  })
}

const CTX7: McpServer = {
  id: 'mcp_c7', name: 'Context7', type: 'stdio',
  command: ['npx', '-y', '@upstash/context7-mcp'], url: '', env: {},
  enabled: true, status: 'idle', error: null, tools_count: 0,
}
const FIRECRAWL: McpServer = {
  id: 'mcp_fc', name: 'Firecrawl', type: 'stdio',
  command: ['npx', '-y', 'firecrawl-mcp'], url: '', env: {},
  enabled: true, status: 'error', error: 'npx not found', tools_count: 0,
}

beforeEach(() => {
  localStorage.clear()
})

describe('McpTab — список серверов', () => {
  it('рендерит имя, тип и статус-чипы (idle — «не подключён», error — «ошибка»)', async () => {
    vi.stubGlobal('fetch', stubFetch([CTX7, FIRECRAWL]))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    await screen.findByText('Context7')
    expect(screen.getByText('Firecrawl')).toBeTruthy()
    expect(screen.getAllByText('stdio').length).toBe(2)
    expect(screen.getByText('не подключён')).toBeTruthy()
    expect(screen.getByText('ошибка')).toBeTruthy()
    expect(screen.getByText('npx not found')).toBeTruthy()
  })

  it('пустой список — «Пусто»', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    expect(await screen.findByText('Пусто')).toBeTruthy()
  })
})

describe('McpTab — подключение', () => {
  it('клик «Подключить» → POST .../connect; после перечитывания — «подключён», счётчик и инструменты', async () => {
    const calls: { url: string }[] = []
    vi.stubGlobal('fetch', stubFetch([CTX7], (method, url) => {
      if (method === 'POST' && url.includes('/connect')) calls.push({ url })
    }))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    await screen.findByText('Context7')
    fireEvent.click(screen.getByRole('button', { name: 'Подключить' }))
    await waitFor(() => expect(calls).toEqual([{ url: '/api/mcp/servers/mcp_c7/connect' }]))
    await screen.findByText('подключён')
    expect(screen.getByText('2 инстр.')).toBeTruthy()
    // инструменты подключённого сервера видны в секции
    expect(await screen.findByText('mock_echo')).toBeTruthy()
    expect(screen.getByText('Эхо-инструмент')).toBeTruthy()
    expect(screen.getByText('mock_ping')).toBeTruthy()
  })
})

describe('McpTab — добавление', () => {
  it('кнопка «Добавить» disabled, пока не заполнены имя и command (stdio)', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'Добавить' })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('Имя'), { target: { value: 'My' } })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText(/npx -y/), { target: { value: 'npx -y x' } })
    expect(btn).toBeEnabled()
  })

  it('клик «Добавить» → POST {name, type, command-массив, env из KEY=VALUE}; поля очищаются', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([], (method, url, body) => {
      if (method === 'POST' && url === '/api/mcp/servers') calls.push({ url, body })
    }))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    await screen.findByRole('button', { name: 'Добавить' })
    fireEvent.change(screen.getByPlaceholderText('Имя'), { target: { value: 'My' } })
    fireEvent.change(screen.getByPlaceholderText(/npx -y/), { target: { value: 'npx -y pkg --flag' } })
    fireEvent.change(screen.getByPlaceholderText(/KEY=VALUE/), { target: { value: 'TOKEN=abc\n' } })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    await waitFor(() =>
      expect(calls).toEqual([{
        url: '/api/mcp/servers',
        body: { name: 'My', type: 'stdio', command: ['npx', '-y', 'pkg', '--flag'], url: '', env: { TOKEN: 'abc' }, enabled: true },
      }]),
    )
    expect((screen.getByPlaceholderText('Имя') as HTMLInputElement).value).toBe('')
  })

  it('тип http → поле url, command не уходит в POST', async () => {
    const calls: { body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([], (method, url, body) => {
      if (method === 'POST' && url === '/api/mcp/servers') calls.push({ body })
    }))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    await screen.findByRole('button', { name: 'Добавить' })
    fireEvent.change(screen.getByPlaceholderText('Имя'), { target: { value: 'R' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Тип сервера' }), { target: { value: 'http' } })
    fireEvent.change(screen.getByPlaceholderText(/https:/), { target: { value: 'https://x/mcp' } })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    await waitFor(() =>
      expect(calls).toEqual([{
        body: { name: 'R', type: 'http', command: [], url: 'https://x/mcp', env: {}, enabled: true },
      }]),
    )
  })
})

describe('McpTab — удаление', () => {
  it('клик по «×» → DELETE /api/mcp/servers/{id}', async () => {
    const calls: { url: string }[] = []
    vi.stubGlobal('fetch', stubFetch([CTX7, FIRECRAWL], (method, url) => {
      if (method === 'DELETE') calls.push({ url })
    }))
    render(
      <StudioProvider>
        <McpTab />
      </StudioProvider>,
    )
    await screen.findByText('Context7')
    fireEvent.click(screen.getByRole('button', { name: 'Удалить Context7' }))
    await waitFor(() => expect(calls).toEqual([{ url: '/api/mcp/servers/mcp_c7' }]))
  })
})

describe('ContextPanel — вкладка «MCP»', () => {
  it('4-й таб «MCP» отображается и открывает вкладку', async () => {
    vi.stubGlobal('fetch', stubFetch([CTX7]))
    render(
      <StudioProvider>
        <ContextPanel />
      </StudioProvider>,
    )
    for (const label of ['Память', 'Профили', 'Инварианты', 'MCP']) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy()
    }
    const tab = screen.getByRole('tab', { name: 'MCP' })
    expect(tab).toHaveAttribute('aria-selected', 'false')
    fireEvent.click(tab)
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('Context7')).toBeTruthy()
  })
})
