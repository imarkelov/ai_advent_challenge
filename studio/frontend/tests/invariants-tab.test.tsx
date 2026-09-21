// InvariantsTab (день 14, новая схема): вкладка «Инварианты» —
// список {title, description, forbidden, is_active},
// добавление (POST /api/invariants), тумблер per-инвариант
// (POST /api/invariants/{id}/toggle), удаление (DELETE /api/invariants/{id}).
// Пометка «неизменяемые»: ассистент инварианты не меняет и не удаляет,
// но пользователь их включает/выключает (активный/спит).
// Фикстура API — как в memory-tab-toggles.test.tsx.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import ContextPanel from '../src/components/ContextPanel'
import InvariantsTab from '../src/components/InvariantsTab'

type Inv = {
  id: string
  title: string
  description: string
  forbidden: string[]
  is_active: boolean
}

// Контракты GET /api/* для StudioProvider (loadAll при старте) — без /api/invariants
// (тот отдаётся из изменяемого «серверного» хранилища внутри стаба)
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

// fetch-стаб: «серверное» хранилище инвариантов (toggle/POST/DELETE меняют его,
// GET /api/invariants читает текущее состояние) + перехват всех мутаций
function stubFetch(invariants: Inv[], onMutate?: (method: string, url: string, body: unknown) => void) {
  const store: Inv[] = [...invariants]
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = normalizeUrl(input)
    const method = init?.method ?? 'GET'
    if (method === 'POST' && url === '/api/invariants') {
      const body = JSON.parse(String(init.body)) as Inv
      onMutate?.('POST', url, body)
      const created: Inv = { id: 'inv-new', ...body }
      store.push(created)
      return jsonResponse({ invariant: created })
    }
    const m = url.match(/^\/api\/invariants\/([^/]+)\/toggle$/)
    if (method === 'POST' && m) {
      onMutate?.('POST', url, null)
      const id = decodeURIComponent(m[1])
      const i = store.findIndex((x) => x.id === id)
      if (i >= 0) store[i] = { ...store[i], is_active: !store[i].is_active }
      return jsonResponse({ invariant: store[i] })
    }
    if (method === 'DELETE' && url.startsWith('/api/invariants/')) {
      onMutate?.('DELETE', url, null)
      const id = decodeURIComponent(url.slice('/api/invariants/'.length))
      const i = store.findIndex((x) => x.id === id)
      if (i >= 0) store.splice(i, 1)
      return jsonResponse({ ok: true })
    }
    if (url === '/api/invariants') return jsonResponse({ invariants: store })
    return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
  })
}

beforeEach(() => {
  localStorage.clear()
})

describe('InvariantsTab — список', () => {
  it('рендерит title / description / forbidden-чипы из API', async () => {
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', title: 'Язык ответа', description: 'Отвечай на русском', forbidden: ['anglicisms', 'списки'], is_active: true },
      { id: 'inv-2', title: 'Формат', description: 'Без списков', forbidden: [], is_active: true },
    ]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByText('Язык ответа')
    expect(screen.getByText('Отвечай на русском')).toBeTruthy()
    expect(screen.getByText('anglicisms')).toBeTruthy()
    expect(screen.getByText('списки')).toBeTruthy()
    expect(screen.getByText('Формат')).toBeTruthy()
    expect(screen.getByText('Без списков')).toBeTruthy()
  })

  it('статус-чип: активный — «активный», выключенный — «спит»', async () => {
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', title: 'A', description: 'a', forbidden: [], is_active: true },
      { id: 'inv-2', title: 'B', description: 'b', forbidden: [], is_active: false },
    ]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    const list = (await screen.findByText('A')).closest('ul') as HTMLElement
    expect(within(list).getAllByText('активный')).toHaveLength(1)
    expect(within(list).getAllByText('спит')).toHaveLength(1)
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

  it('пометка «неизменяемые» + пояснение: тумблер — только у пользователя', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    expect(await screen.findByText('неизменяемые')).toBeTruthy()
    expect(screen.getByText(/ассистент их не меняет и не удаляет/i)).toBeTruthy()
  })
})

describe('InvariantsTab — тумблер is_active', () => {
  it('клик по свитчу строки → POST /api/invariants/{id}/toggle; после перечитывания — «спит»', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', title: 'Язык', description: 'русский', forbidden: [], is_active: true },
    ], (method, url, body) => {
      if (method === 'POST' && url.includes('/toggle')) calls.push({ url, body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByText('Язык')
    const sw = screen.getByRole('switch', { name: 'Выключить Язык' })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(sw)
    await waitFor(() => expect(calls).toEqual([{ url: '/api/invariants/inv-1/toggle', body: null }]))
    // refreshInvariants перечитал список: статус переключился
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'false'))
    expect(screen.getByText('спит')).toBeTruthy()
  })
})

describe('InvariantsTab — добавление', () => {
  it('кнопка «Добавить» disabled, пока не заполнены title и description', async () => {
    vi.stubGlobal('fetch', stubFetch([]))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    const btn = await screen.findByRole('button', { name: 'Добавить' })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('Название'), { target: { value: 'Тон' } })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('Описание'), { target: { value: 'кратко' } })
    expect(btn).toBeEnabled()
  })

  it('клик по «Добавить» → POST {title, description, forbidden из запятой строки, is_active: true}, поля очищаются', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([], (method, url, body) => {
      if (method === 'POST' && url === '/api/invariants') calls.push({ url, body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByRole('button', { name: 'Добавить' })
    fireEvent.change(screen.getByPlaceholderText('Название'), { target: { value: 'Тон' } })
    fireEvent.change(screen.getByPlaceholderText('Описание'), { target: { value: 'кратко' } })
    fireEvent.change(screen.getByPlaceholderText(/Запрещённое/), { target: { value: 'списки,  шутки ,  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    await waitFor(() =>
      expect(calls).toEqual([{
        url: '/api/invariants',
        body: { title: 'Тон', description: 'кратко', forbidden: ['списки', 'шутки'], is_active: true },
      }]),
    )
    expect((screen.getByPlaceholderText('Название') as HTMLInputElement).value).toBe('')
    expect((screen.getByPlaceholderText('Описание') as HTMLTextAreaElement).value).toBe('')
    expect((screen.getByPlaceholderText(/Запрещённое/) as HTMLTextAreaElement).value).toBe('')
  })

  it('forbidden также делится по переносам строк', async () => {
    const calls: { body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([], (method, url, body) => {
      if (method === 'POST' && url === '/api/invariants') calls.push({ body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByRole('button', { name: 'Добавить' })
    fireEvent.change(screen.getByPlaceholderText('Название'), { target: { value: 'T' } })
    fireEvent.change(screen.getByPlaceholderText('Описание'), { target: { value: 'd' } })
    fireEvent.change(screen.getByPlaceholderText(/Запрещённое/), { target: { value: 'a\nb, c' } })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    await waitFor(() =>
      expect(calls).toEqual([{ body: { title: 'T', description: 'd', forbidden: ['a', 'b', 'c'], is_active: true } }]),
    )
  })

  it('тумблер is_active в форме выключен → POST с is_active: false', async () => {
    const calls: { body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([], (method, url, body) => {
      if (method === 'POST' && url === '/api/invariants') calls.push({ body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByRole('button', { name: 'Добавить' })
    fireEvent.change(screen.getByPlaceholderText('Название'), { target: { value: 'T' } })
    fireEvent.change(screen.getByPlaceholderText('Описание'), { target: { value: 'd' } })
    const formSwitch = screen.getByRole('switch', { name: 'Активность нового инварианта' })
    expect(formSwitch).toHaveAttribute('aria-checked', 'true') // default true
    fireEvent.click(formSwitch)
    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    await waitFor(() =>
      expect(calls).toEqual([{ body: { title: 'T', description: 'd', forbidden: [], is_active: false } }]),
    )
  })
})

describe('InvariantsTab — удаление', () => {
  it('клик по «×» → DELETE /api/invariants/{id}', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', title: 'Язык', description: 'русский', forbidden: [], is_active: true },
      { id: 'inv-2', title: 'Формат', description: 'списки', forbidden: [], is_active: true },
    ], (method, url, body) => {
      if (method === 'DELETE') calls.push({ url, body })
    }))
    render(
      <StudioProvider>
        <InvariantsTab />
      </StudioProvider>,
    )
    await screen.findByText('Язык')
    const delBtns = screen.getAllByRole('button', { name: /Удалить/ })
    expect(delBtns).toHaveLength(2)
    fireEvent.click(delBtns[0])
    await waitFor(() => expect(calls).toEqual([{ url: '/api/invariants/inv-1', body: null }]))
  })
})

describe('ContextPanel — вкладка «Инварианты»', () => {
  it('3-й таб «Инварианты» отображается и открывает вкладку', async () => {
    vi.stubGlobal('fetch', stubFetch([
      { id: 'inv-1', title: 'Язык', description: 'русский', forbidden: [], is_active: true },
    ]))
    render(
      <StudioProvider>
        <ContextPanel />
      </StudioProvider>,
    )
    // все 3 таба панели «Контекст» (Токены/Запрос перенесены в сайдбар)
    for (const label of ['Память', 'Профили', 'Инварианты']) {
      expect(screen.getByRole('tab', { name: label })).toBeTruthy()
    }
    const tab = screen.getByRole('tab', { name: 'Инварианты' })
    expect(tab).toHaveAttribute('aria-selected', 'false')
    fireEvent.click(tab)
    // контент вкладки: пометка + список
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('неизменяемые')).toBeTruthy()
    expect(screen.getByText('Язык')).toBeTruthy()
  })
})
