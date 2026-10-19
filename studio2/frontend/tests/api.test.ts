import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost, apiUrl, createRunStream, type RunEvent } from '../src/api'

class FakeEventSource {
  static last: FakeEventSource | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeEventSource.last = this
  }
  close() {
    this.closed = true
  }
}

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

  it('createRunStream ignores malformed frames', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const events: RunEvent[] = []
    createRunStream('r1', (event) => events.push(event))
    const source = FakeEventSource.last!
    source.onmessage!({ data: 'not json' })
    source.onmessage!({ data: '[DONE]' })
    source.onmessage!({ data: '{"no": "type"}' })
    source.onmessage!({ data: '{"type": "stage_changed", "stage": "study"}' })
    expect(events).toEqual([{ type: 'stage_changed', stage: 'study' }])
  })

  it('createRunStream reports a broken stream once and closes it', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const events: RunEvent[] = []
    createRunStream('r1', (event) => events.push(event))
    const source = FakeEventSource.last!
    source.onerror!()
    expect(source.closed).toBe(true)
    expect(events).toEqual([{ type: 'error', message: 'поток прерван' }])
  })

  it('createRunStream ignores onerror after run_done', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const events: RunEvent[] = []
    createRunStream('r1', (event) => events.push(event))
    const source = FakeEventSource.last!
    source.onmessage!({ data: '{"type": "run_done", "status": "done"}' })
    source.onerror!()
    expect(source.closed).toBe(true)
    expect(events.map((event) => event.type)).toEqual(['run_done'])
  })

  it('createRunStream ignores onerror after an error frame', () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    const events: RunEvent[] = []
    createRunStream('r1', (event) => events.push(event))
    const source = FakeEventSource.last!
    source.onmessage!({ data: '{"type": "error", "message": "сбой агента"}' })
    source.onerror!()
    expect(source.closed).toBe(true)
    expect(events).toEqual([{ type: 'error', message: 'сбой агента' }])
  })
})
