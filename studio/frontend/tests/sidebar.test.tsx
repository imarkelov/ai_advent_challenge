import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { DialogueMeta } from '../src/state'
import { StudioProvider } from '../src/state'
import Sidebar from '../src/components/Sidebar'
import ChatPanel from '../src/components/ChatPanel'

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

describe('Sidebar — клик по строке = активация', () => {
  it('клик по строке: активация диалога, ренейм НЕ открывается; WM из /memory', async () => {
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

    // клик по строке диалога (названию) — активация d2, ренейм не открывается
    fireEvent.click(screen.getByRole('button', { name: /Второй/ }))
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(document.querySelector('.rename-input')).toBeNull()

    // панели памяти перечитаны: WM d2 — 2 записи
    await waitFor(() => expect(workingEntries()).toBe('2'))
  })
})

describe('Sidebar — ренейм по карандашу', () => {
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

  it('карандаш → инпут → Enter → POST /rename → новый title в списке И в h1 чата', async () => {
    const server = {
      dialogues: [d1],
      renamed: [] as unknown[],
    }
    vi.stubGlobal('fetch', renameFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
        <ChatPanel />
      </StudioProvider>,
    )

    // до ренейма: h1 чата — «Первый», инпут ренейма нет
    await screen.findByRole('heading', { level: 1, name: 'Первый' })
    expect(document.querySelector('.rename-input')).toBeNull()

    fireEvent.click(screen.getByTitle('Переименовать'))
    const input = document.querySelector('.rename-input') as HTMLInputElement
    expect(input.value).toBe('Первый')
    fireEvent.change(input, { target: { value: 'Новый заголовок' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    // title обновился в списке сайдбара И в шапке чата (regression stale-title)
    await waitFor(() => expect(screen.getAllByText('Новый заголовок')).toHaveLength(2))
    expect(server.renamed).toEqual([{ title: 'Новый заголовок' }])
    await screen.findByRole('heading', { level: 1, name: 'Новый заголовок' })
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

    fireEvent.click(await screen.findByTitle('Переименовать'))
    const input = document.querySelector('.rename-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Отменю' } })
    fireEvent.keyDown(input, { key: 'Escape' })

    await waitFor(() => expect(document.querySelector('.rename-input')).toBeNull())
    expect(server.renamed).toEqual([])
    expect(screen.getByText('Первый')).toBeTruthy()
  })
})

describe('Sidebar — одиночное удаление по корзине', () => {
  function deleteFetchMock(
    server: { activeId: string | null; dialogues: DialogueMeta[]; deleted: string[] },
  ) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      const method = init?.method ?? 'GET'
      if (method === 'DELETE' && url.startsWith('/api/dialogues/')) {
        const id = url.slice('/api/dialogues/'.length)
        server.deleted.push(id)
        server.dialogues = server.dialogues.filter((d) => d.id !== id)
        if (server.activeId === id) server.activeId = null
        return jsonResponse({ ok: true })
      }
      if (method === 'GET' && url === '/api/dialogues') {
        return jsonResponse({ active_id: server.activeId, dialogues: server.dialogues })
      }
      if (method === 'GET' && url === '/api/dialogues/d1') {
        return jsonResponse({ dialogue: { messages: [] } })
      }
      if (method === 'GET' && url === '/api/memory') {
        return jsonResponse(memoryFor(server.activeId, {}))
      }
      return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
    })
  }

  it('корзина → confirm → DELETE → строка исчезла', async () => {
    const server = {
      activeId: 'd1' as string | null,
      dialogues: [d1],
      deleted: [] as string[],
    }
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.stubGlobal('fetch', deleteFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    await screen.findByText('Первый')
    fireEvent.click(screen.getByTitle('Удалить'))

    await waitFor(() => expect(screen.queryByText('Первый')).toBeNull())
    expect(confirmSpy).toHaveBeenCalledWith('Удалить диалог «Первый»?')
    expect(server.deleted).toEqual(['d1'])
  })

  it('confirm отклонён — диалог не удалён', async () => {
    const server = {
      activeId: 'd1' as string | null,
      dialogues: [d1],
      deleted: [] as string[],
    }
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    vi.stubGlobal('fetch', deleteFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    await screen.findByText('Первый')
    fireEvent.click(screen.getByTitle('Удалить'))

    expect(confirmSpy).toHaveBeenCalledWith('Удалить диалог «Первый»?')
    expect(server.deleted).toEqual([])
    expect(screen.getByText('Первый')).toBeTruthy()
  })
})

describe('Sidebar — иконка used_task (задача использовалась)', () => {
  function usedTaskFetchMock(dialogues: DialogueMeta[]) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url.endsWith('/activate')) {
        return jsonResponse({ active_id: url.split('/')[3] })
      }
      if (method === 'GET' && url === '/api/dialogues') {
        return jsonResponse({ active_id: dialogues[0]?.id ?? null, dialogues })
      }
      if (method === 'GET' && url.startsWith('/api/dialogues/')) {
        return jsonResponse({ dialogue: { messages: [] } })
      }
      if (method === 'GET' && url === '/api/memory') {
        return jsonResponse(memoryFor(dialogues[0]?.id ?? null, {}))
      }
      return jsonResponse(BASE_FIXTURES[url] ?? { ok: true })
    })
  }

  it('used_task: true → иконка с title="Задача использовалась" в строке; без флага — нет', async () => {
    const used: DialogueMeta = { ...d1, used_task: true }
    vi.stubGlobal('fetch', usedTaskFetchMock([used, d2]))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )
    await screen.findByText('Первый')
    const icons = screen.getAllByTitle('Задача использовалась')
    expect(icons).toHaveLength(1)
    // иконка именно в строке «Первый», а не «Второй»
    const usedRow = screen.getByRole('button', { name: /Первый/ }).closest('li')
    expect(usedRow?.querySelector('[title="Задача использовалась"]')).toBeTruthy()
    const otherRow = screen.getByRole('button', { name: /Второй/ }).closest('li')
    expect(otherRow?.querySelector('[title="Задача использовалась"]')).toBeNull()
  })

  it('used_task отсутствует у всех — иконок нет', async () => {
    vi.stubGlobal('fetch', usedTaskFetchMock([d1, d2]))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )
    await screen.findByText('Первый')
    expect(screen.queryByTitle('Задача использовалась')).toBeNull()
  })
})

describe('Sidebar — режим выбора (чекбоксы)', () => {
  function selectFetchMock(
    server: { activeId: string | null; dialogues: DialogueMeta[]; deleted: string[] },
  ) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    })
  }

  it('переключатель: до — чекбоксов нет, после — все; клик по строке = toggle; повторный клик — выключение', async () => {
    const server = {
      activeId: 'd1' as string | null,
      dialogues: [d1, d2, d3],
      deleted: [] as string[],
    }
    vi.stubGlobal('fetch', selectFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    await screen.findByText('Первый')
    // до режима: чекбоксов нет, иконки-действия видны
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.getAllByTitle('Удалить')).toHaveLength(3)

    fireEvent.click(screen.getByTitle('Режим выбора'))
    const boxes = await screen.findAllByRole('checkbox')
    expect(boxes).toHaveLength(3)
    // в режиме: иконки карандаш/корзина скрыты
    expect(screen.queryByTitle('Переименовать')).toBeNull()
    expect(screen.queryByTitle('Удалить')).toBeNull()

    // клик по строке в режиме выбора тоже переключает чекбокс
    fireEvent.click(screen.getByRole('button', { name: /Второй/ }))
    expect(boxes[1]).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: /Второй/ }))
    expect(boxes[1]).not.toBeChecked()

    // повторный клик по переключателю — режим выключается, выбор сбрасывается
    fireEvent.click(screen.getByTitle('Режим выбора'))
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  })

  it('выбор 2 → «Удалить (2)» → confirm → 2 DELETE → режим выключился', async () => {
    const server = {
      activeId: 'd1' as string | null,
      dialogues: [d1, d2, d3],
      deleted: [] as string[],
    }
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.stubGlobal('fetch', selectFetchMock(server))
    render(
      <StudioProvider>
        <Sidebar />
      </StudioProvider>,
    )

    await screen.findByText('Первый')
    fireEvent.click(screen.getByTitle('Режим выбора'))
    const boxes = await screen.findAllByRole('checkbox')

    // d2 — кликом по строке, d3 — кликом по чекбоксу
    fireEvent.click(screen.getByRole('button', { name: /Второй/ }))
    fireEvent.click(boxes[2])
    fireEvent.click(screen.getByRole('button', { name: 'Удалить (2)' }))

    await waitFor(() => {
      expect(screen.queryByText('Второй')).toBeNull()
      expect(screen.queryByText('Третий')).toBeNull()
    })
    expect(confirmSpy).toHaveBeenCalledWith('Удалить 2 диалог(а)?')
    expect(server.deleted).toEqual(['d2', 'd3'])
    // список перечитан: остался только Первый; режим выключен, кнопки нет
    expect(screen.getByText('Первый')).toBeTruthy()
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /Удалить \(/ })).toBeNull()
  })
})
