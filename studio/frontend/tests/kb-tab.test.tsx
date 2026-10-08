// KbTab (день 21) — вкладка «База знаний» (RAG):
// пустое состояние (GET /api/kb/stats → 404 {detail} → {exists:false}),
// индексация (POST /api/kb/index {strategy, embedder} → stats + сравнение
// стратегий), тумблеры RAG/агент-цикл (POST /api/kb-settings patch),
// топ-K (rag_top_k), поиск (GET /api/kb/search?q=&k=5 → результаты с
// score/файлом/секцией), загрузка файлов (POST /api/kb/upload, multipart
// "file"). Fetch mocked, реального /api/kb в тестах нет.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import KbTab from '../src/components/KbTab'

type Call = { url: string; body: unknown }

// Контракты GET /api/* для StudioProvider (loadAll при старте);
// активный диалог не нужен — вкладка «База знаний» глобальная
const FIXTURES: Record<string, unknown> = {
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

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

// KbStats из «бэкенда»: файлы индекса + загрузки (uploads растут при
// upload; в files загруженный файл попадает только после «Индексировать»)
// + pending_files (корпус, ещё не попавший в индекс)
function kbStatsBody(
  files: string[],
  uploads: { name: string; size: number }[],
  pending: string[] = [],
) {
  return {
    exists: true,
    strategy: 'fixed',
    embedder: 'hash',
    dim: 1024,
    built_at: '2026-09-29T10:00:00',
    stats: {
      docs: 3,
      files: files.length,
      chunks: 42,
      total_chars: 15000,
      build_ms: 4300,
      corpus_words: 4200,
    },
    comparison: {
      fixed: { chunks: 42, avg_chars: 357, max_chars: 900, hit_at_3: 0.875, precision_at_3: 0.611, mrr: 0.812 },
      structural: { chunks: 17, avg_chars: 882, max_chars: 2000, hit_at_3: 0.813, precision_at_3: 0.556, mrr: 0.789 },
    },
    files,
    uploads,
    pending_files: pending,
    // День 21 (реранкер): ключ настроен по умолчанию (warning — отдельный тест)
    reranker_key_configured: true,
  }
}

const SEARCH_RESULTS = [
  {
    chunk_id: 'c1',
    source: 'file',
    file: 'weather.md',
    section: 'Погода',
    score: 0.912,
    text: 'Погода в Самаре +21.',
  },
  {
    chunk_id: 'c2',
    source: 'file',
    file: 'notes.md',
    section: '',
    score: 0.61,
    text: 'Заметка без секции.',
  },
]

// indexed — индекс уже построен (GET stats → 200 KbStats, иначе 404 {detail}).
// Состояние мока мутабельное: индексация (POST /api/kb/index) и upload
// меняют его; calls — все POST {url, body} для ассертов.
// indexDelay — отложить ответ POST /api/kb/index (прогресс-бар виден);
// pending — pending_files в stats (корпус вне индекса).
function mockApi(
  opts: { indexed: boolean; indexDelay?: number; pending?: string[] },
  calls: Call[],
) {
  let indexed = opts.indexed
  const pending = opts.pending ?? []
  const files = opts.indexed ? ['a.md', 'b.py'] : []
  // Загрузки: при indexed — одна сид-загрузка, ещё не попавшая в индекс
  const uploads: { name: string; size: number }[] = opts.indexed
    ? [{ name: 'note.txt', size: 11 }]
    : []
  let settings = { agent_loop: false, rag: false, rag_top_k: 5, strategy: 'fixed', embedder: 'hash', reranker: 'off', rag_recall: 50 }
  // Прогресс сборки (GET /api/kb/build-status): running, пока POST висит
  let building = false

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      if (init?.method === 'DELETE') {
        calls.push({ url, body: null })
        if (url.startsWith('/api/kb/uploads/')) {
          const name = decodeURIComponent(url.replace('/api/kb/uploads/', ''))
          const i = uploads.findIndex((u) => u.name === name)
          if (i === -1) {
            return new Response(JSON.stringify({ detail: 'Файл не найден' }), {
              status: 404,
              headers: { 'Content-Type': 'application/json' },
            })
          }
          uploads.splice(i, 1)
          const fi = files.indexOf(`uploads/${name}`)
          if (fi !== -1) files.splice(fi, 1)
          return jsonResponse({ ok: true, file: name, chunks_removed: 1 })
        }
        if (url === '/api/kb') {
          const n = uploads.length
          uploads.length = 0
          files.length = 0
          indexed = false
          pending.length = 0
          return jsonResponse({ ok: true, uploads_removed: n })
        }
        return jsonResponse({ ok: true })
      }
      if (init?.method === 'POST') {
        if (url === '/api/kb/upload') {
          // multipart: тело — FormData, поле "file"
          const fd = init.body as FormData
          const f = fd.get('file') as File
          calls.push({ url, body: fd })
          uploads.push({ name: f.name, size: f.size })
          return jsonResponse({ ok: true, file: f.name, size: f.size })
        }
        const body = JSON.parse(String(init.body))
        calls.push({ url, body })
        if (url === '/api/kb/index') {
          building = true
          if (opts.indexDelay) await new Promise((r) => setTimeout(r, opts.indexDelay))
          indexed = true
          building = false
          // пересборка: загрузки попадают в индекс (files)
          uploads.forEach((u) => files.push(`uploads/${u.name}`))
          return jsonResponse({
            stats: { chunks: 42, build_ms: 4300 },
            comparison: kbStatsBody(files, uploads).comparison,
            strategy: body.strategy,
            mode: 'full',
            added: files.length,
          })
        }
        if (url === '/api/kb/settings') {
          settings = { ...settings, ...body }
          return jsonResponse(settings)
        }
        return jsonResponse({ ok: true })
      }
      // GET
      if (url === '/api/kb/uploads') {
        // Список загрузок — тот же mutable-массив, что у stats (работает
        // и без индекса)
        return jsonResponse({ uploads })
      }
      if (url.startsWith('/api/kb/stats')) {
        if (indexed) return jsonResponse(kbStatsBody(files, uploads, pending))
        return new Response(JSON.stringify({ detail: 'Индекс не построен' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url === '/api/kb/build-status') {
        return jsonResponse(
          building
            ? { running: true, phase: 'embedding', done: 5, total: 10 }
            : { running: false, phase: 'idle', done: 0, total: 0 },
        )
      }
      if (url === '/api/kb/settings') return jsonResponse(settings)
      if (url.startsWith('/api/kb/search')) return jsonResponse({ results: SEARCH_RESULTS })
      return jsonResponse(FIXTURES[url] ?? { ok: true })
    }),
  )
}

async function renderTab(opts: { indexed: boolean }) {
  const calls: Call[] = []
  mockApi(opts, calls)
  render(
    <StudioProvider>
      <KbTab />
    </StudioProvider>,
  )
  // Дождаться загрузки stats/settings: индекс есть — «Статистика»,
  // нет — подсказка «Сначала создайте индекс»
  await screen.findByText(opts.indexed ? /документов/i : /сначала создайте индекс/i)
  // Вымыть пассивные апдейты (ответы loadAll после первого рендера)
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
  return calls
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('KbTab — вкладка «RAG»', () => {
  it('индекс не построен — пустое состояние с кнопкой «Индексировать»', async () => {
    const calls = await renderTab({ indexed: false })
    // stats 404 → {exists:false}: подсказка в поиске, таблицы/статистики нет
    expect(screen.getByText(/сначала создайте индекс/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Индексировать' })).toBeInTheDocument()
    expect(screen.queryByText('MRR')).not.toBeInTheDocument()
    // при открытии POST-запросов нет (только GET stats/settings)
    expect(calls).toHaveLength(0)
  })

  it('блок «Документы репозитория в индексе» убран (корпус — только загрузки)', async () => {
    // mock отдаёт в files и не-загрузки (a.md, b.py) — после фикса дня 21
    // отдельного блока для доков репозитория нет вообще
    await renderTab({ indexed: true })
    expect(screen.queryByText(/документы репозитория в индексе/i)).not.toBeInTheDocument()
  })

  it('«Индексировать» → POST /api/kb/index {strategy, embedder}, затем stats + сравнение', async () => {
    const calls = await renderTab({ indexed: false })
    fireEvent.click(screen.getByRole('button', { name: 'Индексировать' }))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/kb/index',
        body: { strategy: 'fixed', embedder: 'hash' },
      }),
    )
    // после успешной индексации — статистика и сравнение стратегий
    expect(await screen.findByText('MRR')).toBeInTheDocument()
    expect(screen.getByText('0.875')).toBeInTheDocument() // fixed hit@3
    expect(screen.getByText('0.789')).toBeInTheDocument() // structural MRR
    expect(screen.getByText('4.3 c')).toBeInTheDocument() // build_ms → «N c»
  })

  it('тумблеры RAG/агент-цикл → POST /api/kb/settings с patch', async () => {
    const calls = await renderTab({ indexed: false })
    fireEvent.click(screen.getByRole('switch', { name: 'RAG в диалоге' }))
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/kb/settings', body: { rag: true } }),
    )
    fireEvent.click(screen.getByRole('switch', { name: 'Цикл агента (MCP-инструменты)' }))
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/kb/settings', body: { agent_loop: true } }),
    )
  })

  it('«Этап 2: Топ-K в диалоге» → POST /api/kb/settings {rag_top_k}', async () => {
    const calls = await renderTab({ indexed: false })
    fireEvent.change(screen.getByLabelText('Этап 2: Топ-K в диалоге'), { target: { value: '7' } })
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/kb/settings', body: { rag_top_k: 7 } }),
    )
  })

  it('поиск: Enter/кнопка → GET /api/kb/search, результаты с score/файлом/секцией', async () => {
    await renderTab({ indexed: true })
    const input = screen.getByLabelText('Запрос')
    fireEvent.change(input, { target: { value: 'погода' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    // результат: score-чип + «файл · секция» + выдержка
    expect(await screen.findByText('weather.md · Погода')).toBeInTheDocument()
    expect(screen.getByText('0.912')).toBeInTheDocument()
    expect(screen.getByText('Погода в Самаре +21.')).toBeInTheDocument()
    // второй результат без секции — только файл
    expect(screen.getByText('notes.md')).toBeInTheDocument()
  })

  it('«Файлы»: загрузка видна со статусом «не в индексе», после upload — подтверждение', async () => {
    await renderTab({ indexed: true })
    // сид-загрузка note.txt (11 B) сохранена, но ещё не в индексе
    expect(screen.getByText('note.txt')).toBeInTheDocument()
    expect(screen.getByText(/не в индексе/i)).toBeInTheDocument()
    // новый upload → файл появляется в загрузках + подтверждение «Добавлено»
    const file = new File(['hello world'], 'todo.md', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Файл'), { target: { files: [file] } })
    expect(await screen.findByText('todo.md')).toBeInTheDocument()
    expect(screen.getByText(/добавлено/i)).toBeInTheDocument()
    // обе загрузки «не в индексе» (пересборки ещё не было)
    expect(screen.getAllByText(/не в индексе/i)).toHaveLength(2)
  })

  it('«Файлы»: без индекса загрузка видна (GET /api/kb/uploads) — чип «не в индексе», «Очистить базу» enabled', async () => {
    // Сценарий бага: stats → 404 «Индекс не построен», но файл уже
    // загружен — его несёт отдельный GET /api/kb/uploads
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url.startsWith('/api/kb/stats')) {
          return new Response(JSON.stringify({ detail: 'Индекс не построен' }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        if (url === '/api/kb/uploads') {
          return jsonResponse({ uploads: [{ name: 'note.txt', size: 11 }] })
        }
        if (url === '/api/kb/settings') {
          return jsonResponse({ agent_loop: false, rag: false, rag_top_k: 5, strategy: 'fixed', embedder: 'hash', reranker: 'off', rag_recall: 50 })
        }
        return jsonResponse(FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    // Дождаться загрузки (stats 404 → подсказка в поиске)
    await screen.findByText(/сначала создайте индекс/i)
    // Файл в списке загрузок, чип «не в индексе» (индекса нет)
    expect(await screen.findByText('note.txt')).toBeInTheDocument()
    expect(screen.getByText(/не в индексе/i)).toBeInTheDocument()
    // «Очистить базу» доступна: загрузка есть, индекс не нужен
    expect(screen.getByRole('button', { name: 'Очистить базу' })).toBeEnabled()
  })

  it('«+ Добавить файл»: выбор файла → POST /api/kb/upload (FormData "file")', async () => {
    const calls = await renderTab({ indexed: false })
    const file = new File(['hello world'], 'notes.md', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Файл'), { target: { files: [file] } })
    await waitFor(() => {
      const c = calls.find((c) => c.url === '/api/kb/upload')
      expect(c).toBeTruthy()
      const fd = c?.body as FormData
      expect(fd.get('file')).toBe(file)
    })
  })

  it('«Индексировать»: прогресс-бар с фазой во время сборки, «Готово» после', async () => {
    const calls = await renderTab({ indexed: false, indexDelay: 120 })
    fireEvent.click(screen.getByRole('button', { name: 'Индексировать' }))
    // во время сборки: progressbar + подпись фазы/процента
    const bar = await screen.findByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
    expect(screen.getByText(/— \d+%/)).toBeInTheDocument()
    // после завершения: «Готово: 42 чанков, 4.3 c (полная сборка)» + stats
    expect(await screen.findByText(/готово: 42 чанков, 4\.3 c \(полная сборка\)/i)).toBeInTheDocument()
    expect(screen.getByText('MRR')).toBeInTheDocument()
    expect(calls).toContainEqual({ url: '/api/kb/index', body: { strategy: 'fixed', embedder: 'hash' } })
  })

  it('«Очистить поиск»: сбрасывает запрос, результаты и кнопку неактивна без запроса', async () => {
    await renderTab({ indexed: true })
    // без запроса/результатов кнопка «×» отключена
    expect(screen.getByRole('button', { name: 'Очистить поиск' })).toBeDisabled()
    const input = screen.getByLabelText('Запрос')
    fireEvent.change(input, { target: { value: 'погода' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    expect(await screen.findByText('weather.md · Погода')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Очистить поиск' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Очистить поиск' }))
    // результаты и запрос убраны, кнопка снова отключена
    expect(screen.queryByText('weather.md · Погода')).not.toBeInTheDocument()
    expect((input as HTMLInputElement).value).toBe('')
    expect(screen.getByRole('button', { name: 'Очистить поиск' })).toBeDisabled()
  })

  it('«Файлы»: pending-счётчик «Ещё не в индексе: N»', async () => {
    await renderTab({ indexed: true, pending: ['uploads/note.txt', 'docs/new.md'] })
    expect(screen.getByText(/ещё не в индексе: 2/i)).toBeInTheDocument()
  })

  it('«Удалить файл»: confirm → DELETE /api/kb/uploads/{name}, файл исчезает', async () => {
    const calls = await renderTab({ indexed: true })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    expect(screen.getByText('note.txt')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Удалить note.txt' }))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/kb/uploads/note.txt',
        body: null,
      }),
    )
    expect(confirmSpy).toHaveBeenCalledWith(
      expect.stringContaining('note.txt'),
    )
    // после перечитывания stats файла в загрузках нет
    await waitFor(() =>
      expect(screen.queryByText('note.txt')).not.toBeInTheDocument(),
    )
  })

  it('«Удалить файл»: confirm=нет → запрос не уходит', async () => {
    const calls = await renderTab({ indexed: true })
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: 'Удалить note.txt' }))
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(calls.filter((c) => c.url.startsWith('/api/kb/uploads/'))).toHaveLength(0)
    expect(screen.getByText('note.txt')).toBeInTheDocument()
  })

  it('«Очистить базу»: confirm → DELETE /api/kb, индекс уходит (подсказка 404)', async () => {
    const calls = await renderTab({ indexed: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    // без загрузок кнопка отключена; со сид-загрузкой — включена
    expect(screen.getByRole('button', { name: 'Очистить базу' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Очистить базу' }))
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/kb', body: null }),
    )
    // индекс удалён → stats 404 → пустое состояние
    expect(await screen.findByText(/сначала создайте индекс/i)).toBeInTheDocument()
  })

  it('«Очистить базу»: без загрузок — кнопка отключена', async () => {
    // индекс не построен → загрузок нет → кнопка disabled
    await renderTab({ indexed: false })
    expect(screen.getByRole('button', { name: 'Очистить базу' })).toBeDisabled()
  })
})

// ── День 21: двухэтапный поиск + реранкер ───────────────────────────────────

// Своя заставка fetch: контролирует settings.reranker и stats.
// reranker_key_configured — флаг warning «Ключ реранкера не настроен»
function stubRerankerFetch(opts: { reranker: 'off' | 'api'; rerankerKeyConfigured: boolean }) {
  const settings = {
    agent_loop: false, rag: true, rag_top_k: 3,
    strategy: 'fixed', embedder: 'hash',
    reranker: opts.reranker, rag_recall: 50,
  }
  const statsBody = kbStatsBody(['a.md'], [])
  statsBody.reranker_key_configured = opts.rerankerKeyConfigured
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      if (init?.method === 'POST' && url === '/api/kb/settings') {
        const body = JSON.parse(String(init.body))
        return jsonResponse({ ...settings, ...body })
      }
      if (url.startsWith('/api/kb/stats')) return jsonResponse(statsBody)
      if (url === '/api/kb/settings') return jsonResponse(settings)
      return jsonResponse(FIXTURES[url] ?? { ok: true })
    }),
  )
}

describe('KbTab — день 21: двухэтапный поиск + реранкер', () => {
  it('«Включить»: реранкер-селект + инпуты этапов 1/2', async () => {
    await renderTab({ indexed: false })
    const sel = screen.getByLabelText('Реранкер') as HTMLSelectElement
    expect(sel.tagName).toBe('SELECT')
    expect(sel.value).toBe('off')
    // оба числовых поля двухэтапного поиска на месте
    expect(screen.getByLabelText('Этап 1: кандидатов (recall)')).toBeInTheDocument()
    expect(screen.getByLabelText('Этап 2: Топ-K в диалоге')).toBeInTheDocument()
  })

  it('«Этап 1: кандидатов (recall)» → POST /api/kb/settings {rag_recall}', async () => {
    const calls = await renderTab({ indexed: false })
    fireEvent.change(screen.getByLabelText('Этап 1: кандидатов (recall)'), { target: { value: '80' } })
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/kb/settings', body: { rag_recall: 80 } }),
    )
  })

  it('warning «Ключ реранкера не настроен» — при reranker=api и key не настроен', async () => {
    stubRerankerFetch({ reranker: 'api', rerankerKeyConfigured: false })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    expect(await screen.findByText(/ключ реранкера не настроен/i)).toBeInTheDocument()
  })

  it('warning отсутствует — key настроен (reranker=api)', async () => {
    stubRerankerFetch({ reranker: 'api', rerankerKeyConfigured: true })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await screen.findByText('Документов') // дождались загрузки stats
    expect(screen.queryByText(/ключ реранкера не настроен/i)).not.toBeInTheDocument()
  })

  it('warning отсутствует — reranker=off', async () => {
    stubRerankerFetch({ reranker: 'off', rerankerKeyConfigured: false })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await screen.findByText('Документов')
    expect(screen.queryByText(/ключ реранкера не настроен/i)).not.toBeInTheDocument()
  })

  it('поиск: результат с реранкером — score = rerank_score + чип «из #N»', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url.startsWith('/api/kb/stats')) return jsonResponse(kbStatsBody(['book.md'], []))
        if (url === '/api/kb/settings') {
          return jsonResponse({ agent_loop: false, rag: true, rag_top_k: 3, strategy: 'fixed', embedder: 'hash', reranker: 'api', rag_recall: 50 })
        }
        if (url.startsWith('/api/kb/search')) {
          return jsonResponse({
            results: [
              { chunk_id: 'c1', source: 'file', file: 'book.md', section: 'Гл.1', score: 0.42, rerank_score: 0.93, stage1_rank: 14, text: 'Важный факт.' },
            ],
            recall_total: 50,
            reranked: true,
          })
        }
        return jsonResponse(FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    const input = await screen.findByLabelText('Запрос')
    fireEvent.change(input, { target: { value: 'факт' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    // score-чип показывает rerank_score (0.93 → 0.930), а не гибридный 0.42
    expect(await screen.findByText('0.930')).toBeInTheDocument()
    expect(screen.getByText('из #14')).toBeInTheDocument()
    expect(screen.getByText('book.md · Гл.1')).toBeInTheDocument()
  })

  it('поиск: без реранкера — обычный score, чипа «из #» нет', async () => {
    await renderTab({ indexed: true })
    const input = screen.getByLabelText('Запрос')
    fireEvent.change(input, { target: { value: 'погода' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    expect(await screen.findByText('0.912')).toBeInTheDocument() // metric3(0.912)
    expect(screen.queryByText(/из #/)).not.toBeInTheDocument()
  })
})

// ── День 22: сравнение RAG (один вопрос — два ответа рядом) ──────────────────

// Ответ POST /api/rag/compare (RagCompareResult): оба ответа + чанки
// (KbSearchResult, у r1 — реранк: score чип показывает rerank_score)
// + KB-блок, ушедший в system-промпт RAG-стороны
const RAG_COMPARE_OK = {
  answer_plain:
    'Не знаю: информации о телефоне героя в предоставленных данных нет.',
  answer_rag:
    'У героя был телефон IPhone 17Promax.\n(Источник: egg_book.txt)',
  kb_block:
    'База знаний (топ-3 выдержки):\n1. egg_book.txt · Гл. 1: …телефон IPhone 17Promax…',
  chunks: [
    {
      chunk_id: 'r1',
      source: 'upload',
      file: 'egg_book.txt',
      section: 'Гл. 1',
      score: 0.86,
      rerank_score: 0.93,
      stage1_rank: 4,
      text: 'У героя был телефон IPhone 17Promax.',
    },
    {
      chunk_id: 'r2',
      source: 'upload',
      file: 'egg_book.txt',
      section: '',
      score: 0.52,
      text: 'Он работал игроком на ксилофоне.',
    },
  ],
  rag_context: { recall_total: 50, reranked: true, chunks: [] },
}

function stubRagCompareFetch(mode: 'ok' | 'no-index', calls: Call[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      if (init?.method === 'POST' && url === '/api/rag/compare') {
        const body = JSON.parse(String(init.body))
        calls.push({ url, body })
        if (mode === 'no-index') {
          return new Response(JSON.stringify({ detail: 'Индекс не построен' }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return jsonResponse(RAG_COMPARE_OK)
      }
      if (url.startsWith('/api/kb/stats')) {
        return jsonResponse(kbStatsBody(['egg_book.txt'], []))
      }
      if (url === '/api/kb/settings') {
        return jsonResponse({
          agent_loop: false,
          rag: true,
          rag_top_k: 3,
          strategy: 'fixed',
          embedder: 'hash',
          reranker: 'api',
          rag_recall: 50,
        })
      }
      return jsonResponse(FIXTURES[url] ?? { ok: true })
    }),
  )
}

describe('KbTab — день 22: «Сравнение RAG»', () => {
  it('вопрос → POST /api/rag/compare, две панели (Без RAG / С RAG) + чанки + KB-блок', async () => {
    const calls: Call[] = []
    stubRagCompareFetch('ok', calls)
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    const ta = await screen.findByLabelText('Вопрос')
    fireEvent.change(ta, { target: { value: 'Какая модель телефона была у героя?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сравнить' }))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/rag/compare',
        body: { question: 'Какая модель телефона была у героя?' },
      }),
    )
    // Обе панели с ответами (текст как есть, включая перевод строки).
    // Для RAG-ответа с \n — function-matcher на <pre>: обычный string-matcher
    // не работает (TL: текст ноды нормализуется, а matcher — нет)
    expect(screen.getByText('Без RAG')).toBeInTheDocument()
    expect(screen.getByText('С RAG')).toBeInTheDocument()
    expect(
      await screen.findByText(
        (_content, el) =>
          el?.tagName === 'PRE' && el.textContent === RAG_COMPARE_OK.answer_rag,
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(RAG_COMPARE_OK.answer_plain)).toBeInTheDocument()
    // Чанки: file · section + score-чип (при реранке — rerank_score) + «из #N»
    expect(screen.getByText('egg_book.txt · Гл. 1')).toBeInTheDocument()
    expect(screen.getByText('0.930')).toBeInTheDocument()
    expect(screen.getByText('из #4')).toBeInTheDocument()
    // второй чанк без секции — только файл
    expect(screen.getByText('egg_book.txt')).toBeInTheDocument()
    expect(screen.getByText('Он работал игроком на ксилофоне.')).toBeInTheDocument()
    // KB-блок — сворачиваемая строка
    expect(screen.getByText('KB-блок (в system-промпт)')).toBeInTheDocument()
  })

  it('404 → RU-сообщение «Индекс не построен», без краха и без панелей', async () => {
    stubRagCompareFetch('no-index')
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    const ta = await screen.findByLabelText('Вопрос')
    fireEvent.change(ta, { target: { value: 'Какая модель телефона была у героя?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сравнить' }))
    expect(await screen.findByText('Индекс не построен')).toBeInTheDocument()
    // ответ не рендерится, кнопка снова активна
    expect(screen.queryByText('Без RAG')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Сравнить' })).toBeEnabled()
  })

  it('кнопка «Сравнить» отключена без вопроса', async () => {
    stubRagCompareFetch('ok')
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await screen.findByLabelText('Вопрос')
    expect(screen.getByRole('button', { name: 'Сравнить' })).toBeDisabled()
  })
})

// ── День 23: порог отсечения min_score + 4 панели сравнения ──────────────────

// Ответ POST /api/rag/compare с 4 армами (контракт дня 23): старые поля
// (день 22) + answer_rag_filter / answer_rag_rewrite / rewritten_query /
// rewrite_applied. answer_rag_filter пустой — пустая арма → «—».
const RAG_COMPARE_4 = {
  answer_plain: 'Plain: не знаю.',
  answer_rag: 'RAG: у героя был телефон.',
  answer_rag_filter: '',
  answer_rag_rewrite: 'Rewrite: телефон IPhone 17Promax.',
  kb_block: 'База знаний:',
  chunks: [] as unknown[],
  rag_context: { recall_total: 50, reranked: false, chunks: [] },
  rewritten_query: 'модель телефона героя',
  rewrite_applied: true,
}

// Своя заставка: min_score в settings (не указан — форма «старого»
// бэкенда без поля), compare-ответ настраиваемый; calls — все POST
function stubDay23Fetch(opts: {
  minScore?: number
  compare?: Record<string, unknown>
  calls: Call[]
}) {
  const settings: Record<string, unknown> = {
    agent_loop: false,
    rag: true,
    rag_top_k: 3,
    strategy: 'fixed',
    embedder: 'hash',
    reranker: 'off',
    rag_recall: 50,
  }
  if (opts.minScore !== undefined) settings.min_score = opts.minScore
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      if (init?.method === 'POST' && url === '/api/rag/compare') {
        const body = JSON.parse(String(init.body))
        opts.calls.push({ url, body })
        return jsonResponse(opts.compare ?? {})
      }
      if (init?.method === 'POST' && url === '/api/kb/settings') {
        const body = JSON.parse(String(init.body))
        opts.calls.push({ url, body })
        return jsonResponse({ ...settings, ...body })
      }
      if (url.startsWith('/api/kb/stats')) return jsonResponse(kbStatsBody(['egg_book.txt'], []))
      if (url === '/api/kb/settings') return jsonResponse(settings)
      return jsonResponse(FIXTURES[url] ?? { ok: true })
    }),
  )
}

async function doCompareInUi(question = 'Какая модель телефона была у героя?') {
  const ta = await screen.findByLabelText('Вопрос')
  fireEvent.change(ta, { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: 'Сравнить' }))
}

describe('KbTab — день 23: порог отсечения + 4 панели сравнения', () => {
  it('порог: вводишь 0.5 → blur → POST /api/kb/settings {min_score: 0.5}', async () => {
    const calls = await renderTab({ indexed: false })
    const input = screen.getByLabelText('Порог отсечения (0 = off)') as HTMLInputElement
    expect(input).toHaveAttribute('step', '0.05')
    fireEvent.change(input, { target: { value: '0.5' } })
    // до blur POST не уходит (частичный ввод не спамит)
    expect(calls.filter((c) => c.url === '/api/kb/settings')).toHaveLength(0)
    fireEvent.blur(input)
    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/api/kb/settings', body: { min_score: 0.5 } }),
    )
    // инпут показывает закоммиченное значение
    await waitFor(() => expect(input.value).toBe('0.5'))
  })

  it('порог: значение из GET settings подставлено (persist после reload)', async () => {
    const calls: Call[] = []
    stubDay23Fetch({ minScore: 0.5, compare: RAG_COMPARE_4, calls })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    // дождались загрузки stats/settings
    await screen.findByText('Документов')
    expect((screen.getByLabelText('Порог отсечения (0 = off)') as HTMLInputElement).value).toBe('0.5')
  })

  it('compare: 4 армы → 4 панели с заголовками; пустая арма → «—»; body с min_score', async () => {
    const calls: Call[] = []
    stubDay23Fetch({ minScore: 0.5, compare: RAG_COMPARE_4, calls })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await doCompareInUi()
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/rag/compare',
        body: { question: 'Какая модель телефона была у героя?', min_score: 0.5 },
      }),
    )
    // 4 заголовка панелей
    expect(screen.getByText('Без RAG')).toBeInTheDocument()
    expect(screen.getByText('С RAG')).toBeInTheDocument()
    expect(screen.getByText('С RAG + фильтром')).toBeInTheDocument()
    expect(screen.getByText('С RAG + rewrite')).toBeInTheDocument()
    // ответы арм + пустая filter-арма → «—»
    expect(await screen.findByText('RAG: у героя был телефон.')).toBeInTheDocument()
    expect(screen.getByText('Plain: не знаю.')).toBeInTheDocument()
    expect(screen.getByText('Rewrite: телефон IPhone 17Promax.')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('chip «фильтр ≥ 0.5» виден при min_score=0.5 (поиск + filter-панель)', async () => {
    const calls: Call[] = []
    stubDay23Fetch({ minScore: 0.5, compare: RAG_COMPARE_4, calls })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await screen.findByText('Документов')
    // до compare — только информационный чип в поиске
    expect(screen.getAllByText('фильтр ≥ 0.5')).toHaveLength(1)
    await doCompareInUi()
    expect(await screen.findByText('RAG: у героя был телефон.')).toBeInTheDocument()
    // + чип у filter-панели
    expect(screen.getAllByText('фильтр ≥ 0.5')).toHaveLength(2)
  })

  it('chip «фильтр ≥ X» скрыт при min_score=0 / без поля (старый бэкенд); body без min_score', async () => {
    const calls: Call[] = []
    // settings без min_score (форма «старого» бэкенда)
    stubDay23Fetch({ compare: RAG_COMPARE_4, calls })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await screen.findByText('Документов')
    expect((screen.getByLabelText('Порог отсечения (0 = off)') as HTMLInputElement).value).toBe('0')
    expect(screen.queryByText(/фильтр ≥/)).not.toBeInTheDocument()
    await doCompareInUi()
    // тело byte-совместимо с днём 22: без min_score
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/rag/compare',
        body: { question: 'Какая модель телефона была у героя?' },
      }),
    )
    expect(screen.queryByText(/фильтр ≥/)).not.toBeInTheDocument()
  })

  it('rewrite-чип: rewritten_query виден + копирование («Скопировано»)', async () => {
    const calls: Call[] = []
    stubDay23Fetch({ compare: RAG_COMPARE_4, calls })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await doCompareInUi()
    expect(await screen.findByText('Rewrite: телефон IPhone 17Promax.')).toBeInTheDocument()
    // чип с переписанным запросом + кнопка копирования
    expect(screen.getByText('модель телефона героя')).toBeInTheDocument()
    const copyBtn = screen.getByRole('button', { name: 'Скопировать' })
    fireEvent.click(copyBtn)
    expect(await screen.findByText('Скопировано')).toBeInTheDocument()
  })

  it('rewrite_applied=false → панели rag+rewrite без rewrite-чипа (ответ показан)', async () => {
    const calls: Call[] = []
    stubDay23Fetch({
      compare: {
        ...RAG_COMPARE_4,
        rewrite_applied: false,
        // бэкенд в fallback может вернуть исходный вопрос — UI чип не рендерит
        rewritten_query: 'Какая модель телефона была у героя?',
      },
      calls,
    })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await doCompareInUi()
    // панель на месте, ответ показан
    expect(await screen.findByText('Rewrite: телефон IPhone 17Promax.')).toBeInTheDocument()
    // rewrite-чипа нет ни в каком виде
    expect(screen.queryByText('модель телефона героя')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Скопировать' })).not.toBeInTheDocument()
  })

  it('старая форма ответа (без полей дня 23) → без краха: 4 панели, пустые армы «—»', async () => {
    const calls: Call[] = []
    // RAG_COMPARE_OK — ответ дня 22: ни одного поля дня 23 (undefined)
    stubDay23Fetch({ compare: RAG_COMPARE_OK, calls })
    render(
      <StudioProvider>
        <KbTab />
      </StudioProvider>,
    )
    await doCompareInUi()
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/rag/compare',
        body: { question: 'Какая модель телефона была у героя?' },
      }),
    )
    // старые армы с ответами, новые — «—» (2 шт: filter + rewrite)
    expect(screen.getByText('Без RAG')).toBeInTheDocument()
    expect(screen.getByText('С RAG')).toBeInTheDocument()
    expect(screen.getByText('С RAG + фильтром')).toBeInTheDocument()
    expect(screen.getByText('С RAG + rewrite')).toBeInTheDocument()
    expect(screen.getByText(RAG_COMPARE_OK.answer_plain)).toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(2)
    // чипы фильтра/rewrite не рендерятся
    expect(screen.queryByText(/фильтр ≥/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Скопировать' })).not.toBeInTheDocument()
  })
})
