import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost, apiUrl } from '../src/api'

describe('api helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('builds urls against the api prefix', () => {
    expect(apiUrl('/health')).toBe('http://127.0.0.1:8100/api/health')
  })

  it('apiGet returns parsed json', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 })))
    await expect(apiGet('/health')).resolves.toEqual({ status: 'ok' })
  })

  it('apiGet throws russian detail on error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'нет связи' }), { status: 400 })))
    await expect(apiGet('/health')).rejects.toThrow('нет связи')
  })

  it('apiPost sends json body', async () => {
    const fetchMock = vi.fn(
      Object.assign(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ saved: true }), { status: 200 }), {
        mock: { calls: [] as unknown[][] },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const result = await apiPost('/doc/file', { path: 'kb/a.md', content: 'x' })
    expect(result).toEqual({ saved: true })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ path: 'kb/a.md', content: 'x' })
  })
})
