// Хелперы инвариантов (день 14, новая схема):
// GET /api/invariants → {invariants:[{id,title,description,forbidden,is_active}]}
// POST /api/invariants {title,description,forbidden,is_active} → {invariant}
// POST /api/invariants/{id}/toggle → {invariant}
// DELETE /api/invariants/{id} → {ok}
// fetch — стаб; проверяем url/тело запроса и парсинг ответа.
import { describe, expect, it, vi } from 'vitest'
import { addInvariant, deleteInvariant, getInvariants, toggleInvariant } from '../src/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

const inv = {
  id: 'inv-1',
  title: 'Язык ответа',
  description: 'Отвечай на русском',
  forbidden: ['anglicisms', 'списки'],
  is_active: true,
}

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

  it('записи старой схемы {key, value} маппятся в {title, description}', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ invariants: [{ id: 'inv-old', key: 'lang', value: 'русский' }] }),
      ),
    )
    const list = await getInvariants()
    expect(list).toEqual([
      { id: 'inv-old', title: 'lang', description: 'русский', forbidden: [], is_active: true },
    ])
  })
})

describe('addInvariant — POST /api/invariants', () => {
  it('шлёт {title, description, forbidden, is_active: true} по умолчанию', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: normalizeUrl(input), body: JSON.parse(String(init?.body)) })
        return jsonResponse({ invariant: inv })
      }),
    )
    const r = await addInvariant('Язык ответа', 'Отвечай на русском', ['anglicisms'])
    expect(r).toEqual(inv)
    expect(calls).toEqual([
      {
        url: '/api/invariants',
        body: { title: 'Язык ответа', description: 'Отвечай на русском', forbidden: ['anglicisms'], is_active: true },
      },
    ])
  })

  it('forbidden-массив сериализуется в тело запроса целиком', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: normalizeUrl(input), body: JSON.parse(String(init?.body)) })
        return jsonResponse({ invariant: inv })
      }),
    )
    await addInvariant('t', 'd', ['a', 'b', 'c'], true)
    expect(calls).toEqual([
      { url: '/api/invariants', body: { title: 't', description: 'd', forbidden: ['a', 'b', 'c'], is_active: true } },
    ])
  })

  it('явный is_active: false уходит в тело запроса', async () => {
    const calls: { body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ body: JSON.parse(String(init?.body)) })
        return jsonResponse({ invariant: inv })
      }),
    )
    await addInvariant('t', 'd', [], false)
    expect(calls).toEqual([{ body: { title: 't', description: 'd', forbidden: [], is_active: false } }])
  })

  it('ответ старой схемы {key, value} маппится в новую (бэкенд не мигрирован)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ invariant: { id: 'inv-old', key: 'lang', value: 'русский' } }),
      ),
    )
    const r = await addInvariant('lang', 'русский', [])
    expect(r).toEqual({ id: 'inv-old', title: 'lang', description: 'русский', forbidden: [], is_active: true })
  })

  it('400 — ApiError с RU-detail из тела ответа', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Пустой title' }, 400)))
    await expect(addInvariant('', 'v', [])).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: 'Пустой title',
    })
  })
})

describe('toggleInvariant — POST /api/invariants/{id}/toggle', () => {
  it('шлёт POST по id, возвращает {invariant}', async () => {
    const calls: string[] = []
    const flipped = { ...inv, is_active: false }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${normalizeUrl(input)}`)
        return jsonResponse({ invariant: flipped })
      }),
    )
    const r = await toggleInvariant('inv-1')
    expect(r).toEqual(flipped)
    expect(calls).toEqual(['POST /api/invariants/inv-1/toggle'])
  })

  it('id в URL экранируется', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push(`${init?.method ?? 'GET'} ${normalizeUrl(input)}`)
        return jsonResponse({ invariant: { ...inv, id: 'a/b' } })
      }),
    )
    await toggleInvariant('a/b')
    expect(calls).toEqual(['POST /api/invariants/a%2Fb/toggle'])
  })

  it('404 — ApiError с RU-detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Инвариант не найден' }, 404)))
    await expect(toggleInvariant('inv-x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: 'Инвариант не найден',
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
