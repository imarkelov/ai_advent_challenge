import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { DialogueMeta } from '../src/state'
import { StudioProvider } from '../src/state'
import Sidebar from '../src/components/Sidebar'

const d1: DialogueMeta = { id: 'd1', title: 'Первый', created: '2026-09-18', message_count: 3 }
const d2: DialogueMeta = { id: 'd2', title: 'Второй', created: '2026-09-19', message_count: 1 }
const d3: DialogueMeta = { id: 'd3', title: 'Третий', created: '2026-09-19', message_count: 0 }

// WM разный у диалогов: items задаёт содержимое «текущей задачи» активного
function memoryFor(activeId: string | null, items: Record<string, string>) {
  return {
    active_id: activeId,
    dialogue: { message_count: 3, tokens_est: 20 },
    working: { entries: Object.keys(items).length, tokens_est: 5, items },
    long_term: { entries: 1, tokens_est: 4, items: { fact: 'значение' } },
  }
}

const BASE_FIXTURES: Record<string, unknown> = {
  '/api/config': { model: 'qwen3.8-27b', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': { models: [] },
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

// Счётчик «Текущая задача» (Working Memory) в сводке памяти
function workingEntries(): string {
  const row = screen.getByText('Текущая задача').closest('li')
  return row?.querySelector('.memory-count')?.textContent ?? ''
}

beforeEach(() => {
  localStorage.clear()
})

describe('Sidebar — переключение диалога обновляет память', () => {
  it('activateDialogue: WM берётся из /memory нового диалога, не от прежнего', async () => {
    const server = {
      activeId: 'd1' as string | null,
      dialogues: [d1, d2],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url === '/api/dialogues/d2/activate') {
          server.activeId = 'd2'
          return jsonResponse({ active_id: 'd2' })
        }
        if (method === 'GET' && url === '/api/dialogues') {
          return jsonResponse({ active_id: server.activeId, dialogues: server.dialogues })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          return jsonResponse({ dialogue: { messages: [] } })
        }
        if (method === 'GET' && url === '/api/dialogues/d2') {
          return jsonResponse({ dialogue: { messages: [] } })
        }
        if (method === 'GET' && url === '/api/memory') {
          const items = server.activeId === 'd2' ? { task: 'задача-d2', note: 'заметка' } : {}
          return jsonResponse(memoryFor(server.activeId, items))
        }
        return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    // начальная загрузка: активен d1 — WM пуст
    await screen.findByText('Текущая задача')
    await waitFor(() => expect(workingEntries()).toBe('0'))

    // клик по строке диалога (не по заголовку) — активация d2
    fireEvent.click(screen.getByRole('button', { name: /Второй/ }))

    // панели памяти перечитаны: WM d2 — 2 записи
    await waitFor(() => expect(workingEntries()).toBe('2'))
  })
})

describe('Sidebar — удаление диалогов чекбоксами', () => {
  it('выбор 2 диалогов → «Удалить (2)» → confirm → 2 DELETE → список перечитан', async () => {
    const server = {
      activeId: 'd1' as string | null,
      dialogues: [d1, d2, d3],
      deleted: [] as string[],
    }
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        const method = init?.method ?? 'GET'
        if (method === 'DELETE' && url.startsWith('/api/dialogues/')) {
          const id = url.slice('/api/dialogues/'.length)
          server.deleted.push(id)
          server.dialogues = server.dialogues.filter((d) => d.id !== id)
          if (server.activeId === id) {
            server.activeId = server.dialogues[server.dialogues.length - 1]?.id ?? null
          }
          return jsonResponse({ ok: true })
        }
        if (method === 'GET' && url === '/api/dialogues') {
          return jsonResponse({ active_id: server.activeId, dialogues: server.dialogues })
        }
        if (method === 'GET' && url === '/api/dialogues/d1') {
          return jsonResponse({ dialogue: { messages: [{ role: 'user', content: 'привет' }] } })
        }
        if (method === 'GET' && url === '/api/memory') {
          return jsonResponse(memoryFor(server.activeId, {}))
        }
        return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    const boxes = await screen.findAllByRole('checkbox')
    expect(boxes).toHaveLength(3)
    expect(screen.queryByRole('button', { name: 'Удалить (2)' })).toBeNull()

    fireEvent.click(boxes[1])
    fireEvent.click(boxes[2])
    fireEvent.click(screen.getByRole('button', { name: 'Удалить (2)' }))

    await waitFor(() => {
      expect(screen.queryByText('Второй')).toBeNull()
      expect(screen.queryByText('Третий')).toBeNull()
    })
    expect(confirmSpy).toHaveBeenCalledWith('Удалить 2 диалог(а)?')
    expect(server.deleted).toEqual(['d2', 'd3'])
    // список перечитан: остался только Первый, выбор сброшен
    expect(screen.getByText('Первый')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Удалить \(/ })).toBeNull()
  })
})

describe('Sidebar — inline-ренейм диалога', () => {
  function renameFetchMock(server: { dialogues: DialogueMeta[]; renamed: unknown[] }) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url === '/api/dialogues/d1/rename') {
        const body = JSON.parse(String(init.body)) as { title: string }
        server.renamed.push(body)
        server.dialogues[0] = { ...server.dialogues[0], title: body.title }
        return jsonResponse({ dialogue: server.dialogues[0] })
      }
      if (method === 'GET' && url === '/api/dialogues') {
        return jsonResponse({ active_id: 'd1', dialogues: server.dialogues })
      }
      if (method === 'GET' && url === '/api/dialogues/d1') {
        return jsonResponse({ dialogue: { messages: [] } })
      }
      if (method === 'GET' && url === '/api/memory') {
        return jsonResponse(memoryFor('d1', {}))
      }
      return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
    })
  }

  it('клик по заголовку → инпут → Enter → POST /rename → title обновлён в списке', async () => {
    const server = {
      dialogues: [d1],
      renamed: [] as unknown[],
    }
    vi.stubGlobal('fetch', renameFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    const title = await screen.findByText('Первый')
    fireEvent.click(title)

    const input = screen.getByRole('textbox') as HTMLInputElement
    expect(input.value).toBe('Первый')
    fireEvent.change(input, { target: { value: 'Новый заголовок' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(screen.queryByText('Новый заголовок')).toBeTruthy())
    expect(server.renamed).toEqual([{ title: 'Новый заголовок' }])
    expect(screen.queryByText('Первый')).toBeNull()
  })

  it('Esc — инпут закрывается, POST /rename не отправляется', async () => {
    const server = {
      dialogues: [d1],
      renamed: [] as unknown[],
    }
    vi.stubGlobal('fetch', renameFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    fireEvent.click(await screen.findByText('Первый'))
    const input = screen.getByRole('textbox') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Отменю' } })
    fireEvent.keyDown(input, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
    expect(server.renamed).toEqual([])
    expect(screen.getByText('Первый')).toBeTruthy()
  })
})
