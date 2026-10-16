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
// POST /api/profile/action {dialogue_id, action: interview|decline|reset} →
// {profile, interview_text?} — interview_text: текст интервью (4 вопроса),
// приходит только для action=interview (старый бэкенд — без поля)

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
): Promise<{ profile: UserProfile; interview_text?: string }> {
  return apiPost('/profile/action', { dialogue_id, action })
}

// ── Инварианты (день 14, новая схема) ───────────────────────────────────────
// Жёсткие правила ассистента: ассистент их не меняет и не удаляет
// (неизменяемый слой), но пользователь включает/выключает каждый инвариант
// (is_active; выключенный не уходит в промпт).
// GET    /api/invariants                              → {invariants: [...]}
// POST   /api/invariants {title,description,forbidden,is_active} → {invariant}
// POST   /api/invariants/{id}/toggle                  → {invariant}
// DELETE /api/invariants/{id}                         → {ok}

export interface Invariant {
  id: string
  title: string
  description: string
  forbidden: string[]
  is_active: boolean
}

// Запись старой схемы (бэкенд не мигрирован): {id, key, value}
type RawInvariant = {
  id: string
  title?: string
  description?: string
  forbidden?: string[]
  is_active?: boolean
  key?: string
  value?: string
}

// Нормализация записи из API в новую схему: старые поля key/value
// маппятся в title/description, отсутствие forbidden/is_active — по умолчанию
function normalizeInvariant(raw: RawInvariant): Invariant {
  return {
    id: raw.id,
    title: raw.title ?? raw.key ?? '',
    description: raw.description ?? raw.value ?? '',
    forbidden: raw.forbidden ?? [],
    is_active: raw.is_active ?? true,
  }
}

// Список инвариантов (нет поля invariants — старый бэкенд → пустой список)
export async function getInvariants(): Promise<Invariant[]> {
  const r = await apiGet<{ invariants?: RawInvariant[] }>('/invariants')
  return (r.invariants ?? []).map(normalizeInvariant)
}

// Добавить инвариант: POST /api/invariants {title, description, forbidden, is_active}
// (is_active по умолчанию true) → {invariant}
export function addInvariant(
  title: string,
  description: string,
  forbidden: string[],
  is_active: boolean = true,
): Promise<Invariant> {
  return apiPost<{ invariant: RawInvariant }>('/invariants', {
    title, description, forbidden, is_active,
  }).then((r) => normalizeInvariant(r.invariant))
}

// Переключить инвариант: POST /api/invariants/{id}/toggle → {invariant}
export function toggleInvariant(id: string): Promise<Invariant> {
  return apiPost<{ invariant: RawInvariant }>(`/invariants/${encodeURIComponent(id)}/toggle`)
    .then((r) => normalizeInvariant(r.invariant))
}

// Удалить инвариант: DELETE /api/invariants/{id}
export async function deleteInvariant(id: string): Promise<void> {
  await apiDelete<unknown>(`/invariants/${encodeURIComponent(id)}`)
}

// ── MCP-серверы (день 16) ────────────────────────────────────────────────────
// Реестр внешних Model Context Protocol серверов (stdio-процесс или
// streamable-http). Сценарий дня 16: подключить + показать инструменты;
// вызов инструментов (tool-loop) — задел, в UI не реализуется.
// GET    /api/mcp/servers                              → {servers: [...]}
// POST   /api/mcp/servers {name,type,command?,url?,env?,enabled?} → {server}
// DELETE /api/mcp/servers/{id}                         → {ok}
// POST   /api/mcp/servers/{id}/connect                 → {server} (status)
// POST   /api/mcp/servers/{id}/disconnect              → {server} (status idle)
// GET    /api/mcp/tools                                → {tools: [...]}

export type McpServerType = 'stdio' | 'http'
export type McpServerStatus = 'idle' | 'connected' | 'error'

export interface McpServer {
  id: string
  name: string
  type: McpServerType
  command: string[]
  url: string
  env: Record<string, string>
  enabled: boolean
  status: McpServerStatus
  error: string | null
  tools_count: number
}

export interface McpTool {
  server: string
  name: string
  description: string
  input_schema: Record<string, unknown>
}

// Запись из API (опциональные поля — старый/чужой бэкенд; нормализуем)
type RawMcpServer = {
  id: string
  name?: string
  type?: string
  command?: string[]
  url?: string
  env?: Record<string, string>
  enabled?: boolean
  status?: string
  error?: string | null
  tools_count?: number
}

function normalizeMcpServer(raw: RawMcpServer): McpServer {
  return {
    id: raw.id,
    name: raw.name ?? '',
    type: raw.type === 'http' ? 'http' : 'stdio',
    command: raw.command ?? [],
    url: raw.url ?? '',
    env: raw.env ?? {},
    enabled: raw.enabled ?? true,
    status: raw.status === 'connected' || raw.status === 'error' ? raw.status : 'idle',
    error: raw.error ?? null,
    tools_count: raw.tools_count ?? 0,
  }
}

// Реестр серверов (нет поля servers → пустой список)
export async function getMcpServers(): Promise<McpServer[]> {
  const r = await apiGet<{ servers?: RawMcpServer[] }>('/mcp/servers')
  return (r.servers ?? []).map(normalizeMcpServer)
}

// Удалить сервер: DELETE /api/mcp/servers/{id} → {ok}
export async function deleteMcpServer(id: string): Promise<void> {
  await apiDelete<unknown>(`/mcp/servers/${encodeURIComponent(id)}`)
}

// Подключить: POST /api/mcp/servers/{id}/connect → {server}
// (status: connected | error — ошибка не бросает, видна в статусе)
export function connectMcpServer(id: string): Promise<McpServer> {
  return apiPost<{ server: RawMcpServer }>(`/mcp/servers/${encodeURIComponent(id)}/connect`)
    .then((r) => normalizeMcpServer(r.server))
}

// Отключить: POST /api/mcp/servers/{id}/disconnect → {server} (status idle)
export function disconnectMcpServer(id: string): Promise<McpServer> {
  return apiPost<{ server: RawMcpServer }>(`/mcp/servers/${encodeURIComponent(id)}/disconnect`)
    .then((r) => normalizeMcpServer(r.server))
}

// Инструменты подключённых серверов: GET /api/mcp/tools → {tools}
export async function getMcpTools(): Promise<McpTool[]> {
  const r = await apiGet<{ tools?: McpTool[] }>('/mcp/tools')
  return r.tools ?? []
}

// Вызов инструмента (день 16, tool-loop): POST /api/mcp/servers/{sid}/tools/{tool}
// тело {dialogue_id, arguments} → {ok}. Результат бэкенд сохраняет как
// system-сообщение с полем mcp_tool; non-2xx — ApiError с RU-detail из тела.
export async function callMcpTool(
  sid: string,
  tool: string,
  dialogueId: string,
  args: Record<string, unknown>,
): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>(
    `/mcp/servers/${encodeURIComponent(sid)}/tools/${encodeURIComponent(tool)}`,
    { dialogue_id: dialogueId, arguments: args },
  )
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
  | 'planning' | 'plan_review' | 'execution' | 'validation' | 'done' | 'paused' | 'failed'
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
  plan_approved: boolean
  constraints: string[]
  alternative: string
  retries: number
  error: string | null
  updated: string | null
}

export type TaskEvent =
  | { type: 'agent_spawned'; stage: TaskStage; agent: string }
  | { type: 'step_updated'; index: number; name: string; status: TaskPlanStatus; output?: string; usage?: TaskUsage; duration_s?: number }
  | { type: 'step_delta'; index: number; text: string }
  | { type: 'stage_done'; stage: TaskStage; output: string; verdict?: 'pass' | 'fail'; plan?: string[]; retry?: boolean; usage?: TaskUsage }
  | { type: 'plan_review'; stage: 'plan_review'; constraints: string[]; alternative: string }
  | { type: 'task_paused'; stage: string }
  | { type: 'task_resumed'; stage: TaskStage }
  | { type: 'invariant_violation'; patterns: string[] }
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

// Утвердить план после plan_review: POST /api/task/approve {dialogue_id} → {task}
export function apiPostTaskApprove(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/approve', { dialogue_id })
}

// Отклонить план (возврат на planning): POST /api/task/reject {dialogue_id, note?} → {task}
export function apiPostTaskReject(dialogue_id: string, note?: string): Promise<{ task: TaskState }> {
  return apiPost('/task/reject', { dialogue_id, note })
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
//   {"type":"invariant_violation","patterns"} — нарушен активный инвариант (до done, день 14)
//   {"type":"done","answer","usage","request_id"} — готово
//   {"type":"error","message"} — ошибка генерации

export type ChatEvent =
  | { type: 'delta'; text: string }
  // Нарушение активного инварианта: приходит ДО done (день 14)
  | { type: 'invariant_violation'; patterns: string[] }
  | { type: 'done'; answer: string; usage: Record<string, unknown> | null; request_id: number }
  | { type: 'error'; message: string }

// ── База знаний (день 21): индекс документов + RAG ──────────────────────────
// GET    /api/kb/stats                → KbStats (404 {detail} — индекс не построен)
// GET    /api/kb/uploads              → {uploads: [{name, size}]} (работает без индекса)
// POST   /api/kb/index {strategy, embedder} → KbBuildResult (400 — некорректные параметры)
// POST   /api/kb/upload (multipart "file")  → {ok, file, size} (400 — неподдерживаемое расширение)
// GET    /api/kb/search?q=&k=         → {results, recall_total, reranked} (404 — нет индекса)
// GET/POST /api/kb/settings           → KbSettings (POST — частичное обновление)

export type KbStrategy = 'fixed' | 'structural'
export type KbEmbedder = 'hash' | 'api' | 'ollama'
// Реранкер этапа 2 (день 21): cross-encoder поверх гибридного поиска
export type KbReranker = 'off' | 'api'

// Метрики стратегии чанкинга (бенчмарк при сборке индекса)
export interface StrategyMetric {
  chunks: number
  avg_chars: number
  max_chars: number
  hit_at_3: number
  precision_at_3: number
  mrr: number
}

export interface StrategyMetrics {
  fixed: StrategyMetric
  structural: StrategyMetric
}

// Загрузка пользователя (data/kb/uploads): {name, size}. Несёт отдельный
// GET /api/kb/uploads — работает ДО сборки индекса (stats при этом 404)
export type KbUpload = { name: string; size: number }

export interface KbStats {
  exists: true
  strategy: KbStrategy
  embedder: KbEmbedder
  // День 28: фактическая модель эмбеддера (ollama — nomic-embed-text:v1.5);
  // null у hash-эмбеддера. Старый бэкенд поля не отдаёт
  model?: string | null
  dim: number
  built_at: string
  stats: {
    docs: number
    files: number
    chunks: number
    total_chars: number
    build_ms: number
    corpus_words: number
  }
  comparison: StrategyMetrics
  files: string[]
  // Загрузки пользователя (data/kb/uploads): видны до пересборки индекса;
  // в files попадают только после «Индексировать»
  uploads?: KbUpload[]
  // Корпус, который ещё не попал в индекс (новые загрузки/доки)
  pending_files?: string[]
  // День 21 (реранкер): ключ GPUSTACK_KEY_RERANK настроен (false — только
  // гибридный поиск, даже если settings.reranker === 'api')
  reranker_key_configured: boolean
}

// Прогресс сборки индекса (GET /api/kb/build-status, polling из UI).
// phase: corpus | chunking | embedding | metrics | save | idle | done
export interface KbBuildStatus {
  running: boolean
  phase: string
  done: number
  total: number
}

export interface KbSearchResult {
  chunk_id: string
  source: string
  file: string
  section: string
  score: number
  text: string
  // День 21 (реранкер): cross-encoder score 0..1 (только при реранке)
  rerank_score?: number
  // Позиция (1-based) чанка в результатах этапа 1; есть только при реранке
  stage1_rank?: number
}

// День 21 (реранкер): извлечённый RAG-контекст ответа ассистента — какие
// чанки ушли в system-промпт (видно в инспекторе под сообщением)
export interface RagContextChunk {
  rank: number          // 1-based финальный ранг
  // День 24: идентификатор чанка (stem-strategy-NNNN, как в метаданных
  // индекса). Старые сообщения (до дня 24) — без поля; UI undefined-safe.
  chunk_id?: string
  // День 24 (F-wave): источник чанка (docs/code/upload, как в метаданных
  // индекса). Старые сообщения — без поля; UI undefined-safe.
  source?: string
  file: string
  section: string
  score: number         // rerank_score при реранке, иначе гибридный score
  stage1_rank: number | null   // null, если не реранкено
  reranked: boolean
  text: string          // отрывок ≤300 символов (срезает бэкенд)
}
export interface RagContext {
  // Запрос пользователя, по которому шёл retrieval (бэкенд отдаёт;
  // старые сообщения — без поля)
  query?: string
  recall_total: number
  reranked: boolean
  chunks: RagContextChunk[]
  // День 24 (dont-know A′): слабая база — ни один чанк не прошёл порог
  // (min_score) / релевантности; бэкенд помечает ответ, UI рисует красную
  // карточку «🚫 Не знаю» (мокап B-2). Опционально — старые сообщения.
  dont_know?: boolean
}

export interface KbSettings {
  agent_loop: boolean
  rag: boolean
  rag_top_k: number
  strategy: KbStrategy
  embedder: KbEmbedder
  // День 21 (реранкер): реранкер этапа 2 (off — только гибридный поиск)
  reranker: KbReranker
  // Этап 1: число кандидатов (recall), по которому идёт реранк (1..200)
  rag_recall: number
  // День 23: порог отсечения реранк-скоров (0..1; 0 — отсечка выключена).
  // Старый бэкенд (до дня 23) поле не отдаёт — в UI читать как ?? 0
  min_score: number
}

export interface KbBuildResult {
  stats: Record<string, unknown>
  comparison: StrategyMetrics
  strategy: KbStrategy
  // full — полная пересборка; incremental — только новые файлы
  mode: 'full' | 'incremental'
  // число добавленных файлов (full — весь корпус)
  added: number
}

// Статистика индекса. 404 «индекс не построен» — НЕ ошибка: возвращаем
// {exists:false} (узел-пусто). Другие коды — ApiError с RU-detail.
export async function apiKbStats(): Promise<KbStats | { exists: false }> {
  try {
    return await apiGet<KbStats>('/kb/stats')
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return { exists: false }
    throw err
  }
}

// Список загрузок: GET /api/kb/uploads → {uploads: [{name, size}]}.
// Независим от индекса: файл виден сразу после upload (stats — 404).
export function apiKbUploads(): Promise<KbUpload[]> {
  return apiGet<{ uploads: KbUpload[] }>('/kb/uploads').then((r) => r.uploads)
}

// Прогресс сборки (polling, пока идёт «Индексировать»)
export function apiKbBuildStatus(): Promise<KbBuildStatus> {
  return apiGet<KbBuildStatus>('/kb/build-status')
}

// Собрать (пересобрать) индекс: POST /api/kb/index {strategy, embedder}
export function apiKbIndex(strategy: KbStrategy, embedder: KbEmbedder): Promise<KbBuildResult> {
  return apiPost('/kb/index', { strategy, embedder })
}

// Загрузить файл в каталог знаний: multipart-запрос, поле "file".
// Content-Type НЕ задаём вручную — браузер поставит boundary.
export function apiKbUpload(file: File): Promise<{ ok: boolean; file: string; size: number }> {
  const fd = new FormData()
  fd.append('file', file)
  return fetch(`${BASE}/kb/upload`, { method: 'POST', body: fd }).then((r) => parseJson(r))
}

// Удалить загруженный файл + его чанки из индекса:
// DELETE /api/kb/uploads/{name} → {ok, file, chunks_removed}
export function apiKbDeleteUpload(name: string): Promise<{ ok: boolean; file: string; chunks_removed: number }> {
  return apiDelete(`/kb/uploads/${encodeURIComponent(name)}`)
}

// Полная очист базы знаний (все загрузки + индекс; settings сохраняются):
// DELETE /api/kb → {ok, uploads_removed}
export function apiKbWipe(): Promise<{ ok: boolean; uploads_removed: number }> {
  return apiDelete('/kb')
}

// Поиск по базе (день 21, двухэтапный): GET /api/kb/search?q=&k= →
// {results, recall_total, reranked} (404 — нет индекса). results — top-k
// финальных (после реранкера, если включён); recall_total — кандидатов
// этапа 1, reranked — применялся ли реранкер
export function apiKbSearch(q: string, k: number = 5): Promise<{ results: KbSearchResult[]; recall_total: number; reranked: boolean }> {
  return apiGet(`/kb/search?q=${encodeURIComponent(q)}&k=${k}`)
}

// Текущие настройки RAG/агента: GET /api/kb/settings
export function apiKbSettings(): Promise<KbSettings> {
  return apiGet('/kb/settings')
}

// ── День 22: RAG-режим per-диалог ───────────────────────────────────────────
// POST /api/dialogues/{id}/rag {rag} → 200. dialogue.rag = null → диалог
// следует глобальной настройке БЗ (settings['rag']); true/false —
// per-диалог override. non-2xx — ApiError с RU-detail (паттерн хелперов выше).
export function apiSetDialogueRag(id: string, rag: boolean): Promise<{ rag: boolean }> {
  return apiPost(`/dialogues/${encodeURIComponent(id)}/rag`, { rag })
}

// ── День 25: память задачи (task state) per-диалог ─────────────────────────
// Память задачи: что пользователь уже уточнил (clarifications), какие
// ограничения/термины зафиксированы (constraints), какова цель диалога (goal).
// Внимание: НЕ путать с TaskState (FSM задачи дня 13, выше) — отдельная
// сущность дня 25, поэтому имя другое (TaskMemoryState).
export interface TaskMemoryState {
  clarifications: string[]
  constraints: string[]
  goal: string
}

// GET /api/dialogues/{id}/task-state → {task_state}; 404 — диалог не найден.
// Старые записи диалога без task_state → пустое состояние (бэкенд нормализует).
export function apiGetTaskState(id: string): Promise<{ task_state: TaskMemoryState }> {
  return apiGet(`/dialogues/${encodeURIComponent(id)}/task-state`)
}

// POST /api/dialogues/{id}/task-state — запись ЦЕЛОГО состояния (все 3 поля
// обязательны; отсутствующее поле = 400). 400 — некорректные поля, 404 — диалог.
export function apiSetTaskState(id: string, taskState: TaskMemoryState): Promise<{ task_state: TaskMemoryState }> {
  return apiPost(`/dialogues/${encodeURIComponent(id)}/task-state`, taskState)
}

// Частичное обновление настроек: POST /api/kb/settings {…} → актуальные настройки
export function apiKbSettingsPost(patch: Partial<KbSettings>): Promise<KbSettings> {
  return apiPost('/kb/settings', patch)
}

// День 22 (сравнение RAG): ответ POST /api/rag/compare — один вопрос, два
// ответа LLM (без KB-блока / с чанками), non-stream. chunks — тот же тип,
// что у двухэтапного поиска (KbSearchResult), rag_context — RagContext.
// День 23 (аддитивно): +2 армы (фильтр min_score / rewrite) — все новые
// поля OPTIONAL: старый бэкенд (до дня 23) их не отдаёт, UI — undefined-safe.
export interface RagCompareResult {
  answer_plain: string
  answer_rag: string
  kb_block: string
  chunks: KbSearchResult[]
  rag_context: RagContext
  // ── День 23: новые армы (отсутствуют у старого бэкенда) ──
  // Арма «С RAG + фильтром»: RAG с отсечкой чанков ниже min_score
  answer_rag_filter?: string
  // Арма «С RAG + rewrite»: RAG по переписанному LLM-запросу
  answer_rag_rewrite?: string
  chunks_rag_filter?: KbSearchResult[]
  chunks_rag_rewrite?: KbSearchResult[]
  rag_context_rag_filter?: RagContext
  rag_context_rag_rewrite?: RagContext
  // Переписанный запрос (rewrite-арма); есть, если rewrite_applied === true
  rewritten_query?: string
  // true — LLM действительно переписала запрос; false — fallback на
  // исходный вопрос (арма = обычный RAG), rewritten_query может отсутствовать
  rewrite_applied?: boolean
}

// Сравнение RAG: POST /api/rag/compare {question, min_score?} → ответы
// одним запросом (non-stream, стриминга нет). min_score уйдёт в тело,
// только если > 0 (0/undefined — тело как в день 22, byte-совместимо).
// 400/404/500 — ApiError с RU-detail (паттерн соседних KB-хелперов).
export function apiRagCompare(question: string, minScore?: number): Promise<RagCompareResult> {
  const body: { question: string; min_score?: number } = { question }
  if (minScore != null && minScore > 0) body.min_score = minScore
  return apiPost('/rag/compare', body)
}

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

// ── День 29: оптимизация локальной LLM (поиск сериалов) ─────────────────────
// Профиль — именованный набор параметров инференса (temperature,
// max_tokens, num_ctx, seed) и промпт-шаблона под задачу. Активный
// профиль хранится на бэкенде (studio/data/tuning.json).

export interface TuningProfile {
  name: string
  title: string
  description: string
  model: string | null
  temperature: number
  top_p: number
  num_predict: number
  num_ctx: number | null
  seed: number | null
  system_prompt: string
  stop: string[]
  format_json: boolean
  // Отключение «размышления» reasoning-моделей (день 29): обязательно
  // для glm-4.7-flash, иначе content пуст.
  think: boolean | null
  notes: string
}

export interface TuningProfiles {
  active: string
  profiles: TuningProfile[]
}

export interface SeriesStats {
  total: number
  years: [number, number] | null
  genres: Record<string, number>
  countries: Record<string, number>
}

export interface SeriesCatalog {
  stats: SeriesStats
  sample: SeriesItem[]
  ready: boolean
}

export interface SeriesItem {
  id?: string
  imdb?: string
  title: string
  year: number | null
  genres: string[]
  country: string | null
  why?: string
  satisfies?: boolean
}

export interface SeriesConstraints {
  genres: string[]
  country: string | null
  min_year: number | null
  max_year: number | null
}

export interface SeriesMetrics {
  latency_sec: number
  prompt_tokens: number
  completion_tokens: number
  tokens_per_sec: number | null
  num_ctx: number | null
  parsed: boolean
  hallucination_rate: number
  constraint_satisfaction: number
}

export interface SeriesResult {
  query: string
  profile: string
  model: string | null
  constraints: SeriesConstraints
  recommendations: SeriesItem[]
  hallucinated: { id: string; title: string; why: string }[]
  note: string
  parsed: boolean
  metrics: SeriesMetrics
  candidates: SeriesItem[]
  request_body: Record<string, unknown>
}

export interface SeriesCompareRow {
  query: string
  profile: string
  ok: boolean
  error?: string
  latency_sec?: number
  tokens_per_sec?: number | null
  num_ctx?: number | null
  parsed?: boolean
  hallucination_rate?: number
  constraint_satisfaction?: number
}

export interface SeriesCompareResult {
  profiles: Record<
    string,
    {
      ok_cases: number
      format_validity?: number
      hallucination_rate?: number
      constraint_satisfaction?: number
      latency_p50?: number
      tokens_per_sec?: number | null
      num_ctx?: number | null
    }
  >
  rows: SeriesCompareRow[]
}

// GET /api/tuning/profiles → список профилей + активный
export function apiTuningProfiles(): Promise<TuningProfiles> {
  return apiGet('/tuning/profiles')
}

// POST /api/tuning/profile {profile} → {active}
export function apiSetTuningProfile(profile: string): Promise<{ active: string }> {
  return apiPost('/tuning/profile', { profile })
}

// GET /api/series/catalog → статистика каталога + образец записей
export function apiSeriesCatalog(): Promise<SeriesCatalog> {
  return apiGet('/series/catalog')
}

// POST /api/series/search {query, profile?, limit?} → рекомендации + метрики
export function apiSeriesSearch(
  query: string,
  profile?: string,
  limit?: number,
): Promise<SeriesResult> {
  const body: { query: string; profile?: string; limit?: number } = { query }
  if (profile) body.profile = profile
  if (limit) body.limit = limit
  return apiPost('/series/search', body)
}

// POST /api/series/compare {queries, profiles?} → сравнение профилей
export function apiSeriesCompare(
  queries: string[],
  profiles?: string[],
): Promise<SeriesCompareResult> {
  return apiPost('/series/compare', { queries, profiles })
}

// ── Локальная LLM как сетевой сервис (день 30) ──────────────────────────────
// Шлюз OpenAI-совместимого API: клиенты с ключами, rate limit (RPM/TPM),
// лимит контекста с LLM-компрессией, учёт использования.
// GET  /api/gateway/status     → адрес в LAN, модели, предупреждение
// GET  /api/gateway/clients    → клиенты (ключ маскирован)
// POST /api/gateway/clients    → создать/обновить; key — ТОЛЬКО в ответе
// DELETE /api/gateway/clients/{name}
// GET  /api/gateway/usage      → счётчики по клиентам + окно лимитов
// GET  /api/gateway/rejections → лог отклонённых запросов
// POST /api/gateway/reset      → сбросить счётчики клиента

export interface GatewayClient {
  name: string
  rpm: number
  tpm: number
  max_context: number
  max_output_tokens: number
  allow_models: string[]
  enabled: boolean
  note: string
  created_at: number
  key_masked: string
}

export interface GatewayClientCreated extends GatewayClient {
  key: string | null
}

export interface GatewayStatus {
  enabled: boolean
  host: string
  port: number
  lan_url: string
  chat_url: string
  models: string[]
  clients: number
  tls: boolean
  note: string
}

export interface GatewayUsageEntry {
  client: string
  requests: number
  tokens_in: number
  tokens_out: number
  tokens_total: number
  rejected: number
  compressed: number
  last_seen?: number
  last_latency?: number
  avg_latency?: number
  limits?: { rpm: number; tpm: number; max_context: number }
  window?: { requests: number; tokens: number }
}

export interface GatewayUsage {
  usage: Record<string, GatewayUsageEntry>
  totals: { requests: number; tokens: number; rejected: number }
}

export interface GatewayRejection {
  ts: number
  client: string
  code: string
  status: number
  message: string
}

export interface GatewayClientInput {
  name: string
  rpm?: number
  tpm?: number
  max_context?: number
  max_output_tokens?: number
  allow_models?: string[]
  enabled?: boolean
  note?: string
  rotate_key?: boolean
}

export function apiGatewayStatus(): Promise<GatewayStatus> {
  return apiGet('/gateway/status')
}

export function apiGatewayClients(): Promise<{ clients: GatewayClient[] }> {
  return apiGet('/gateway/clients')
}

export function apiGatewayClientUpsert(
  input: GatewayClientInput,
): Promise<GatewayClientCreated> {
  return apiPost('/gateway/clients', input)
}

export function apiGatewayClientDelete(
  name: string,
): Promise<{ ok: boolean; name: string }> {
  return apiDelete(`/gateway/clients/${encodeURIComponent(name)}`)
}

export function apiGatewayUsage(): Promise<GatewayUsage> {
  return apiGet('/gateway/usage')
}

export function apiGatewayRejections(
  limit = 50,
): Promise<{ rejections: GatewayRejection[] }> {
  return apiGet(`/gateway/rejections?limit=${limit}`)
}

export function apiGatewayReset(name: string): Promise<{ ok: boolean; name: string }> {
  return apiPost('/gateway/reset', { name })
}

// ── Ассистент разработчика и поддержки (дни 30–34) ──────────────────────────
// Единый инструмент: RAG-вопросы по документации и коду проекта, ревью
// изменений (PR review), обработка тикетов с учётом CRM и автономные
// файловые сценарии.

export interface AssistantSource {
  file: string
  section?: string
  chunk_id?: string
  score?: number
  source?: string
  quote: string
}

export interface AssistantAsk {
  question: string
  answer: string
  sources: AssistantSource[]
  found: boolean
  llm: boolean
  llm_error?: string | null
  recall_total?: number
}

export interface AssistantFinding {
  rule: string
  severity: 'high' | 'medium' | 'low'
  message: string
  file: string | null
  line: number | null
  snippet?: string
}

export interface AssistantFileChange {
  path: string
  added: number
  removed: number
  is_new: boolean
  is_deleted: boolean
}

export interface AssistantRecommendation {
  rule: string
  advice: string
}

export interface AssistantReview {
  source: string
  summary: {
    files: number
    added: number
    removed: number
    findings: number
    by_severity: { high: number; medium: number; low: number }
  }
  files: AssistantFileChange[]
  findings: AssistantFinding[]
  affected_docs: { file: string; section?: string; because?: string }[]
  recommendations: AssistantRecommendation[]
  llm_note: string
}

export interface AssistantTicket {
  id: string
  user_id: string
  subject: string
  body: string
  topic: string
  status: string
  priority: string
  tags?: string[]
  created_at?: string
  resolution?: string | null
}

export interface AssistantTickets {
  tickets: AssistantTicket[]
  stats: {
    users: number
    tickets: number
    by_status: Record<string, number>
    available: boolean
  }
}

export interface AssistantSupport {
  ticket_id: string
  found: boolean
  answer: string
  llm: boolean
  llm_error?: string | null
  sources: AssistantSource[]
  context: {
    found: boolean
    user: {
      id?: string
      name?: string
      plan?: string
      role?: string
      payment_status?: string
      channel?: string
      registered_at?: string
    } | null
    ticket: {
      id?: string
      topic?: string
      status?: string
      priority?: string
    } | null
    history_count: number
    applied: string[]
  }
}

export interface AssistantScenario {
  name: string
  title: string
  params: string[]
  description: string
}

export interface AssistantScenarioResult {
  scenario: string
  duration_ms: number
  ok: boolean
  summary: string
  changed_files: string[]
  diff: string
  dry_run?: boolean
  report?: string
  result?: unknown
}

export interface AssistantStatus {
  indexed: boolean
  documents: number
  chunks: number
  strategy?: string | null
  embedder?: string | null
  built_at?: string | null
  llm_available: boolean
  crm: AssistantTickets['stats']
  git: { available: boolean; branch?: string | null; changed: number }
  corpus: { files: number; chars: number; by_source: Record<string, number> }
}

export interface AssistantGit {
  available: boolean
  branch?: string | null
  tracked: string[]
  untracked: string[]
  diff: string
  diff_lines: number
  repo_root?: string
}

export function apiAssistantStatus(): Promise<AssistantStatus> {
  return apiGet('/assistant/status')
}

export function apiAssistantIndex(strategy = 'structural'): Promise<unknown> {
  return apiPost('/assistant/index', { strategy })
}

export function apiAssistantAsk(question: string, top_k = 5): Promise<AssistantAsk> {
  return apiPost('/assistant/ask', { question, top_k })
}

export function apiAssistantReview(input: {
  diff?: string
  base?: string
  with_llm?: boolean
}): Promise<AssistantReview> {
  return apiPost('/assistant/review', input)
}

export function apiAssistantTickets(): Promise<AssistantTickets> {
  return apiGet('/assistant/tickets')
}

export function apiAssistantSupport(
  ticket_id: string,
  question?: string,
): Promise<AssistantSupport> {
  return apiPost('/assistant/support', { ticket_id, question })
}

export function apiAssistantScenarios(): Promise<{ scenarios: AssistantScenario[] }> {
  return apiGet('/assistant/scenarios')
}

export function apiAssistantScenario(
  name: string,
  params: Record<string, unknown> = {},
): Promise<AssistantScenarioResult> {
  return apiPost('/assistant/scenario', { name, params })
}

export function apiAssistantGit(): Promise<AssistantGit> {
  return apiGet('/assistant/git')
}
