// День 22: свитч «RAG» в шапке чата (per-диалог).
// Контракт API: POST /api/dialogues/{id}/rag {rag: bool} → 200;
// GET /api/dialogues — у диалога поле rag: bool|null (null = по глобальному
// /api/kb/settings). Effective = dialogue.rag ?? globalRag; ошибка
// загрузки globalRag → fallback true (дефолт дня 21).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider, useStudio } from '../src/state'
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

// Проба: ловит globalRag из состояния провайдера (null — ещё загружается)
let capturedGlobalRag: boolean | null = null
function GlobalRagProbe() {
  const { state } = useStudio()
  capturedGlobalRag = state.globalRag
  return null
}

// Контракты: активный диалог d1 с полем rag + GET /api/kb/settings
// (globalRag). POST /api/dialogues/d1/rag — «бэкенд»: фиксирует rag и
// отдаёт его в последующих GET /api/dialogues (авторитетное перечитывание).
function stubRagFetch(
  initialDialogueRag: boolean | null,
  globalRag: boolean,
  kbSettingsError = false,
) {
  const server = { dialogueRag: initialDialogueRag }
  const posted: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url === '/api/dialogues/d1/rag') {
        const body = JSON.parse(String(init.body))
        posted.push({ url, body })
        server.dialogueRag = body.rag
        return jsonResponse({ rag: body.rag })
      }
      if (method === 'GET' && url === '/api/dialogues') {
        return jsonResponse({
          active_id: 'd1',
          dialogues: [
            { id: 'd1', title: 'Диалог', created: '2026-10-02', message_count: 0, rag: server.dialogueRag },
          ],
        })
      }
      if (method === 'GET' && url === '/api/dialogues/d1') {
        return jsonResponse({ dialogue: { messages: [] } })
      }
      if (method === 'GET' && url === '/api/kb/settings') {
        if (kbSettingsError) {
          return new Response('HTTP 500', { status: 500, headers: { 'Content-Type': 'application/json' } })
        }
        return jsonResponse({
          agent_loop: true,
          rag: globalRag,
          rag_top_k: 3,
          strategy: 'structural',
          embedder: 'hash',
          reranker: 'off',
          rag_recall: 50,
        })
      }
      return jsonResponse(API_FIXTURES[url] ?? { ok: true })
    }),
  )
  return { posted }
}

beforeEach(() => {
  localStorage.clear()
  capturedGlobalRag = null
})

describe('ChatPanel — свитч «RAG» в шапке чата (день 22, per-диалог)', () => {
  it('dialogue.rag=null + global rag=true → свитч ВКЛ (по глобальной настройке)', async () => {
    stubRagFetch(null, true)
    render(
      <StudioProvider>
        <GlobalRagProbe />
        <ChatPanel />
      </StudioProvider>,
    )
    const sw = await screen.findByRole('switch')
    // ждём, пока глобальная настройка дойдёт (иначе ON — просто фолбэк)
    await waitFor(() => expect(capturedGlobalRag).toBe(true))
    expect(sw).toBeChecked()
  })

  it('dialogue.rag=false + global rag=true → свитч ВЫКЛ (per-диалог override побеждает)', async () => {
    stubRagFetch(false, true)
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const sw = await screen.findByRole('switch')
    expect(sw).not.toBeChecked()
  })

  it('dialogue.rag=true + global rag=false → свитч ВКЛ (override работает в обе стороны)', async () => {
    stubRagFetch(true, false)
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    const sw = await screen.findByRole('switch')
    expect(sw).toBeChecked()
  })

  it('клик → POST /api/dialogues/d1/rag {rag:false}; после успеха состояние флипается', async () => {
    const { posted } = stubRagFetch(null, true)
    render(
      <StudioProvider>
        <GlobalRagProbe />
        <ChatPanel />
      </StudioProvider>,
    )
    const sw = await screen.findByRole('switch')
    await waitFor(() => expect(capturedGlobalRag).toBe(true))
    expect(sw).toBeChecked() // effective = null ?? true

    fireEvent.click(sw)

    // клик отправил POST с аргументом нового значения
    await waitFor(() =>
      expect(posted).toContainEqual({ url: '/api/dialogues/d1/rag', body: { rag: false } }),
    )
    // авторитетное перечитывание: бэкенд отдал rag=false → свитч ВЫКЛ
    await waitFor(() => expect(sw).not.toBeChecked())
  })

  it('ошибка GET /api/kb/settings (500) + dialogue.rag=null → fallback true (дефолт дня 21)', async () => {
    stubRagFetch(null, false, true) // globalRag «должен быть» false, но запрос упал
    render(
      <StudioProvider>
        <GlobalRagProbe />
        <ChatPanel />
      </StudioProvider>,
    )
    const sw = await screen.findByRole('switch')
    // fallback: globalRag в состоянии — true
    await waitFor(() => expect(capturedGlobalRag).toBe(true))
    expect(sw).toBeChecked()
  })
})
