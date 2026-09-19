// Хелперы профиля пользователя (день 12): POST /api/profile и
// POST /api/profile/action. fetch — стаб; проверяем url/тело запроса
// и парсинг ответа {profile}.
import { describe, expect, it, vi } from 'vitest'
import { apiPostProfile, apiPostProfileAction } from '../src/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

const activeProfile = { status: 'active', interview: false, name: 'Иван', role: '', tone: '', taboos: '' }
const declinedProfile = { status: 'declined', interview: false, name: '', role: '', tone: '', taboos: '' }

describe('apiPostProfile — POST /api/profile', () => {
  it('шлёт {dialogue_id, name, role, tone, taboos}, возвращает {profile}', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: normalizeUrl(input), body: JSON.parse(String(init?.body)) })
        return jsonResponse({ profile: activeProfile })
      }),
    )
    const r = await apiPostProfile('d1', 'Иван', '', '', '')
    expect(r.profile).toEqual(activeProfile)
    expect(calls).toEqual([
      { url: '/api/profile', body: { dialogue_id: 'd1', name: 'Иван', role: '', tone: '', taboos: '' } },
    ])
  })

  it('404 — ApiError с RU-detail из тела ответа', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Диалог «nope» не найден' }, 404)))
    await expect(apiPostProfile('nope', 'Иван', '', '', '')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: 'Диалог «nope» не найден',
    })
  })
})

describe('apiPostProfileAction — POST /api/profile/action', () => {
  it('шлёт {dialogue_id, action}, возвращает {profile}', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: normalizeUrl(input), body: JSON.parse(String(init?.body)) })
        return jsonResponse({ profile: declinedProfile })
      }),
    )
    const r = await apiPostProfileAction('d1', 'decline')
    expect(r.profile).toEqual(declinedProfile)
    expect(calls).toEqual([{ url: '/api/profile/action', body: { dialogue_id: 'd1', action: 'decline' } }])
  })
})
