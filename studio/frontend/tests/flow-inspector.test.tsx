// FlowInspector (ui-rework, задача 3): строка-чип «📡 Обработка…» под
// assistant-сообщением + раскрытая карточка (RAG → Prompt-сборка → LLM →
// Ответ) с пунктом «Запрос JSON» (ленивый fetch GET /api/requests/{id},
// тело — поле `request` записи журнала) и копированием в буфер.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import type { Message } from '../src/state'
import type { RagContext } from '../src/api'
import FlowInspector from '../src/components/FlowInspector'
import ChatPanel from '../src/components/ChatPanel'

// ── Фикстуры ────────────────────────────────────────────────────────────────
// Запись журнала: тело в поле `request` (НЕ body) — контракт agent.py
const REQUEST_1 = {
  id: 1,
  ts: '2026-10-02T10:00:00',
  model: 'qwen3.8-27b',
  request: {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    messages: [{ role: 'user', content: 'Какой телефон был у героя?' }],
  },
  usage: { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 },
  error: null,
}

// RAG-контекст: 3 чанка — по одному на каждую цветовую шкалу score
// (≥0.8 зелёный, 0.5–0.8 жёлтый, <0.5 красный) + stage1_rank>20 → «из #N»
const RAG_CTX: RagContext = {
  query: 'Какой телефон был у героя?',
  recall_total: 50,
  reranked: true,
  chunks: [
    { rank: 1, file: 'The_Cherry_Orchard.md', section: 'Act III', score: 0.82, stage1_rank: 25, reranked: true, text: 'Телефон не упоминается. Телеграммы. …' },
    { rank: 2, file: 'The_Cherry_Orchard.md', section: 'Act IV', score: 0.61, stage1_rank: 3, reranked: true, text: 'Вишнёвый сад будет продан. …' },
    { rank: 3, file: 'The_Dame_With_the_Dog.md', section: 'Chapter 2', score: 0.3, stage1_rank: null, reranked: true, text: 'Море. Собака. …' },
  ],
}

const FULL_MSG: Message = {
  role: 'assistant',
  content: 'В приведённых отрывках телефон не упоминается.',
  model: 'qwen3.8-27b',
  request_id: 1,
  usage: { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 },
  rag_context: RAG_CTX,
}

const PLAIN_MSG: Message = { role: 'assistant', content: 'Простой ответ без данных.' }

// Только RAG-чанки, без request_id/usage/model
const RAG_ONLY_MSG: Message = {
  role: 'assistant',
  content: 'Ответ на RAG.',
  rag_context: RAG_CTX,
}

// Контракты GET /api/* для StudioProvider (loadAll при старте)
const API_FIXTURES: Record<string, unknown> = {
  '/api/config': {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    max_tokens: 1024,
    system_prompt: 'Ты — ассистент Студии. Отвечай кратко.',
  },
  '/api/dialogues': { active_id: null, dialogues: [] },
  '/api/memory': {
    active_id: null,
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 1, tokens_est: 40, items: { project: 'ai_advent_challenge' } },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
  '/api/rules': {
    profile_block: 'Профиль пользователя:\n- имя: Migo',
    invariants_block: 'Инварианты:\n- Стек: TypeScript',
  },
  '/api/requests/1': REQUEST_1,
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

// jsdom без navigator.clipboard — ставим spy перед тестами копирования
function stubClipboard(): { writeText: ReturnType<typeof vi.fn> } {
  const writeText = vi.fn(async () => undefined)
  Object.defineProperty(window.navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return { writeText }
}

// Рендер FlowInspector в провайдере; fetch — фикстуры + опц. override
// (url → Response) для отдельных тестов
function renderFlow(
  msg: Message,
  override?: (url: string) => Response | undefined,
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      if (override) {
        const res = override(url)
        if (res) return res
      }
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
  return render(
    <StudioProvider>
      <FlowInspector msg={msg} />
    </StudioProvider>,
  )
}

// jsdom: execCommand-фолбэк копирования (если clipboard API недоступен).
// jsdom не определяет document.execCommand — вешаем свой spy-пропр
function stubExecCommand(): ReturnType<typeof vi.fn> {
  const spy = vi.fn(() => true)
  Object.defineProperty(document, 'execCommand', {
    value: spy,
    configurable: true,
    writable: true,
  })
  return spy
}

beforeEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// ── Свёрнутая строка ────────────────────────────────────────────────────────
describe('FlowInspector — свёрнутая строка', () => {
  it('строка «Обработка» с сегментами RAG → Prompt → LLM → total (все данные есть)', async () => {
    renderFlow(FULL_MSG)
    const row = await screen.findByRole('button', { name: /Обработка:/ })
    // RAG 3 чанка (склонение), оценка Prompt (эвристика 0.44 tok/символ),
    // модель, total из msg.usage
    expect(row).toHaveTextContent(
      /Обработка: RAG 3 чанка → Prompt ≈\d+ tok → LLM qwen3.8-27b → 150 tok/,
    )
    expect(row.className).toContain('flow-row')
    expect(row).toHaveAttribute('aria-expanded', 'false')
  })

  it('сегменты без данных пропускаются (нет request_id/usage/model — только RAG)', async () => {
    renderFlow(RAG_ONLY_MSG)
    const row = await screen.findByRole('button', { name: /Обработка:/ })
    // RAG-чанки есть, оценка Prompt посчиталась (config/память из state)
    expect(row).toHaveTextContent(/Обработка: RAG 3 чанка → Prompt ≈\d+ tok/)
    expect(row.textContent).not.toContain('LLM qwen')
    expect(row.textContent).not.toContain('150 tok')
  })
})

// ── Условие рендера (уровень ChatPanel) ────────────────────────────────────
describe('ChatPanel — FlowInspector рендерится только при данных', () => {
  it('у assistant с request_id — строка есть; у plain-assistant — нет', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Диалог', created: '2026-10-02', message_count: 4 }],
          })
        }
        if (url === '/api/dialogues/d1') {
          return jsonResponse({
            dialogue: {
              messages: [
                { role: 'user', content: 'вопрос' },
                { role: 'assistant', ...FULL_MSG },
                { role: 'user', content: 'ещё' },
                { role: 'assistant', ...PLAIN_MSG },
              ],
            },
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    await screen.findByText('В приведённых отрывках телефон не упоминается.')
    await screen.findByText('Простой ответ без данных.')
    // ровно одна строка-чип — у сообщения с request_id/rag_context
    expect(document.querySelectorAll('.flow-row')).toHaveLength(1)
  })
})

// ── Раскрытие: карточка и шаги ──────────────────────────────────────────────
describe('FlowInspector — раскрытая карточка', () => {
  it('клик по строке — карточка: RAG-шаг (query/recall/чанки/score/«из #N»)', async () => {
    renderFlow(FULL_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))

    // RAG-шаг: заголовок, query, recall + reranked.
    // Текст строки разбит на несколько text-нод (JSX-выражения) — ищем
    // функцией по полному textContent элемента
    expect(await screen.findByText('RAG · retrieval')).toBeInTheDocument()
    expect(
      screen.getByText(
        (content, el) =>
          el?.classList.contains('flow-step-detail') &&
          content.includes('«Какой телефон был у героя?»') &&
          content.includes('recall 50 (reranked)'),
      ),
    ).toBeInTheDocument()
    // чанки-чипы: file · section + score с цветовой шкалой
    const chunk1 = screen.getByText('The_Cherry_Orchard.md · Act III')
    expect(chunk1.className).toContain('flow-chunk')
    const scores = screen.getAllByText(/^0\.\d{2}$/)
    expect(scores.map((s) => s.textContent)).toEqual(['0.82', '0.61', '0.30'])
    expect(scores[0].className).toContain('flow-score-g') // ≥0.8 зелёный
    expect(scores[1].className).toContain('flow-score-y') // 0.5–0.8 жёлтый
    expect(scores[2].className).toContain('flow-score-r') // <0.5 красный
    // stage1_rank 25 > 20 → «из #25» (у второго чанка stage1_rank=3 → нет)
    expect(screen.getByText('из #25')).toBeInTheDocument()
    expect(screen.queryByText('из #3')).not.toBeInTheDocument()
  })

  it('Prompt-шаг: чипы блоков (базовый/профиль/инварианты/память WM/RAG-блок) + «≈ N tok system»', async () => {
    renderFlow(FULL_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))

    // чипы блоков: базовый/память/RAG-блок — из state (немедленно);
    // профиль/инварианты — ленивый fetch /api/rules (ждём его)
    expect(await screen.findByText('Prompt-сборка')).toBeInTheDocument()
    expect(screen.getByText(/базовый ≈\d+/)).toBeInTheDocument()
    expect(await screen.findByText(/профиль ≈\d+/)).toBeInTheDocument()
    expect(screen.getByText(/инварианты ≈\d+/)).toBeInTheDocument()
    expect(screen.getByText('память WM ≈40')).toBeInTheDocument()
    expect(screen.getByText(/RAG-блок ≈\d+/)).toBeInTheDocument()
    // LT пустой (entries=0, tokens_est=0) — чипа нет
    expect(screen.queryByText(/память LT/)).not.toBeInTheDocument()
    // итог
    expect(screen.getByText(/≈ \d+ tok system/)).toBeInTheDocument()
  })

  it('LLM-шаг: модель; Ответ-шаг: usage «prompt 100 · completion 50 · total 150»', async () => {
    renderFlow(FULL_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))

    expect(await screen.findByText('LLM')).toBeInTheDocument()
    expect(screen.getByText('qwen3.8-27b')).toBeInTheDocument()
    expect(screen.getByText('Ответ')).toBeInTheDocument()
    expect(screen.getByText('prompt 100 · completion 50 · total 150')).toBeInTheDocument()
  })

  it('Ответ без usage — «—» (data-driven)', async () => {
    renderFlow(RAG_ONLY_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    expect(await screen.findByText('Ответ')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('клик по заголовку карточки — сворачивается в строку', async () => {
    renderFlow(FULL_MSG)
    // свёрнуто: строка-чип, карточки нет
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    // раскрыто: карточка с шагами и пунктом JSON (request_id есть)
    await screen.findByText('RAG · retrieval')
    expect(screen.getByRole('button', { name: /Обработка запроса/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Запрос JSON · req_1/ })).toBeInTheDocument()

    // клик по заголовку — карточка исчезает, строка возвращается
    fireEvent.click(screen.getByRole('button', { name: /Обработка запроса/ }))
    await waitFor(() =>
      expect(screen.queryByText('RAG · retrieval')).not.toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /Обработка:/ })).toBeInTheDocument()
    // пункт JSON убрался вместе с карточкой
    expect(screen.queryByRole('button', { name: /Запрос JSON · req_1/ })).not.toBeInTheDocument()
  })
})

// ── Пункт «Запрос JSON» ─────────────────────────────────────────────────────
describe('FlowInspector — «Запрос JSON»', () => {
  it('клик по toggle → lazy fetch /api/requests/1 → pre с pretty-JSON поля `request`', async () => {
    // Отложенный ответ на /api/requests/1: детерминированно застолбить
    // состояние «Загрузка…», затем разрешить
    let resolveReq: (r: Response) => void = () => undefined
    const reqGate = new Promise<Response>((resolve) => {
      resolveReq = resolve
    })
    const fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      if (url === '/api/requests/1') return reqGate
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    })
    vi.stubGlobal('fetch', fetchSpy)
    render(
      <StudioProvider>
        <FlowInspector msg={FULL_MSG} />
      </StudioProvider>,
    )
    // до раскрытия JSON fetch /requests/1 НЕ идёт
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    const jsonToggle = await screen.findByRole('button', { name: /Запрос JSON · req_1/ })
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes('/requests/1'))).toHaveLength(0)

    fireEvent.click(jsonToggle)
    // fetch не разрешён → «Загрузка…»; сам fetch уже ушёл
    expect(await screen.findByText('Загрузка…')).toBeInTheDocument()
    expect(
      fetchSpy.mock.calls.some((c) => normalizeUrl(c[0]) === '/api/requests/1'),
    ).toBe(true)
    resolveReq(jsonResponse(REQUEST_1))
    // тело — pretty-JSON из поля `request` записи журнала (НЕ body)
    const preEl = await screen.findAllByText(/"temperature": 0\.7/)
    expect(preEl.length).toBeGreaterThan(0)
    const preBox = preEl[0].closest('pre')
    expect(preBox?.className).toContain('flow-json-pre')
    expect(preBox?.textContent).toContain('"temperature": 0.7')
    expect(preBox?.textContent).toContain('"model": "qwen3.8-27b"')
    expect(preBox?.textContent).toContain('Какой телефон был у героя?')
  })

  it('сворачивание/повторное раскрытие JSON — без рефетча (кэш в состоянии)', async () => {
    const fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    })
    vi.stubGlobal('fetch', fetchSpy)
    render(
      <StudioProvider>
        <FlowInspector msg={FULL_MSG} />
      </StudioProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    const jsonToggle = await screen.findByRole('button', { name: /Запрос JSON · req_1/ })
    fireEvent.click(jsonToggle)
    await screen.findAllByText(/"temperature": 0\.7/)

    // свернуть JSON (toggle) и раскрыть снова
    fireEvent.click(jsonToggle)
    await waitFor(() =>
      expect(screen.queryByText(/"temperature": 0\.7/)).not.toBeInTheDocument(),
    )
    fireEvent.click(jsonToggle)
    await screen.findAllByText(/"temperature": 0\.7/)
    // ровно один fetch /requests/1
    expect(
      fetchSpy.mock.calls.filter((c) => normalizeUrl(c[0]) === '/api/requests/1'),
    ).toHaveLength(1)
  })

  it('клик «Скопировать» → clipboard.writeText с pretty-JSON; кнопка «Скопировано»', async () => {
    const { writeText } = stubClipboard()
    renderFlow(FULL_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Запрос JSON · req_1/ }))
    await screen.findAllByText(/"temperature": 0\.7/)

    fireEvent.click(await screen.findByRole('button', { name: /Скопировать/ }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(REQUEST_1.request, null, 2))
    // «Скопировано» (1.5s)
    expect(await screen.findByRole('button', { name: /Скопировано/ })).toBeInTheDocument()
  })

  it('clipboard API недоступен → фолбэк execCommand (textarea + select + copy)', async () => {
    const { writeText } = stubClipboard()
    writeText.mockRejectedValueOnce(new Error('denied'))
    const execSpy = stubExecCommand()
    renderFlow(FULL_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Запрос JSON · req_1/ }))
    await screen.findAllByText(/"temperature": 0\.7/)

    fireEvent.click(await screen.findByRole('button', { name: /Скопировать/ }))
    await waitFor(() => expect(execSpy).toHaveBeenCalledWith('copy'))
    // «Скопировано» показывается даже по фолбэку
    expect(await screen.findByRole('button', { name: /Скопировано/ })).toBeInTheDocument()
  })

  it('ошибка fetch /requests/1 → «Ошибка: …» (без падения карточки)', async () => {
    renderFlow(
      FULL_MSG,
      (url) => (url === '/api/requests/1' ? jsonResponse({ detail: 'Запрос не найден' }, 404) : undefined),
    )
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Запрос JSON · req_1/ }))
    expect(await screen.findByText('Ошибка: Запрос не найден')).toBeInTheDocument()
  })

  it('без request_id → пункта «Запрос JSON» нет (даже с RAG-чанками)', async () => {
    renderFlow(RAG_ONLY_MSG)
    fireEvent.click(await screen.findByRole('button', { name: /Обработка:/ }))
    await screen.findByText('RAG · retrieval')
    expect(screen.queryByText(/Запрос JSON/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Запрос JSON · req_/ })).not.toBeInTheDocument()
  })
})
