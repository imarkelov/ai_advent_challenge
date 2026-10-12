// Вкладка/overlay «Сериалы» (день 29): открывается кнопкой «🎬» в шапке
// чата, показывает профиль оптимизации (temperature / max tokens /
// context window), каталог и результат поиска с метриками. Офлайн: stub
// fetch на контракты дня 29.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../src/App'

const PROFILES = {
  active: 'series',
  profiles: [
    {
      name: 'studio', title: 'Студия (по умолчанию)', description: 'Базовый режим.',
      model: null, temperature: 0.7, top_p: 0.9, num_predict: 2048,
      num_ctx: null, seed: null, system_prompt: 'Ты — ассистент.',
      stop: [], format_json: false, notes: '',
    },
    {
      name: 'series', title: 'Поиск сериалов (Q4_K_M)',
      description: 'Подбор сериалов по каталогу.',
      model: 'qwen3-coder:30b', temperature: 0.2, top_p: 0.9, num_predict: 512,
      num_ctx: 8192, seed: 29, system_prompt: 'Ты — эксперт по сериалам. Каталог.',
      stop: [], format_json: false, notes: 'Проверяем послушание промпту.',
    },
  ],
}

const CATALOG = {
  ready: true,
  stats: {
    total: 400, years: [2010, 2025],
    genres: { драма: 205, комедия: 115, криминал: 55 },
    countries: { США: 43, Россия: 26 },
  },
  sample: [],
}

const SEARCH = {
  query: 'криминал после 2018',
  profile: 'series',
  model: 'qwen3-coder:30b',
  constraints: { genres: ['криминал'], country: null, min_year: 2019, max_year: null },
  recommendations: [
    {
      id: '1', imdb: 'tt1', title: 'Мрачный детектив', year: 2021,
      genres: ['криминал'], country: 'Россия', why: 'подходит по жанру и году',
      satisfies: true,
    },
  ],
  hallucinated: [],
  note: 'отобрал по жанру',
  parsed: true,
  metrics: {
    latency_sec: 3.2, prompt_tokens: 900, completion_tokens: 60,
    tokens_per_sec: 18.75, num_ctx: 8192, parsed: true,
    hallucination_rate: 0, constraint_satisfaction: 1,
  },
  candidates: [{ id: '1', title: 'Мрачный детектив', year: 2021 }],
  request_body: { model: 'qwen3-coder:30b', num_ctx: 8192 },
}

const FIXTURES: Record<string, unknown> = {
  '/api/config': { model: 'qwen3.8-27b', temperature: 0.7, max_tokens: 1024, system_prompt: 'x' },
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
  '/api/mcp/servers': { servers: [] },
  '/api/mcp/tools': { tools: [] },
  '/api/tuning/profiles': PROFILES,
  '/api/series/catalog': CATALOG,
  '/api/series/search': SEARCH,
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

function seriesOverlayEl(): HTMLElement | null {
  const dlg = document.querySelector('aside.settings-panel[aria-label="Поиск сериалов"]')
  return dlg ? (dlg.closest('.settings-overlay') as HTMLElement) : null
}

let posted: { url: string; body: unknown }[] = []

beforeEach(() => {
  localStorage.clear()
  posted = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      if (init?.method === 'POST') {
        posted.push({ url, body: init.body ? JSON.parse(String(init.body)) : null })
      }
      return jsonResponse(FIXTURES[url] ?? { ok: true })
    }),
  )
})

describe('Series overlay (день 29)', () => {
  it('кнопка «🎬» в шапке чата; overlay скрыт в начале', async () => {
    render(<App />)
    const btn = await screen.findByRole('button', { name: 'Поиск сериалов' })
    expect(btn).toHaveClass('series-toggle')
    expect(btn.closest('.chat-head')).toBeTruthy()
    expect(seriesOverlayEl()).toBeTruthy()
    expect(seriesOverlayEl()).not.toHaveClass('open')
    expect(screen.queryByRole('dialog', { name: 'Поиск сериалов' })).toBeNull()
  })

  it('клик открывает overlay, «×» закрывает', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await waitFor(() => expect(seriesOverlayEl()).toHaveClass('open'))
    expect(screen.getByRole('dialog', { name: 'Поиск сериалов' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Закрыть поиск сериалов' }))
    await waitFor(() => expect(seriesOverlayEl()).not.toHaveClass('open'))
  })

  it('клик по фону закрывает overlay', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    const backdrop = seriesOverlayEl()?.querySelector('.settings-backdrop')
    fireEvent.click(backdrop as HTMLElement)
    await waitFor(() => expect(seriesOverlayEl()).not.toHaveClass('open'))
  })

  it('показывает параметры профиля: temperature, max tokens, context window', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    // параметры активного профиля series
    expect(await screen.findByText('0.2')).toBeTruthy()
    expect(screen.getByText('512')).toBeTruthy()
    expect(screen.getByText('8192')).toBeTruthy()
    expect(screen.getByText('29')).toBeTruthy()
  })

  it('показывает статистику каталога', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    expect(await screen.findByText('400')).toBeTruthy()
    expect(screen.getByText('криминал')).toBeTruthy()
  })

  it('раскрывает промпт-шаблон по кнопке', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    expect(screen.queryByText(/эксперт по сериалам/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Показать промпт-шаблон' }))
    expect(screen.getByText(/эксперт по сериалам/)).toBeTruthy()
  })

  it('поиск: отправляет запрос с активным профилем и рендерит карточку', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    const input = await screen.findByLabelText('Что хотите посмотреть?')
    fireEvent.change(input, { target: { value: 'криминал после 2018' } })
    fireEvent.click(screen.getByRole('button', { name: 'Найти' }))

    expect(await screen.findByText('Мрачный детектив')).toBeTruthy()
    expect(screen.getByText('подходит по жанру и году')).toBeTruthy()
    const call = posted.find((p) => p.url === '/api/series/search')
    expect(call?.body).toEqual({ query: 'криминал после 2018', profile: 'series' })
  })

  it('показывает метрики ответа (латентность, верность каталогу)', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    fireEvent.change(await screen.findByLabelText('Что хотите посмотреть?'),
      { target: { value: 'криминал' } })
    fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
    expect(await screen.findByText(/3\.2 с/)).toBeTruthy()
    expect(screen.getByText('18.75 ток/с')).toBeTruthy()
    // «верность каталогу» и «по ограничениям» — обе 100% (два чипа)
    expect(screen.getAllByText('100%')).toHaveLength(2)
  })

  it('подсказка запускает поиск', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    fireEvent.click(await screen.findByRole('button', {
      name: /Посоветуй мрачный криминальный сериал/,
    }))
    expect(await screen.findByText('Мрачный детектив')).toBeTruthy()
  })

  it('пустое поле — кнопка «Найти» заблокирована', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Поиск сериалов' }))
    await screen.findByRole('dialog', { name: 'Поиск сериалов' })
    expect(screen.getByRole('button', { name: 'Найти' })).toBeDisabled()
  })
})
