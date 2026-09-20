// ProfileTab (день 12) — вкладка «Профили» (4-я, панель «Контекст»):
// 4 текстовых поля с текущими значениями activeProfile, статус-чип
// (pending/active/declined), «Сохранить» → POST /api/profile {dialogue_id, name,
// role, tone, taboos}, «Отказаться» → POST /api/profile/action {action: decline},
// «Провести интервью» → {action: interview} + подсказка написать «интервью» в чат,
// «Заполнить заново» → {action: reset}.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import ProfileTab from '../src/components/ProfileTab'

const DIALOGUE_ID = 'd1'

function pendingProfile() {
  return { status: 'pending', interview: false, name: '', role: '', tone: '', taboos: '' }
}

type Profile = ReturnType<typeof pendingProfile>

// Контракты GET /api/* для StudioProvider (loadAll при старте);
// профиль активного диалога — из его записи в списке диалогов
function fixtures(profile?: Profile): Record<string, unknown> {
  return {
    '/api/config': {
      model: 'qwen3.8-27b',
      temperature: 0.7,
      max_tokens: 1024,
      system_prompt: 'Ты — ассистент.',
    },
    '/api/dialogues': {
      active_id: DIALOGUE_ID,
      dialogues: [
        {
          id: DIALOGUE_ID,
          title: 'Тест',
          created: '2026-09-19T10:00:00',
          message_count: 0,
          profile: profile ?? pendingProfile(),
        },
      ],
    },
    [`/api/dialogues/${DIALOGUE_ID}`]: { dialogue: { messages: [] } },
    '/api/memory': {
      active_id: DIALOGUE_ID,
      dialogue: { message_count: 0, tokens_est: 0 },
      working: { entries: 0, tokens_est: 0, items: {} },
      long_term: { entries: 0, tokens_est: 0, items: {} },
    },
    '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
    '/api/requests': { requests: [] },
  }
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

// GET-запросы — фикстуры; POST /api/profile и /api/profile/action —
// отвечают по контракту бэкенда (непустое содержимое → active) и
// записываются в calls {url, body} для ассертов
function mockApi(fx: Record<string, unknown>, calls: { url: string; body: unknown }[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = normalizeUrl(input)
      if (init?.method !== 'POST') {
        return jsonResponse(fx[url] ?? { ok: true })
      }
      const body = JSON.parse(String(init.body))
      calls.push({ url, body })
      if (url === '/api/profile') {
        const active = Boolean(body.name || body.role || body.tone || body.taboos)
        return jsonResponse({
          profile: {
            status: active ? 'active' : 'pending',
            interview: false,
            name: body.name,
            role: body.role,
            tone: body.tone,
            taboos: body.taboos,
          },
        })
      }
      if (url === '/api/profile/action') {
        if (body.action === 'decline') {
          return jsonResponse({
            profile: { status: 'declined', interview: false, name: '', role: '', tone: '', taboos: '' },
          })
        }
        if (body.action === 'interview') {
          return jsonResponse({ profile: { status: 'pending', interview: true, name: '', role: '', tone: '', taboos: '' } })
        }
        return jsonResponse({ profile: pendingProfile() })
      }
      return jsonResponse({ ok: true })
    }),
  )
}

async function renderTab(profile?: Profile) {
  const fx = fixtures(profile)
  const calls: { url: string; body: unknown }[] = []
  mockApi(fx, calls)
  render(
    <StudioProvider>
      <ProfileTab />
    </StudioProvider>,
  )
  // loadAll: дождаться, пока профиль появится в состоянии, затем вымыть
  // пассивный эффект синхронизации полей (он идёт следом за рендером)
  await screen.findByText(/профиль/i)
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
  return calls
}

beforeEach(() => {
  localStorage.clear()
})

describe('ProfileTab — вкладка «Профили»', () => {
  it('рендерит 4 поля с текущими значениями активного профиля', async () => {
    const calls = await renderTab({
      status: 'active',
      interview: false,
      name: 'Иван',
      role: 'backend',
      tone: 'кратко',
      taboos: 'мат',
    })
    expect(screen.getByDisplayValue('Иван')).toBeInTheDocument()
    expect(screen.getByDisplayValue('backend')).toBeInTheDocument()
    expect(screen.getByDisplayValue('кратко')).toBeInTheDocument()
    expect(screen.getByDisplayValue('мат')).toBeInTheDocument()
    // статус-чип active
    expect(screen.getByText('активен')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('«Сохранить» → POST /api/profile с 4 полями + dialogue_id', async () => {
    const calls = await renderTab()
    fireEvent.change(screen.getByLabelText('Имя пользователя'), { target: { value: 'Мария' } })
    fireEvent.change(screen.getByLabelText('Роль и сфера'), { target: { value: 'дизайнер' } })
    fireEvent.click(screen.getByTitle('Сохранить'))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/profile',
        body: { dialogue_id: DIALOGUE_ID, name: 'Мария', role: 'дизайнер', tone: '', taboos: '' },
      }),
    )
    // после сохранения профиль active — чип обновился
    expect(await screen.findByText('активен')).toBeInTheDocument()
  })

  it('«Отказаться» → POST /api/profile/action {action: decline}, чип — «отказан»', async () => {
    const calls = await renderTab()
    fireEvent.click(screen.getByTitle('Отказаться'))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/profile/action',
        body: { dialogue_id: DIALOGUE_ID, action: 'decline' },
      }),
    )
    expect(await screen.findByText('отказан')).toBeInTheDocument()
  })

  it('«Провести интервью» → POST {action: interview} + подсказка в чат', async () => {
    const calls = await renderTab()
    fireEvent.click(screen.getByTitle('Провести интервью'))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/profile/action',
        body: { dialogue_id: DIALOGUE_ID, action: 'interview' },
      }),
    )
    expect(await screen.findByText(/напишите «интервью» в чате/i)).toBeInTheDocument()
  })

  it('«Заполнить заново» → POST {action: reset}, поля очищаются', async () => {
    const calls = await renderTab({
      status: 'active',
      interview: false,
      name: 'Иван',
      role: 'backend',
      tone: 'кратко',
      taboos: 'мат',
    })
    fireEvent.click(screen.getByTitle('Заполнить заново'))
    await waitFor(() =>
      expect(calls).toContainEqual({
        url: '/api/profile/action',
        body: { dialogue_id: DIALOGUE_ID, action: 'reset' },
      }),
    )
    // профиль вернулся в pending с пустыми полями
    expect(await screen.findByText('ожидает заполнения')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Иван')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('backend')).not.toBeInTheDocument()
  })

  it('нет активного диалога — короткое сообщение', async () => {
    const fx = fixtures()
    fx['/api/dialogues'] = { active_id: null, dialogues: [] }
    const calls: { url: string; body: unknown }[] = []
    mockApi(fx, calls)
    render(
      <StudioProvider>
        <ProfileTab />
      </StudioProvider>,
    )
    expect(await screen.findByText(/нет активного диалога/i)).toBeInTheDocument()
    expect(screen.queryByTitle('Сохранить')).not.toBeInTheDocument()
  })
})
