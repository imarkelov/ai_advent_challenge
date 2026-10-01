// TokenPopover: окно по hover на токен-гейдже (вместо блока «Токены» в
// сайдбаре). Поведение: открывается по hover, закрывается по mouseleave
// (после grace ~300ms) и по клику вне; размер из localStorage
// (token-pop-size) применяется при открытии; drag-grip растягивает
// (pointer events, min 320×200) и сохраняет размер.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
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
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
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

const TOKENS = {
  last: { prompt: 1000, completion: 200, reasoning: 120, total: 1240 },
  session: { prompt: 5000, completion: 2500, total: 7500 },
  context_limit: 32768,
}

async function renderGauge() {
  const utils = render(
    <StudioProvider>
      <TokenGauge />
    </StudioProvider>,
  )
  const gauge = await screen.findByRole('progressbar', { name: 'Лимит контекста' })
  // ждём loadAll: число из загруженных токенов
  await waitFor(() => expect(gauge.querySelector('.token-gauge-num')?.textContent).toBe('1.2k'))
  return { ...utils, gauge }
}

beforeEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('TokenPopover — открытие/закрытие', () => {
  it('открывается по hover; строки = данные TokensTab (лимит, usage, сессия, модель)', async () => {
    stubFetch(TOKENS)
    const { container, gauge } = await renderGauge()
    expect(container.querySelector('.token-pop')).toBeNull()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    expect(pop).toBeTruthy()
    expect(pop?.getAttribute('aria-label')).toBe('Токены')

    const rows = pop?.querySelectorAll('.pop-row')
    expect(rows?.length).toBe(6)
    const values = [...(rows ?? [])].map((r) => r.querySelector('b')?.textContent)
    expect(values).toEqual([
      '1240 / 32768', // последний запрос: total / лимит
      '1000', // prompt
      '200', // completion
      '1240', // total
      '7500', // за сессию
      'qwen3.8-27b', // модель
    ])
    // progress-бар: 1240 / 32768 → 4
    expect(pop?.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('4')
    // drag-grip на месте
    expect(pop?.querySelector('.token-pop-grip')).toBeTruthy()
  })

  it('mouseleave — закрывается после grace ~300ms', async () => {
    stubFetch(TOKENS)
    const { container, gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    await screen.findByText('последний запрос')
    expect(container.querySelector('.token-pop')).toBeTruthy()

    fireEvent.mouseLeave(gauge)
    // в grace-периоде поповер ещё открыт
    expect(container.querySelector('.token-pop')).toBeTruthy()
    // после grace — закрыт
    await waitFor(
      () => expect(container.querySelector('.token-pop')).toBeNull(),
      { timeout: 1500 },
    )
  })

  it('grace: курсор успел заехать в поповер — не закрывается', async () => {
    stubFetch(TOKENS)
    const { container, gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    expect(pop).toBeTruthy()

    // ушли с гейджа, но в grace-периоде заехали в поповер
    fireEvent.mouseLeave(gauge)
    fireEvent.mouseEnter(pop as Element)
    // даже после grace-периода окно открыто
    await new Promise((r) => setTimeout(r, 500))
    expect(container.querySelector('.token-pop')).toBeTruthy()
  })

  it('клик вне — закрывается', async () => {
    stubFetch(TOKENS)
    const { container, gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    await screen.findByText('последний запрос')
    expect(container.querySelector('.token-pop')).toBeTruthy()

    fireEvent.click(document.body)
    await waitFor(() => expect(container.querySelector('.token-pop')).toBeNull())
  })
})

describe('TokenPopover — размер', () => {
  it('размер из localStorage (token-pop-size) применяется при открытии', async () => {
    stubFetch(TOKENS)
    localStorage.setItem('token-pop-size', JSON.stringify({ w: 480, h: 300 }))
    const { gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    expect(pop?.style.width).toBe('480px')
    expect(pop?.style.height).toBe('300px')
  })

  it('битая запись в localStorage — дефолтный размер без падения', async () => {
    stubFetch(TOKENS)
    localStorage.setItem('token-pop-size', '{не json')
    const { gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    // дефолт 340×240
    expect(pop?.style.width).toBe('340px')
    expect(pop?.style.height).toBe('240px')
  })

  it('drag-grip: pointermove меняет размер, pointerup сохраняет в localStorage', async () => {
    stubFetch(TOKENS)
    const { container, gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    const grip = pop?.querySelector('.token-pop-grip') as HTMLElement
    expect(grip).toBeTruthy()
    // стартовый размер — дефолт 340×240
    expect(pop?.style.width).toBe('340px')
    expect(pop?.style.height).toBe('240px')

    // drag: старт (100,100) → (160,140): +60×+40
    fireEvent.pointerDown(grip, { clientX: 100, clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(window, { clientX: 160, clientY: 140 })
    expect(pop?.style.width).toBe('400px')
    expect(pop?.style.height).toBe('280px')

    fireEvent.pointerUp(window)
    expect(JSON.parse(localStorage.getItem('token-pop-size') ?? '')).toEqual({ w: 400, h: 280 })
  })

  it('drag не уводит размер ниже min 320×200', async () => {
    stubFetch(TOKENS)
    const { gauge } = await renderGauge()

    fireEvent.mouseEnter(gauge)
    const pop = (await screen.findByText('последний запрос')).closest('.token-pop')
    const grip = pop?.querySelector('.token-pop-grip') as HTMLElement

    // драг влево-вверх сильнее min: 340−50=290 → 320, 240−60=180 → 200
    fireEvent.pointerDown(grip, { clientX: 100, clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(window, { clientX: 50, clientY: 40 })
    expect(pop?.style.width).toBe('320px')
    expect(pop?.style.height).toBe('200px')
    fireEvent.pointerUp(window)
  })
})
