// Хелперы инвариантов (день 14): GET /api/invariants,
// POST /api/invariants {key, value}, DELETE /api/invariants/{id}.
// fetch — стаб; проверяем url/тело запроса и парсинг ответа.
import { describe, expect, it, vi } from 'vitest'
import { addInvariant, deleteInvariant, getInvariants } from '../src/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

const inv = { id: 'inv-1', key: 'language', value: 'отвечай на русском' }

describe('getInvariants — GET /api/invariants', () => {
  it('возвращает массив инвариантов из {invariants}', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${normalizeUrl(input)}`)
        return jsonResponse({ invariants: [inv] })
      }),
    )
    const list = await getInvariants()
    expect(list).toEqual([inv])
    expect(calls).toEqual(['GET /api/invariants'])
  })

  it('нет поля invariants (старый бэкенд) → пустой список', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ok: true })))
    await expect(getInvariants()).resolves.toEqual([])
  })
})

describe('addInvariant — POST /api/invariants', () => {
  it('шлёт {key, value}, возвращает {invariant}', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: normalizeUrl(input), body: JSON.parse(String(init?.body)) })
        return jsonResponse({ invariant: inv })
      }),
    )
    const r = await addInvariant('language', 'отвечай на русском')
    expect(r).toEqual(inv)
    expect(calls).toEqual([{ url: '/api/invariants', body: { key: 'language', value: 'отвечай на русском' } }])
  })

  it('400 — ApiError с RU-detail из тела ответа', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Пустой key' }, 400)))
    await expect(addInvariant('', 'v')).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: 'Пустой key',
    })
  })
})

describe('deleteInvariant — DELETE /api/invariants/{id}', () => {
  it('шлёт DELETE по id', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${normalizeUrl(input)}`)
        return jsonResponse({ ok: true })
      }),
    )
    await deleteInvariant('inv-1')
    expect(calls).toEqual(['DELETE /api/invariants/inv-1'])
  })

  it('id в URL экранируется', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${normalizeUrl(input)}`)
        return jsonResponse({ ok: true })
      }),
    )
    await deleteInvariant('a/b')
    expect(calls).toEqual(['DELETE /api/invariants/a%2Fb'])
  })
})
