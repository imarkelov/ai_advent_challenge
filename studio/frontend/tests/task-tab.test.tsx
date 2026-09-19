import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import TaskTab from '../src/components/TaskTab'

// Контракты GET /api/* для StudioProvider (loadAll при старте)
const TASK_FIXTURE = {
  active: true, stage: 'execution', paused: true,
  description: 'Сделать кнопку', instruction: '',
  stages: { planning: { output: '1. Шаг', ts: '', verdict: null } },
  retries: 0, error: null, updated: null,
}

const API_FIXTURES: Record<string, unknown> = {
  '/api/config': { model: 'qwen3.8-27b', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
  '/api/dialogues': {
    active_id: 'd1',
    dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, task: TASK_FIXTURE }],
  },
  '/api/memory': {
    active_id: 'd1',
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
    toggles: { st: true, wm: true, lt: true },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}
function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

beforeEach(() => { localStorage.clear() })

describe('TaskTab', () => {
  it('на паузе: описание, стадия, «Продолжить», instruction-форма', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    render(<StudioProvider><TaskTab /></StudioProvider>)
    expect(await screen.findByText('Сделать кнопку')).toBeTruthy()
    // «Планирование» встречается в статус-строке и в summary вывода стадии
    expect(screen.getAllByText('Планирование').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: 'Продолжить' })).toBeTruthy()
    expect(screen.getByLabelText(/Инструкция/)).toBeTruthy()
  })

  it('«Сохранить» — POST /api/task/instruction, поле очищается', async () => {
    const posted: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        if (init?.method === 'POST' && url === '/api/task/instruction') {
          posted.push(JSON.parse(String(init.body)))
          return jsonResponse({ task: { ...TASK_FIXTURE, instruction: 'Используй Kotlin' } })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(<StudioProvider><TaskTab /></StudioProvider>)
    const input = (await screen.findByLabelText(/Инструкция/)) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Используй Kotlin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(posted).toEqual([{ dialogue_id: 'd1', text: 'Используй Kotlin' }]))
    expect((screen.getByLabelText(/Инструкция/) as HTMLInputElement).value).toBe('')
  })

  it('задачи нет: форма описания, «Запустить задачу» при пустом — disabled', async () => {
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
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(<StudioProvider><TaskTab /></StudioProvider>)
    const btn = await screen.findByRole('button', { name: 'Запустить задачу' })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })
})
