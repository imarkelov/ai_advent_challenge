// InvariantsTab (день 14): вкладка «Инварианты» — список key → value,
// добавление (POST /api/invariants), удаление (DELETE /api/invariants/{id}),
// пометка «неизменяемые» и ОТСУТСТВИЕ свитчей вкл/выкл (в отличие от
// слоёв памяти). Фикстура API — как в memory-tab-toggles.test.tsx.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import ContextPanel from '../src/components/ContextPanel'
import InvariantsTab from '../src/components/InvariantsTab'

type Inv = { id: string; key: string; value: string }

// Контракты GET /api/* для StudioProvider (loadAll при старте)
function fixtures(invariants: Inv[] = []): Record<string, unknown> {
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
    },
    '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
    '/api/requests': { requests: [] },
    '/api/invariants': { invariants },
  }
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

// fetch-стаб: фикстуры GET + перехват POST/DELETE /api/invariants
function stubFetch(invariants: Inv[], onMutate?: (method: string, url: string, body: unknown) => void) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = normalizeUrl(input)
    const method = init?.method ?? 'GET'
    if (method === 'POST' && url === '/api/invariants') {
      onMutate?.('POST', url, JSON.parse(String(init.body)))
      return jsonResponse({ invariant: { id: 'inv-new', ...JSON.parse(String(init.body)) } })
    }
    if (method === 'DELETE' && url.startsWith('/api/invariants/')) {
      onMutate?.('DELETE', url, null)
      return jsonResponse({ ok: true })
    }
    return jsonResponse(fixtures(invariants)[url] ?? { ok: true })
  })
}

beforeEach(() => {
  localStorage.clear()
})

describe('InvariantsTab — список и пометка', () => {
  it('рендерит список key → value из API', async () => {
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', key: 'language', value: 'отвечай на русском' },
      { id: 'inv-2', key: 'format', value: 'без списков' },
    ]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByText('language')
    expect(screen.getByText('отвечай на русском')).toBeTruthy()
    expect(screen.getByText('format')).toBeTruthy()
    expect(screen.getByText('без списков')).toBeTruthy()
  })

  it('пустой список — «Пусто»', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText('Пусто')).toBeTruthy()
  })

  it('пометка «неизменяемые» + пояснение об активности', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText('неизменяемые')).toBeTruthy()
    expect(screen.getByText(/всегда активны в промпте, не отключаемы/i)).toBeTruthy()
  })

  it('БЕЗ свитчей вкл/выкл (в отличие от слоёв памяти)', async () => {
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', key: 'k', value: 'v' },
    ]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByText('k')
    expect(screen.queryAllByRole('switch')).toHaveLength(0)
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  })
})

describe('InvariantsTab — добавление', () => {
  it('кнопка «Добавить» disabled, пока поля не заполнены', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'Добавить' })
    expect(btn).toBeDisabled()
  })

  it('клик по «Добавить» → POST /api/invariants {key, value}, поля очищаются', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([], (method, url, body) => {
      if (method === 'POST') calls.push({ url, body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    const keyInput = await screen.findByPlaceholderText('key')
    const valueInput = screen.getByPlaceholderText('value')
    const btn = screen.getByRole('button', { name: 'Добавить' })
    expect(btn).toBeDisabled()
    // кнопка разблокируется, только когда заполнены ОБА поля
    fireEvent.change(keyInput, { target: { value: 'tone' } })
    expect(btn).toBeDisabled()
    fireEvent.change(valueInput, { target: { value: 'кратко' } })
    expect(btn).toBeEnabled()
    fireEvent.click(btn)
    await waitFor(() => expect(calls).toEqual([{ url: '/api/invariants', body: { key: 'tone', value: 'кратко' } }]))
    expect((keyInput as HTMLInputElement).value).toBe('')
    expect((valueInput as HTMLInputElement).value).toBe('')
  })
})

describe('InvariantsTab — удаление', () => {
  it('клик по «×» → DELETE /api/invariants/{id}', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', key: 'language', value: 'русский' },
      { id: 'inv-2', key: 'format', value: 'списки' },
    ], (method, url, body) => {
      if (method === 'DELETE') calls.push({ url, body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByText('language')
    const delBtns = screen.getAllByRole('button', { name: /Удалить/ })
    expect(delBtns).toHaveLength(2)
    fireEvent.click(delBtns[0])
    await waitFor(() => expect(calls).toEqual([{ url: '/api/invariants/inv-1', body: null }]))
  })
})

describe('ContextPanel — вкладка «Инварианты»', () => {
  it('5-й таб «Инварианты» отображается и открывает вкладку', async () => {
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', key: 'language', value: 'русский' },
    ]))
    render(
      <StudioProvider>
        <ContextPanel />
      </StudioProvider>,
    )
    // все 5 табов панели «Контекст»
    for (const label of ['Память', 'Токены', 'Запрос', 'Профили', 'Инварианты']) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy()
    }
    const tab = screen.getByRole('tab', { name: 'Инварианты' })
    expect(tab).toHaveAttribute('aria-selected', 'false')
    fireEvent.click(tab)
    // контент вкладки: пометка + список
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('неизменяемые')).toBeTruthy()
    expect(screen.getByText('language')).toBeTruthy()
  })
})
