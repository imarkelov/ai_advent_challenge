// Состояние приложения «Студия»: React Context + чистый reducer.
// Чистые функции (initialState/appendDelta/finishAssistant/reducer) — без React,
// покрыты тестами (tests/state.test.ts).
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  useRef,
  type ReactNode,
} from 'react'
import {
  apiDelete,
  apiGet,
  apiGetTask,
  apiPost,
  apiPostTaskInstruction,
  apiPostTaskPause,
  apiPostTaskReset,
  apiPostTaskResume,
  apiPostTaskStart,
  chatStream,
  taskStream,
  type ChatEvent,
  type TaskEvent,
  type TaskStage,
  type TaskState,
  type UserProfile,
} from './api'

// ── Типы по контракту API дня 11 ────────────────────────────────────────────
export type Role = 'system' | 'user' | 'assistant'

export interface Message {
  role: Role
  content: string
  // Модель, которой выполнен запрос (assistant-сообщения из бэкенда; старые — без поля)
  model?: string
  // Стадия задачи, выполненная stage-агентом (день 13; обычные сообщения — без поля)
  task_stage?: string
}

export interface DialogueMeta {
  id: string
  title: string
  created: string
  message_count: number
  // Профиль пользователя (день 12): опционально — старые dialogues.json без поля
  profile?: UserProfile
  // Состояние задачи (день 13): опционально — старые dialogues.json без поля
  task?: TaskState
}

export interface MemoryLayer {
  entries: number
  tokens_est: number
  items: Record<string, string>
}

// Включения слоёв памяти в промпт (st/wm/lt, default true)
export interface MemoryToggles {
  st: boolean
  wm: boolean
  lt: boolean
}

export interface MemoryState {
  active_id: string | null
  dialogue: { message_count: number; tokens_est: number }
  working: MemoryLayer
  long_term: MemoryLayer
  toggles: MemoryToggles
}

export interface TokenLast {
  prompt: number
  completion: number
  reasoning: number
  total: number
}

export interface TokenState {
  last: TokenLast | null
  session: { prompt: number; completion: number; total: number }
  context_limit: number
}

export interface RequestSummary {
  id: number
  ts: string
  model: string
  total_tokens: number
  error: string | null
}

export interface RequestDetail {
  id: number
  ts: string
  model: string
  request: Record<string, unknown>
  usage: Record<string, unknown> | null
  error: string | null
}

export interface Config {
  model: string
  temperature: number
  max_tokens: number
  system_prompt: string
}

export interface ModelInfo {
  id: string
  context_limit: number
}

// Активная вкладка правой панели «Контекст» (день 12: бейдж в шапке чата
// открывает вкладку «Профили» извне панели)
export type ContextTab = 'memory' | 'tokens' | 'request' | 'profile' | 'task'

export interface StudioState {
  loaded: boolean
  config: Config | null
  dialogues: DialogueMeta[]
  // Профили диалогов (день 12): id диалога → профиль (из GET /api/dialogues)
  profiles: Record<string, UserProfile>
  // Задачи диалогов (день 13): id диалога → состояние задачи (из GET /api/dialogues)
  tasks: Record<string, TaskState>
  // Пайплайн задачи выполняется прямо сейчас (SSE-стрим открыт)
  taskRunning: boolean
  // Стадия, которую исполняет stage-агент прямо сейчас (из события «stage»)
  taskCurrentStage: TaskStage | null
  activeId: string | null
  messages: Message[]
  memory: MemoryState | null
  tokens: TokenState | null
  requests: RequestSummary[]
  models: ModelInfo[]
  showRequests: boolean
  streaming: boolean
  lastRequest: RequestDetail | null
  // Вкладка правой панели «Контекст» (день 12)
  contextTab: ContextTab
}

// Ключ localStorage для тумблера «Показывать запросы»
export const SHOW_REQUESTS_KEY = 'studio.showRequests'

// Начальное состояние (showRequests читается из localStorage, default true)
export function initialState(): StudioState {
  let show = true
  try {
    const v = localStorage.getItem(SHOW_REQUESTS_KEY)
    if (v !== null) show = v === '1'
  } catch {
    // нет localStorage (небраузерная среда) — держим default
  }
  return {
    loaded: false,
    config: null,
    dialogues: [],
    profiles: {},
    tasks: {},
    taskRunning: false,
    taskCurrentStage: null,
    activeId: null,
    messages: [],
    memory: null,
    tokens: null,
    requests: [],
    models: [],
    showRequests: show,
    streaming: false,
    lastRequest: null,
    contextTab: 'memory',
  }
}

// Дельта стрима: дописать в текущее assistant-сообщение (или создать)
export function appendDelta(messages: Message[], text: string): Message[] {
  const last = messages[messages.length - 1]
  if (last && last.role === 'assistant') {
    return [...messages.slice(0, -1), { ...last, content: last.content + text }]
  }
  return [...messages, { role: 'assistant', content: text }]
}

// Событие done: заменить накопленный хвост авторитетным полным ответом
export function finishAssistant(messages: Message[], answer: string): Message[] {
  const last = messages[messages.length - 1]
  if (last && last.role === 'assistant') {
    return [...messages.slice(0, -1), { ...last, content: answer }]
  }
  return answer ? [...messages, { role: 'assistant', content: answer }] : messages
}

// Извлечь профили из списка диалогов (записи без profile пропускаются —
// бэкворд-совместимость со старыми dialogues.json)
export function profilesFrom(dialogues: DialogueMeta[]): Record<string, UserProfile> {
  const out: Record<string, UserProfile> = {}
  for (const d of dialogues) {
    if (d.profile) out[d.id] = d.profile
  }
  return out
}

// Профиль активного диалога (derived: из profiles + activeId)
export function activeProfileOf(
  state: Pick<StudioState, 'activeId' | 'profiles'>,
): UserProfile | null {
  if (state.activeId == null) return null
  return state.profiles[state.activeId] ?? null
}

// Извлечь задачи из списка диалогов (записи без task пропускаются —
// бэкворд-совместимость со старыми dialogues.json)
export function tasksFrom(dialogues: DialogueMeta[]): Record<string, TaskState> {
  const out: Record<string, TaskState> = {}
  for (const d of dialogues) {
    if (d.task) out[d.id] = d.task
  }
  return out
}

// Задача активного диалога (derived: из tasks + activeId)
export function activeTaskOf(
  state: Pick<StudioState, 'activeId' | 'tasks'>,
): TaskState | null {
  if (state.activeId == null) return null
  return state.tasks[state.activeId] ?? null
}

export type StudioAction =
  | {
      type: 'loaded'
      config: Config
      dialogues: DialogueMeta[]
      activeId: string | null
      memory: MemoryState
      tokens: TokenState
      requests: RequestSummary[]
    }
  | { type: 'messages'; messages: Message[] }
  | { type: 'created'; dialogue: DialogueMeta; activeId: string }
  | { type: 'activated'; activeId: string; dialogues: DialogueMeta[]; messages: Message[] }
  | { type: 'user-message'; message: Message }
  | { type: 'delta'; text: string }
  | { type: 'done'; answer: string }
  | { type: 'error-message'; text: string }
  | {
      type: 'refresh'
      memory: MemoryState | null
      tokens: TokenState | null
      requests: RequestSummary[]
      lastRequest?: RequestDetail
    }
  | { type: 'memory'; memory: MemoryState }
  | { type: 'renamed'; id: string; title: string }
  | { type: 'dialogues-refresh'; dialogues: DialogueMeta[] }
  | { type: 'dialogues-updated'; dialogues: DialogueMeta[]; activeId: string | null; messages: Message[] }
  | { type: 'profile-set'; id: string; profile: UserProfile }
  | { type: 'task-set'; id: string; task: TaskState }
  | { type: 'task-running'; on: boolean }
  | { type: 'task-current-stage'; stage: TaskStage | null }
  | { type: 'models'; models: ModelInfo[] }
  | { type: 'config'; config: Config }
  | { type: 'last-request'; detail: RequestDetail | null }
  | { type: 'show-requests'; on: boolean }
  | { type: 'context-tab'; tab: ContextTab }

// Чистый reducer: все переходы состояния без побочных эффектов
export function reducer(state: StudioState, action: StudioAction): StudioState {
  switch (action.type) {
    case 'loaded':
      return {
        ...state,
        loaded: true,
        config: action.config,
        dialogues: action.dialogues,
        profiles: profilesFrom(action.dialogues),
        tasks: tasksFrom(action.dialogues),
        activeId: action.activeId,
        memory: action.memory,
        tokens: action.tokens,
        requests: action.requests,
      }
    case 'messages':
      return { ...state, messages: action.messages }
    case 'created':
      return {
        ...state,
        dialogues: [action.dialogue, ...state.dialogues],
        profiles: action.dialogue.profile
          ? { ...state.profiles, [action.dialogue.id]: action.dialogue.profile }
          : state.profiles,
        tasks: action.dialogue.task
          ? { ...state.tasks, [action.dialogue.id]: action.dialogue.task }
          : state.tasks,
        activeId: action.activeId,
        messages: [],
      }
    case 'activated':
      return {
        ...state,
        activeId: action.activeId,
        dialogues: action.dialogues,
        profiles: profilesFrom(action.dialogues),
        tasks: tasksFrom(action.dialogues),
        messages: action.messages,
      }
    case 'user-message':
      return { ...state, messages: [...state.messages, action.message], streaming: true }
    case 'delta':
      return { ...state, messages: appendDelta(state.messages, action.text) }
    case 'done':
      return { ...state, messages: finishAssistant(state.messages, action.answer), streaming: false }
    case 'error-message':
      return {
        ...state,
        messages: [...state.messages, { role: 'assistant', content: action.text }],
        streaming: false,
      }
    case 'refresh':
      return {
        ...state,
        memory: action.memory,
        tokens: action.tokens,
        requests: action.requests,
        lastRequest: action.lastRequest ?? state.lastRequest,
      }
    case 'memory':
      return { ...state, memory: action.memory }
    case 'renamed':
      return {
        ...state,
        dialogues: state.dialogues.map((d) =>
          d.id === action.id ? { ...d, title: action.title } : d,
        ),
      }
    case 'dialogues-refresh':
      // Только state.dialogues + profiles: бэкенд присвоил диалогу авто-название
      // — перечитали список, остальные поля не трогаем
      return {
        ...state,
        dialogues: action.dialogues,
        profiles: profilesFrom(action.dialogues),
        tasks: tasksFrom(action.dialogues),
      }
    case 'dialogues-updated':
      return {
        ...state,
        dialogues: action.dialogues,
        profiles: profilesFrom(action.dialogues),
        tasks: tasksFrom(action.dialogues),
        activeId: action.activeId,
        messages: action.messages,
      }
    case 'profile-set':
      // Обновить профиль диалога (после save/decline/reset, день 12)
      return { ...state, profiles: { ...state.profiles, [action.id]: action.profile } }
    case 'task-set':
      return { ...state, tasks: { ...state.tasks, [action.id]: action.task } }
    case 'task-running':
      return { ...state, taskRunning: action.on, taskCurrentStage: action.on ? state.taskCurrentStage : null }
    case 'task-current-stage':
      return { ...state, taskCurrentStage: action.stage }
    case 'models':
      return { ...state, models: action.models }
    case 'config':
      return { ...state, config: action.config }
    case 'last-request':
      return { ...state, lastRequest: action.detail }
    case 'show-requests':
      return { ...state, showRequests: action.on }
    case 'context-tab':
      return { ...state, contextTab: action.tab }
  }
}

// ── React-слой: провайдер + хук ─────────────────────────────────────────────

export interface StudioApi {
  state: StudioState
  // Профиль активного диалога (день 12, derived из profiles + activeId)
  activeProfile: UserProfile | null
  // Задача (день 13): FSM-действия активного диалога
  activeTask: TaskState | null
  startTask: (description: string) => Promise<void>
  runTask: () => Promise<void>
  pauseTask: () => Promise<void>
  resumeTask: () => Promise<void>
  sendTaskInstruction: (text: string) => Promise<void>
  resetTask: () => Promise<void>
  newDialogue: () => Promise<void>
  setProfile: (id: string, profile: UserProfile) => void
  activateDialogue: (id: string) => Promise<void>
  sendMessage: (text: string) => Promise<void>
  setModel: (id: string) => Promise<void>
  updateConfig: (partial: Partial<Config>) => Promise<void>
  setShowRequests: (on: boolean) => void
  // Вкладка правой панели «Контекст» (день 12): читаем текущую, устанавливаем
  // извне (бейдж в шапке чата открывает «Профили»)
  contextTab: ContextTab
  setContextTab: (tab: ContextTab) => void
  refreshMemory: () => Promise<void>
  setMemoryToggle: (layer: 'st' | 'wm' | 'lt', on: boolean) => Promise<void>
  reloadDialogue: () => Promise<void>
  deleteDialogues: (ids: string[]) => Promise<void>
  renameDialogue: (id: string, title: string) => Promise<void>
}

const StudioCtx = createContext<StudioApi | null>(null)

// Хук доступа к состоянию «Студии» (только внутри <StudioProvider>)
export function useStudio(): StudioApi {
  const ctx = useContext(StudioCtx)
  if (!ctx) throw new Error('useStudio должен вызываться внутри <StudioProvider>')
  return ctx
}

interface DialoguesResponse {
  active_id: string | null
  dialogues: DialogueMeta[]
}

interface DialogueDetailResponse {
  dialogue: { messages: Message[] }
}

export function StudioProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState)
  // Актуальное состояние для асинхронных замыканий (sendMessage)
  const stateRef = useRef(state)
  stateRef.current = state

  // Профиль активного диалога (день 12) — вычисляется из state каждый рендер
  const activeProfile = activeProfileOf(state)

  // Обновить профиль диалога после save/decline/reset (день 12)
  const setProfile = useCallback((id: string, profile: UserProfile) => {
    dispatch({ type: 'profile-set', id, profile })
  }, [])

  // Задача активного диалога (день 13) — вычисляется из state каждый рендер
  const activeTask = activeTaskOf(state)

  // Перечитать задачу активного диалога из API (источник правды — бэкенд)
  const reloadTask = useCallback(async (): Promise<TaskState | null> => {
    const id = stateRef.current.activeId
    if (id == null) return null
    try {
      const { task } = await apiGetTask(id)
      dispatch({ type: 'task-set', id, task })
      return task
    } catch (err) {
      console.error('reloadTask:', err)
      return null
    }
  }, [])

  // Запустить задачу: POST /api/task/start → обновить state
  const startTask = useCallback(async (description: string) => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskStart(id, description)
    dispatch({ type: 'task-set', id, task })
  }, [])

  // Пайплайн задачи: SSE-стрим POST /api/task/run.
  // stage_done — stage-сообщение в чат + перечитать задачу;
  // task_done — финальный ответ (как done чата); task_paused/error — стоп.
  const runTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null || stateRef.current.taskRunning) return
    dispatch({ type: 'task-running', on: true })
    try {
      await taskStream(id, (e: TaskEvent) => {
        if (e.type === 'stage') {
          dispatch({ type: 'task-current-stage', stage: e.stage })
        } else if (e.type === 'stage_done') {
          dispatch({ type: 'task-current-stage', stage: null })
          // stage-output — в историю как assistant-сообщение с меткой стадии
          dispatch({
            type: 'messages',
            messages: [
              ...stateRef.current.messages,
              { role: 'assistant', content: e.output, task_stage: e.stage },
            ],
          })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'task_done') {
          // Финальный ответ — НОВОЕ assistant-сообщение. Не action «done»:
          // finishAssistant перезаписал бы последнее сообщение (вывод
          // стадии validation), а stage-история должна сохраниться.
          dispatch({
            type: 'messages',
            messages: [
              ...stateRef.current.messages,
              { role: 'assistant', content: e.answer },
            ],
          })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'task_paused') {
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else {
          dispatch({ type: 'error-message', text: `Ошибка: ${e.message}` })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        }
      })
    } catch (err) {
      dispatch({
        type: 'error-message',
        text: `Ошибка: ${err instanceof Error ? err.message : String(err)}`,
      })
    } finally {
      dispatch({ type: 'task-running', on: false })
    }
  }, [reloadTask])

  // Стоп: POST /api/task/pause (вступает на границе стадии)
  const pauseTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    try {
      const { task } = await apiPostTaskPause(id)
      dispatch({ type: 'task-set', id, task })
    } catch (err) {
      console.error('pauseTask:', err)
      await reloadTask()
    }
  }, [reloadTask])

  // Продолжить: POST /api/task/resume + повторный запуск пайплайна
  const resumeTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    await apiPostTaskResume(id)
    await reloadTask()
    await runTask()
  }, [reloadTask, runTask])

  // Инструкция на паузе: POST /api/task/instruction
  const sendTaskInstruction = useCallback(async (text: string) => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskInstruction(id, text)
    dispatch({ type: 'task-set', id, task })
  }, [])

  // Сброс: POST /api/task/reset
  const resetTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskReset(id)
    dispatch({ type: 'task-set', id, task })
  }, [])

  // Обновление боковых панелей после действий: memory/tokens/requests (+ lastRequest)
  const refreshPanels = useCallback(async (lastId?: number) => {
    const [memory, tokens, rq] = await Promise.all([
      apiGet<MemoryState>('/memory'),
      apiGet<TokenState>('/tokens'),
      apiGet<{ requests: RequestSummary[] }>('/requests'),
    ])
    const id = lastId ?? rq.requests[0]?.id
    let lastRequest: RequestDetail | undefined
    if (id != null) lastRequest = await apiGet<RequestDetail>(`/requests/${id}`)
    dispatch({ type: 'refresh', memory, tokens, requests: rq.requests, lastRequest })
  }, [])

  // Старт: config, dialogues, memory, tokens, requests + активный диалог + последний запрос
  const loadAll = useCallback(async () => {
    const [config, d, memory, tokens, rq] = await Promise.all([
      apiGet<Config>('/config'),
      apiGet<DialoguesResponse>('/dialogues'),
      apiGet<MemoryState>('/memory'),
      apiGet<TokenState>('/tokens'),
      apiGet<{ requests: RequestSummary[] }>('/requests'),
    ])
    dispatch({
      type: 'loaded',
      config,
      dialogues: d.dialogues,
      activeId: d.active_id,
      memory,
      tokens,
      requests: rq.requests,
    })
    if (d.active_id != null) {
      const det = await apiGet<DialogueDetailResponse>(`/dialogues/${d.active_id}`)
      dispatch({ type: 'messages', messages: det.dialogue.messages })
    }
    const last = rq.requests[0]
    if (last) {
      dispatch({ type: 'last-request', detail: await apiGet<RequestDetail>(`/requests/${last.id}`) })
    }
    // Модели — отдельным запросом: недоступность API (502) не ломает загрузку,
    // в дропдауне останется только текущая модель из конфига.
    apiGet<{ models: ModelInfo[] }>('/models')
      .then((r) => dispatch({ type: 'models', models: r.models }))
      .catch((err) => console.error('models:', err))
  }, [])

  useEffect(() => {
    // Бэкенд недоступен — приложение останется в пустом состоянии (поллинга нет)
    void loadAll().catch((err) => console.error('loadAll:', err))
  }, [loadAll])

  // Новый диалог: POST /api/dialogues → 201 {dialogue, active_id}
  const newDialogue = useCallback(async () => {
    const r = await apiPost<{ dialogue: DialogueMeta; active_id: string }>('/dialogues')
    dispatch({ type: 'created', dialogue: r.dialogue, activeId: r.active_id })
    await refreshPanels()
  }, [refreshPanels])

  // Активация диалога: POST .../activate + загрузка его сообщений
  const activateDialogue = useCallback(async (id: string) => {
    if (id === stateRef.current.activeId) return
    await apiPost<{ active_id: string }>(`/dialogues/${id}/activate`)
    const [d, det, memory] = await Promise.all([
      apiGet<DialoguesResponse>('/dialogues'),
      apiGet<DialogueDetailResponse>(`/dialogues/${id}`),
      // Панели памяти: WM читаем в том же батче — иначе после переключения
      // диалога «текущая задача» останется от прежнего
      apiGet<MemoryState>('/memory'),
    ])
    dispatch({
      type: 'activated',
      activeId: d.active_id ?? id,
      dialogues: d.dialogues,
      messages: det.dialogue.messages,
    })
    dispatch({ type: 'memory', memory })
  }, [])

  // Отправка сообщения: дельты стримом в чат, done → обновление всех панелей,
  // error → сообщение-ошибка в чат
  const sendMessage = useCallback(
    async (text: string) => {
      const id = stateRef.current.activeId
      const trimmed = text.trim()
      if (id == null || !trimmed || stateRef.current.streaming) return
      dispatch({ type: 'user-message', message: { role: 'user', content: trimmed } })
      try {
        await chatStream(id, trimmed, (e: ChatEvent) => {
          if (e.type === 'delta') {
            dispatch({ type: 'delta', text: e.text })
          } else if (e.type === 'done') {
            dispatch({ type: 'done', answer: e.answer })
            void refreshPanels(e.request_id).catch((err) => console.error('refreshPanels:', err))
            // Бэкенд сам назвал новый диалог по первому сообщению — перечитываем
            // список, чтобы сайдбар показал авто-название без перезагрузки.
            // Ошибка перечитывания не ломает чат.
            void apiGet<DialoguesResponse>('/dialogues')
              .then((d) => dispatch({ type: 'dialogues-refresh', dialogues: d.dialogues }))
              .catch((err) => console.error('dialogues-refresh:', err))
          } else {
            dispatch({ type: 'error-message', text: `Ошибка: ${e.message}` })
          }
        })
      } catch (err) {
        dispatch({
          type: 'error-message',
          text: `Ошибка: ${err instanceof Error ? err.message : String(err)}`,
        })
      }
    },
    [refreshPanels],
  )

  // Смена модели: POST /api/config {model} → сервер отвечает актуальным конфигом
  const setModel = useCallback(async (id: string) => {
    const cfg = await apiPost<Config>('/config', { model: id })
    dispatch({ type: 'config', config: cfg })
  }, [])

  // Частичное обновление конфига: POST /api/config {…} → сервер отвечает актуальным конфигом
  const updateConfig = useCallback(async (partial: Partial<Config>) => {
    const cfg = await apiPost<Config>('/config', partial)
    dispatch({ type: 'config', config: cfg })
  }, [])

  // Тумблер «Показывать запросы» (персистится в localStorage)
  const setShowRequests = useCallback((on: boolean) => {
    try {
      localStorage.setItem(SHOW_REQUESTS_KEY, on ? '1' : '0')
    } catch {
      // нет localStorage — переключатель просто не сохранится
    }
    dispatch({ type: 'show-requests', on })
  }, [])

  // Переключить вкладку правой панели «Контекст» (день 12): клик по вкладке
  // внутри панели и клик по бейджу профиля в шапке чата — один и тот же путь
  const setContextTab = useCallback((tab: ContextTab) => {
    dispatch({ type: 'context-tab', tab })
  }, [])

  // Перечитать память (после изменений в MemoryTab)
  const refreshMemory = useCallback(async () => {
    dispatch({ type: 'memory', memory: await apiGet<MemoryState>('/memory') })
  }, [])

  // Включить/выключить слой памяти в промпте: POST /api/memory/toggles {layer, enabled}
  const setMemoryToggle = useCallback(
    async (layer: 'st' | 'wm' | 'lt', on: boolean) => {
      await apiPost('/memory/toggles', { layer, enabled: on })
      await refreshMemory()
    },
    [refreshMemory],
  )

  // Удаление диалогов: для каждого id — DELETE /api/dialogues/{id}
  // (404 — логируем и продолжаем остальные), затем перечитываем список,
  // сообщения активного и память
  const deleteDialogues = useCallback(async (ids: string[]) => {
    for (const id of ids) {
      try {
        await apiDelete<{ ok: boolean }>(`/dialogues/${id}`)
      } catch (err) {
        console.error('deleteDialogue:', err)
      }
    }
    const [d, memory] = await Promise.all([
      apiGet<DialoguesResponse>('/dialogues'),
      apiGet<MemoryState>('/memory'),
    ])
    let messages: Message[] = []
    if (d.active_id != null) {
      messages = (await apiGet<DialogueDetailResponse>(`/dialogues/${d.active_id}`)).dialogue.messages
    }
    dispatch({ type: 'dialogues-updated', dialogues: d.dialogues, activeId: d.active_id, messages })
    dispatch({ type: 'memory', memory })
  }, [])

  // Переименование: POST /api/dialogues/{id}/rename {title} → 200 {dialogue}
  const renameDialogue = useCallback(async (id: string, title: string) => {
    try {
      await apiPost<{ dialogue: DialogueMeta }>(`/dialogues/${id}/rename`, { title })
      dispatch({ type: 'renamed', id, title })
    } catch (err) {
      console.error('renameDialogue:', err)
    }
  }, [])

  // Перечитать сообщения активного диалога (например, после /memory/st/clear)
  const reloadDialogue = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    const det = await apiGet<DialogueDetailResponse>(`/dialogues/${id}`)
    dispatch({ type: 'messages', messages: det.dialogue.messages })
  }, [])

  const api: StudioApi = {
    state,
    activeProfile,
    activeTask,
    startTask,
    runTask,
    pauseTask,
    resumeTask,
    sendTaskInstruction,
    resetTask,
    newDialogue,
    setProfile,
    activateDialogue,
    sendMessage,
    setModel,
    updateConfig,
    setShowRequests,
    contextTab: state.contextTab,
    setContextTab,
    refreshMemory,
    setMemoryToggle,
    reloadDialogue,
    deleteDialogues,
    renameDialogue,
  }

  return <StudioCtx.Provider value={api}>{children}</StudioCtx.Provider>
}
