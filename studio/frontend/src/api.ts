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

// ── Профиль пользователя (день 12) ──────────────────────────────────────────
// Профиль привязан к диалогу: 4 текстовых поля + статус + флаг интервью.
// POST /api/profile      {dialogue_id, name, role, tone, taboos} → {profile}
// POST /api/profile/action {dialogue_id, action: interview|decline|reset} → {profile}

export interface UserProfile {
  status: 'pending' | 'active' | 'declined'
  interview: boolean
  name: string
  role: string
  tone: string
  taboos: string
}

// Сохранить 4 поля профиля (непустое содержимое → active, все пустые → pending)
export function apiPostProfile(
  dialogue_id: string,
  name: string,
  role: string,
  tone: string,
  taboos: string,
): Promise<{ profile: UserProfile }> {
  return apiPost('/profile', { dialogue_id, name, role, tone, taboos })
}

// Действие с профилем: interview (флаг, статус pending), decline, reset
export function apiPostProfileAction(
  dialogue_id: string,
  action: 'interview' | 'decline' | 'reset',
): Promise<{ profile: UserProfile }> {
  return apiPost('/profile/action', { dialogue_id, action })
}

// ── Состояние задачи (день 13b): unified FSM per-диалог ────────────────────
// POST /api/task/start {dialogue_id, description} → {task} (+ user-маркер)
// POST /api/task/run {dialogue_id} → SSE: agent_spawned/step_updated/
//   step_delta/stage_done/task_paused/task_resumed/task_done/task_failed/
//   error
// POST /api/task/pause|resume|reset {dialogue_id} → {task}
// POST /api/task/instruction {dialogue_id, text} → {task} (только на паузе)
// GET  /api/task?dialogue_id= → {task}

export type TaskStage =
  | 'planning' | 'execution' | 'validation' | 'done' | 'paused' | 'failed'
export type TaskPlanStatus = 'pending' | 'in_progress' | 'completed'
export type TaskExpectedAction = 'agent_response' | 'resume_wait' | 'human_input'

// Токены LLM-вызова (стадия / work-шаг): prompt/completion/total
export type TaskUsage = { prompt: number; completion: number; total: number }

export interface TaskPlanEntry {
  step: number
  agent: 'planning' | 'execution' | 'validation' | 'done'
  status: TaskPlanStatus
  output: string | null
  verdict: 'pass' | 'fail' | null
  spawn_ts: string | null
  ts: string | null
  // Токены и длительность стадии (заполняются при stage_done; старые записи — без полей)
  usage?: TaskUsage | null
  duration_s?: number | null
}

export interface TaskWorkStep {
  name: string
  status: TaskPlanStatus
  output: string | null
  ts: string | null
  // start_ts — старт шага (in_progress); usage/duration_s — при completed
  start_ts?: string | null
  usage?: TaskUsage | null
  duration_s?: number | null
}

export interface TaskState {
  active: boolean
  task_id: string | null
  stage: TaskStage | null
  current_step: number
  total_steps: number
  expected_action: TaskExpectedAction | null
  plan: TaskPlanEntry[]
  work_steps: TaskWorkStep[]
  context_snapshot: { description: string; work_steps: TaskWorkStep[]; instruction: string } | null
  description: string
  instruction: string
  retries: number
  error: string | null
  updated: string | null
}

export type TaskEvent =
  | { type: 'agent_spawned'; stage: TaskStage; agent: string }
  | { type: 'step_updated'; index: number; name: string; status: TaskPlanStatus; output?: string; usage?: TaskUsage; duration_s?: number }
  | { type: 'step_delta'; index: number; text: string }
  | { type: 'stage_done'; stage: TaskStage; output: string; verdict?: 'pass' | 'fail'; plan?: string[]; retry?: boolean; usage?: TaskUsage }
  | { type: 'task_paused'; stage: string }
  | { type: 'task_resumed'; stage: TaskStage }
  | { type: 'task_done'; answer: string }
  | { type: 'task_failed'; message: string }
  | { type: 'error'; message: string }

export function apiPostTaskStart(
  dialogue_id: string,
  description: string,
): Promise<{ task: TaskState }> {
  return apiPost('/task/start', { dialogue_id, description })
}

export function apiPostTaskPause(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/pause', { dialogue_id })
}

export function apiPostTaskResume(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/resume', { dialogue_id })
}

export function apiPostTaskInstruction(
  dialogue_id: string,
  text: string,
): Promise<{ task: TaskState }> {
  return apiPost('/task/instruction', { dialogue_id, text })
}

export function apiPostTaskReset(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/reset', { dialogue_id })
}

export function apiGetTask(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiGet(`/task?dialogue_id=${encodeURIComponent(dialogue_id)}`)
}

// SSE POST /api/task/run — тот же паттерн, что chatStream (fetch + ReadableStream)
export async function taskStream(
  dialogueId: string,
  onEvent: (e: TaskEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/task/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ dialogue_id: dialogueId }),
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
          onEvent(JSON.parse(raw) as TaskEvent)
        } catch {
          // битый кадр — пропускаем
        }
      }
    }
  }
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
  dialogueId: string,
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
