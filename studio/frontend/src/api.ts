// Фетч-хелперы для API бэкенда «Студии» (база /api, проксируется Vite на :8000).
// Ошибки — throw с detail из JSON ответа ({detail: RU-текст}).

const BASE = '/api'

// Ошибка API: статус + человекочитаемый detail из тела ответа
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function fail(res: Response): Promise<never> {
  let detail = `HTTP ${res.status}`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (body && typeof body.detail === 'string' && body.detail) detail = body.detail
  } catch {
    // тело не JSON — оставляем «HTTP <status>»
  }
  throw new ApiError(res.status, detail)
}

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) await fail(res)
  return (await res.json()) as T
}

export function apiGet<T>(path: string): Promise<T> {
  return fetch(`${BASE}${path}`).then((r) => parseJson<T>(r))
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => parseJson<T>(r))
}

export function apiDelete<T>(path: string): Promise<T> {
  return fetch(`${BASE}${path}`, { method: 'DELETE' }).then((r) => parseJson<T>(r))
}

// ── SSE-контракт дня 11 ─────────────────────────────────────────────────────
// POST /api/chat {dialogue_id, message} → поток кадров `data: {json}\n\n`:
//   {"type":"delta","text"} — кусок ответа
//   {"type":"done","answer","usage","request_id"} — готово
//   {"type":"error","message"} — ошибка генерации

export type ChatEvent =
  | { type: 'delta'; text: string }
  | { type: 'done'; answer: string; usage: Record<string, unknown> | null; request_id: number }
  | { type: 'error'; message: string }

// EventSource не умеет POST, поэтому — fetch + ReadableStream.
// Буфер разбиваем по '\n\n' (кадр SSE), внутри ищем строки `data: {json}`.
export async function chatStream(
  dialogueId: number,
  message: string,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ dialogue_id: dialogueId, message }),
  })
  if (!res.ok) await fail(res)
  if (!res.body) throw new ApiError(0, 'Пустой SSE-поток (нет тела ответа)')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      for (const line of frame.split('\n')) {
        const l = line.trim()
        if (!l.startsWith('data:')) continue
        const raw = l.slice(5).trim()
        if (!raw) continue
        try {
          onEvent(JSON.parse(raw) as ChatEvent)
        } catch {
          // битый кадр — пропускаем
        }
      }
    }
  }
}
