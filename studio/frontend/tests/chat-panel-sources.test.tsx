// День 24 (задача 6): wiring SourcesPanel в ChatPanel — панель «📖 Источники
// и цитаты» (мокап B) под assistant-ответом, СОСУЩЕСТВУЕТ с существующим
// RagContextInspector (оба инспектора — решение пользователя, старый не
// удалён). Dont-know (A′): красная карточка «🚫 Не знаю» вместо чанк-
// карточек; dont-know done (instant done без дельт) — спиннер не зависает,
// панель видна сразу. Старые сообщения (без chunk_id/dont_know) — без краха.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import type { RagContext } from '../src/api'
import ChatPanel from '../src/components/ChatPanel'

// loadAll-контракты StudioProvider (паттерн chat-panel.test.tsx)
const API_FIXTURES: Record<string, unknown> = {
  '/api/config': {
    model: 'qwen3.8-27b',
    temperature: 0.7,
    max_tokens: 1024,
    system_prompt: 'Ты — ассистент.',
  },
  '/api/memory': {
    active_id: 'd1',
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': {
    models: [{ id: 'qwen3.8-27b', context_limit: 32768 }],
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

// loadAll-контракты + активный диалог d1 с заданными сообщениями
function stubDialogueFetch(messages: unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = normalizeUrl(input)
      if (url === '/api/dialogues') {
        return jsonResponse({
          active_id: 'd1',
          dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0 }],
        })
      }
      if (url === '/api/dialogues/d1') {
        return jsonResponse({ dialogue: { messages } })
      }
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
}

function renderPanel() {
  return render(
    <StudioProvider>
      <ChatPanel />
    </StudioProvider>,
  )
}

// День 24 контракт: у чанка аддитивно chunk_id (stem-strategy-NNNN)
const RAG_CTX_24: RagContext = {
  recall_total: 50,
  reranked: true,
  chunks: [
    {
      rank: 1,
      chunk_id: 'onegin-structural-0042',
      file: 'a.md',
      section: 'Секция',
      score: 0.94,
      stage1_rank: 3,
      reranked: true,
      text: 'Верба над самою рекой',
    },
  ],
}

// Старый контракт (до дня 24): без chunk_id, без dont_know
const RAG_CTX_LEGACY: RagContext = {
  recall_total: 50,
  reranked: false,
  chunks: [
    {
      rank: 1,
      file: 'a.md',
      section: 'Секция',
      score: 0.7,
      stage1_rank: null,
      reranked: false,
      text: 'Старый чанк без chunk_id',
    } as unknown as RagContext['chunks'][number],
  ],
}

// День 24 dont-know (A′): поиск успешен, 0 релевантных чанков
const RAG_CTX_DONT_KNOW: RagContext = {
  recall_total: 50,
  reranked: false,
  chunks: [],
  dont_know: true,
}

const DONT_KNOW_TEXT =
  'Не знаю. В базе знаний не нашлось релевантных материалов. Уточните, пожалуйста: о каком документе или теме вы спрашиваете?'

beforeEach(() => {
  localStorage.clear()
})

describe('ChatPanel — SourcesPanel под assistant-ответом (день 24, задача 6)', () => {
  it('assistant с rag_context.chunks → Источники-панель (sources) И RagContextInspector оба в DOM, панель под ответом', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос' },
      { role: 'assistant', content: 'Ответ с RAG [1]', model: 'm', rag_context: RAG_CTX_24 },
    ])
    const { container } = renderPanel()
    await screen.findByText('Ответ с RAG [1]')

    // оба инспектора в DOM
    const inspector = container.querySelector('.msg.assistant .rag-ctx')
    const panel = container.querySelector('.msg.assistant .src-panel')
    expect(inspector).not.toBeNull()
    expect(panel).not.toBeNull()
    // панель — РЯДом с инспектором, но ПОД ответом и ниже инспектора (мокап B)
    const msg = container.querySelector('.msg.assistant') as HTMLElement
    expect(panel!.parentElement).toBe(msg)
    expect(
      (msg.querySelector('.msg-text') as Element).compareDocumentPosition(panel) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(inspector!.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // панель рендерит источник: chunk_id + цитата
    expect(screen.getByText('onegin-structural-0042')).toBeInTheDocument()
    expect(screen.getByText('Верба над самою рекой')).toBeInTheDocument()
  })

  it('rag_context.dont_know=true → красная карточка «🚫 Не знаю», чанк-карточки НЕ рендерятся', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос без совпадений' },
      { role: 'assistant', content: DONT_KNOW_TEXT, model: 'm', rag_context: RAG_CTX_DONT_KNOW },
    ])
    const { container } = renderPanel()
    await screen.findByText(DONT_KNOW_TEXT)

    const dk = container.querySelector('.msg.assistant .src-dontknow')
    expect(dk).not.toBeNull()
    expect(dk!.textContent).toContain('🚫 Не знаю')
    expect(dk!.textContent).toContain('Уточните вопрос: о каком документе вы спрашиваете?')
    // чанки пусты — карточек источников нет
    expect(container.querySelectorAll('.src-card')).toHaveLength(0)
    expect(container.querySelector('.src-panel')).toBeNull()
  })

  it('legacy-сообщение (без dont_know/chunk_id) → рендер без краха, RagContextInspector как до дня 24', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос' },
      { role: 'assistant', content: 'Ответ до дня 24', model: 'm', rag_context: RAG_CTX_LEGACY },
    ])
    const { container } = renderPanel()
    // рендер не упал: ответ на месте
    await screen.findByText('Ответ до дня 24')
    // старый инспектор — как раньше (toggle свёрнут)
    const toggle = screen.getByRole('button', { name: /Показать извлечённый контекст RAG \(1 чанков из 50\)/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    // панель источников (если рендерится) — без crash и без чужого chunk_id
    expect(container.querySelectorAll('.src-id')).toHaveLength(0)
  })

  it('dont-know done (instant done, без дельт) → спиннер не зависает, панель видна сразу', async () => {
    stubDialogueFetch([
      { role: 'user', content: 'вопрос без совпадений' },
      { role: 'assistant', content: DONT_KNOW_TEXT, model: 'm', rag_context: RAG_CTX_DONT_KNOW },
    ])
    const { container } = renderPanel()
    // done уже пришёл (streaming=false): карточка видна сразу, без ожидания
    await screen.findByText(DONT_KNOW_TEXT)
    expect(container.querySelector('.msg.assistant .src-dontknow')).not.toBeNull()
    // caret-спиннер стрима в сообщении нет (done → стрим закрыт)
    expect(container.querySelector('.msg.assistant .caret')).toBeNull()
  })
})
