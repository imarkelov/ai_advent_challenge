// Тесты мобильного сайдбара (день 30): на узких экранах список диалогов
// выезжает поверх чата по кнопке «☰», закрывается по фону, после выбора
// диалога и после создания нового. Само поведение раскладки — в CSS
// (@media ≤900px), здесь проверяется React-логика переключения.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import Sidebar from '../src/components/Sidebar'
import ChatPanel from '../src/components/ChatPanel'

const d1 = { id: 'd1', title: 'Первый', created: '2026-09-18', message_count: 3 }
const d2 = { id: 'd2', title: 'Второй', created: '2026-09-19', message_count: 1 }

const BASE_FIXTURES: Record<string, unknown> = {
  '/api/config': { model: 'qwen3.8-27b', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': { models: [] },
  '/api/memory': {
    active_id: 'd1',
    dialogue: { message_count: 3, tokens_est: 20 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
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

/** Рендерит сайдбар и чат вместе — как в App (☰ в шапке управляет сайдбаром). */
function renderStudio() {
  return render(
    <StudioProvider>
      <Sidebar />
      <ChatPanel />
    </StudioProvider>,
  )
}

function sidebar(): HTMLElement {
  return document.querySelector('aside.sidebar') as HTMLElement
}

/** Ждёт загрузки списка диалогов. Ищем внутри сайдбара: название
 *  активного диалога показывается ещё и в шапке чата. */
async function waitDialogues() {
  await waitFor(() =>
    expect(sidebar().querySelector('.dialogue-list')).toBeTruthy(),
  )
  await waitFor(() =>
    expect(sidebar().textContent).toContain('Первый'),
  )
}

function backdrop(): HTMLElement {
  return document.querySelector('.sidebar-backdrop') as HTMLElement
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      if (url === '/api/dialogues') {
        return jsonResponse({ active_id: 'd1', dialogues: [d1, d2] })
      }
      if (url.startsWith('/api/dialogues/')) {
        return jsonResponse({ dialogue: { messages: [] } })
      }
      if (url in BASE_FIXTURES) return jsonResponse(BASE_FIXTURES[url])
      return jsonResponse({ ok: true })
    }),
  )
})

describe('мобильный сайдбар (☰)', () => {
  it('кнопка «☰» есть в шапке чата и доступна по имени', async () => {
    renderStudio()
    await waitDialogues()
    const burger = screen.getByRole('button', { name: 'Открыть список диалогов' })
    expect(burger).toBeInTheDocument()
    expect(burger).toHaveClass('sidebar-burger')
  })

  it('по умолчанию сайдбар закрыт (без класса mobile-open)', async () => {
    renderStudio()
    await waitDialogues()
    expect(sidebar()).not.toHaveClass('mobile-open')
    expect(backdrop()).not.toHaveClass('open')
  })

  it('клик по «☰» открывает сайдбар и затемнение', async () => {
    renderStudio()
    await waitDialogues()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть список диалогов' }))
    await waitFor(() => expect(sidebar()).toHaveClass('mobile-open'))
    expect(backdrop()).toHaveClass('open')
  })

  it('клик по затемнению закрывает сайдбар', async () => {
    renderStudio()
    await waitDialogues()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть список диалогов' }))
    await waitFor(() => expect(sidebar()).toHaveClass('mobile-open'))

    fireEvent.click(backdrop())
    await waitFor(() => expect(sidebar()).not.toHaveClass('mobile-open'))
    expect(backdrop()).not.toHaveClass('open')
  })

  it('выбор диалога закрывает сайдбар (иначе чат не виден)', async () => {
    renderStudio()
    await waitDialogues()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть список диалогов' }))
    await waitFor(() => expect(sidebar()).toHaveClass('mobile-open'))

    const second = Array.from(sidebar().querySelectorAll('.dialogue-title'))
      .find((el) => el.textContent === 'Второй') as HTMLElement
    fireEvent.click(second)
    await waitFor(() => expect(sidebar()).not.toHaveClass('mobile-open'))
  })

  it('создание диалога закрывает сайдбар', async () => {
    renderStudio()
    await waitDialogues()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть список диалогов' }))
    await waitFor(() => expect(sidebar()).toHaveClass('mobile-open'))

    fireEvent.click(screen.getByText('+ Новый диалог'))
    await waitFor(() => expect(sidebar()).not.toHaveClass('mobile-open'))
  })

  it('затемнение помечено aria-hidden (декоративное)', async () => {
    renderStudio()
    await waitDialogues()
    expect(backdrop()).toHaveAttribute('aria-hidden', 'true')
  })
})
