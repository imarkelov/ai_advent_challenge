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
  addInvariant as apiAddInvariant,
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
  deleteInvariant as apiDeleteInvariant,
  getInvariants,
  taskStream,
  toggleInvariant as apiToggleInvariant,
  type ChatEvent,
  type Invariant,
  type TaskEvent,
  type TaskPlanStatus,
  type TaskState,
  type TaskUsage,
  type TaskWorkStep,
  type UserProfile,
} from './api'

// Таймаут ожидания освобождения run-слота: resume во время чужого run
// не должен теряться (см. resumeTask / sendTaskMessage).
const sleep = (ms: number) => new Promise<void>((r) => { setTimeout(r, ms) })

// ── Типы по контракту API дня 11 ────────────────────────────────────────────
export type Role = 'system' | 'user' | 'assistant'

export interface Message {
  role: Role
  content: string
  // Модель, которой выполнен запрос (assistant-сообщения из бэкенда; старые — без поля)
  model?: string
  // Стадия задачи, выполненная stage-агентом (день 13; обычные сообщения — без поля)
  task_stage?: string
  // Маркеры задачи (день 13b): id задачи + work-шаг (обычные — без полей)
  task_id?: string
  task_step?: string
  // Токены/длительность LLM-вызова (день 13b): для восстановления карточки
  // из маркеров после перезагрузки (старые сообщения — без полей)
  task_usage?: TaskUsage
  task_duration?: number
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
  // Задача в диалоге использовалась (день 13b): персистентный флаг —
  // ставится при task/start, task/reset его не сбрасывает
  used_task?: boolean
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
export type ContextTab = 'memory' | 'tokens' | 'request' | 'profile' | 'invariants'

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
  // Живой вывод текущего work-шага (step_delta; сбрасывается при смене шага)
  taskLive: { index: number; text: string } | null
  // Режим ввода (день 13b, D8): глобальный, persist в localStorage
  chatMode: 'chat' | 'task'
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
  // Инварианты (день 14): жёсткие правила (ассистент не меняет, пользователь — вкл/выкл)
  invariants: Invariant[]
  // Нарушение активного инварианта (SSE invariant_violation, день 14):
  // паттерны последнего ответа с нарушением; transient — сбрасывается при
  // новом сообщении (user-message) и при смене диалога; null — нарушения нет
  invariantViolation: string[] | null
}

// Ключ localStorage для тумблера «Показывать запросы»
export const SHOW_REQUESTS_KEY = 'studio.showRequests'

// Ключ localStorage для режима ввода (день 13b, D8)
export const CHAT_MODE_KEY = 'studio.chatMode'

// Начальное состояние (showRequests читается из localStorage, default true)
export function initialState(): StudioState {
  let show = true
  try {
    const v = localStorage.getItem(SHOW_REQUESTS_KEY)
    if (v !== null) show = v === '1'
  } catch {
    // нет localStorage (небраузерная среда) — держим default
  }
  let mode: 'chat' | 'task' = 'chat'
  try {
    const v = localStorage.getItem(CHAT_MODE_KEY)
    if (v === 'chat' || v === 'task') mode = v
  } catch {
    // нет localStorage — держим default
  }
  return {
    loaded: false,
    config: null,
    dialogues: [],
    profiles: {},
    tasks: {},
    taskRunning: false,
    taskLive: null,
    chatMode: mode,
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
    invariants: [],
    invariantViolation: null,
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
  | { type: 'chat-mode'; mode: 'chat' | 'task' }
  | { type: 'task-step'; index: number; name: string; status: TaskPlanStatus; output?: string; usage?: TaskUsage | null; duration_s?: number | null }
  | { type: 'task-step-delta'; index: number; text: string }
  | { type: 'models'; models: ModelInfo[] }
  | { type: 'config'; config: Config }
  | { type: 'last-request'; detail: RequestDetail | null }
  | { type: 'show-requests'; on: boolean }
  | { type: 'context-tab'; tab: ContextTab }
  | { type: 'invariants'; invariants: Invariant[] }
  | { type: 'invariant-violation'; patterns: string[] }

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
        // новый диалог — нарушение инварианта прежнего ответа не несём
        invariantViolation: null,
      }
    case 'activated':
      return {
        ...state,
        activeId: action.activeId,
        dialogues: action.dialogues,
        profiles: profilesFrom(action.dialogues),
        tasks: tasksFrom(action.dialogues),
        messages: action.messages,
        // смена диалога — transient-флаг нарушения не пересекает границу
        invariantViolation: null,
      }
    case 'user-message':
      // новая очередь — флаг нарушения предыдущего ответа сбрасывается
      return {
        ...state,
        messages: [...state.messages, action.message],
        streaming: true,
        invariantViolation: null,
      }
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
      return { ...state, taskRunning: action.on }
    case 'chat-mode':
      return { ...state, chatMode: action.mode }
    case 'task-step': {
      const id = state.activeId
      if (id == null) return state
      const task = state.tasks[id]
      if (!task) return state
      const work_steps = task.work_steps.map((ws, j) => {
        if (j !== action.index) return ws
        const next: TaskWorkStep = {
          ...ws, name: action.name, status: action.status,
          output: action.output !== undefined ? action.output : ws.output,
        }
        // usage/duration_s приходят только в step_updated «completed»;
        // in_progress-событие их не несёт — старые значения не трогаем
        if (action.usage !== undefined) next.usage = action.usage
        if (action.duration_s !== undefined) next.duration_s = action.duration_s
        return next
      })
      return {
        ...state,
        tasks: { ...state.tasks, [id]: { ...task, work_steps } },
        taskLive: action.status === 'in_progress'
          ? { index: action.index, text: '' }
          : action.status === 'completed'
            ? null
            : state.taskLive,
      }
    }
    case 'task-step-delta':
      return {
        ...state,
        taskLive: {
          index: action.index,
          text: (state.taskLive && state.taskLive.index === action.index
            ? state.taskLive.text
            : '') + action.text,
        },
      }
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
    case 'invariants':
      return { ...state, invariants: action.invariants }
    case 'invariant-violation':
      // SSE invariant_violation (до done): бейдж нарушения в шапке чата
      return { ...state, invariantViolation: action.patterns }
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
  // Режим ввода (день 13b): 'chat' | 'task', persist в localStorage
  chatMode: 'chat' | 'task'
  setChatMode: (mode: 'chat' | 'task') => void
  // Режим «задача»: сообщение = инструкция на паузе / запуск задачи
  sendTaskMessage: (text: string) => Promise<void>
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
  // Инварианты (день 14): перечитать/добавить/переключить/удалить — все
  // перечитывают список после ответа API (паттерн refreshMemory)
  refreshInvariants: () => Promise<void>
  addInvariant: (
    title: string,
    description: string,
    forbidden: string[],
    is_active?: boolean,
  ) => Promise<void>
  toggleInvariant: (id: string) => Promise<void>
  deleteInvariant: (id: string) => Promise<void>
  // Паттерны последнего ответа с нарушением активного инварианта (null — нет)
  invariantViolation: string[] | null
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
  // Защита от двойного продолжить/повтор (двойной клик по кнопке)
  const resumeInFlight = useRef(false)

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

  // Перечитать сообщения активного диалога (например, после /memory/st/clear;
  // день 13b — после run: user-маркер + stage-сообщения с маркерами)
  const reloadDialogue = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    const det = await apiGet<DialogueDetailResponse>(`/dialogues/${id}`)
    dispatch({ type: 'messages', messages: det.dialogue.messages })
  }, [])

  // Пайплайн задачи: SSE-стрим POST /api/task/run (день 13b).
  // agent_spawned/stage_done — авторитетное состояние из бэкенда (reloadTask);
  // step_updated/step_delta — живой вывод work-шага (taskLive);
  // task_done — финальный ответ (новое assistant-сообщение);
  // task_paused/task_resumed — перечитать; task_failed/error — ошибка в чат.
  const runTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null || stateRef.current.taskRunning) return
    dispatch({ type: 'task-running', on: true })
    try {
      await taskStream(id, (e: TaskEvent) => {
        if (e.type === 'agent_spawned') {
          // план-запись in_progress + spawn_ts — авторитетно из бэкенда
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'step_updated') {
          dispatch({
            type: 'task-step',
            index: e.index, name: e.name, status: e.status, output: e.output,
            usage: e.usage, duration_s: e.duration_s,
          })
        } else if (e.type === 'step_delta') {
          dispatch({ type: 'task-step-delta', index: e.index, text: e.text })
        } else if (e.type === 'stage_done' || e.type === 'task_paused'
            || e.type === 'task_resumed') {
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'task_done') {
          // Финальный ответ — НОВОЕ assistant-сообщение (bubble под карточкой)
          dispatch({
            type: 'messages',
            messages: [
              ...stateRef.current.messages,
              { role: 'assistant', content: e.answer },
            ],
          })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else {  // task_failed | error
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
      void reloadDialogue().catch((err) => console.error('reloadDialogue:', err))
    }
  }, [reloadTask, reloadDialogue])

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

  // Продолжить: POST /api/task/resume + повторный запуск пайплайна.
  // Слот run глобальный — если стримится run другого диалога (taskRunning),
  // resume НЕ теряем: ждём освобождения слота и только затем резюмируем
  // (иначе состояние бэкенда рассинхронизируется с фронтендом). Если после
  // ожидания задача уже не в paused/failed (устаревшее фронтенд-состояние),
  // 400-гард бэкенда отклонит resume — перечитываем авторитетную задачу.
  const resumeTask = useCallback(async () => {
    if (resumeInFlight.current) return
    resumeInFlight.current = true
    try {
      const id = stateRef.current.activeId
      if (id == null) return
      // Ждём, пока run-слот свободен (чужой run может стримиться долго)
      while (stateRef.current.taskRunning) {
        await sleep(400)
      }
      await apiPostTaskResume(id)
      await reloadTask()
      await runTask()
    } catch (err) {
      // Задача уже не resumable (400) — синхронизируем состояние с бэкендом
      console.error('resumeTask:', err)
      await reloadTask()
    } finally {
      resumeInFlight.current = false
    }
  }, [reloadTask, runTask])

  // Инструкция на паузе: POST /api/task/instruction
  const sendTaskInstruction = useCallback(async (text: string) => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskInstruction(id, text)
    dispatch({ type: 'task-set', id, task })
  }, [])

  // Режим ввода (день 13b, D8): persist + dispatch
  const setChatMode = useCallback((mode: 'chat' | 'task') => {
    try {
      localStorage.setItem(CHAT_MODE_KEY, mode)
    } catch {
      // нет localStorage — переключатель просто не сохранится
    }
    dispatch({ type: 'chat-mode', mode })
  }, [])

  // Режим «задача»: сообщение = инструкция (paused) / запуск (иначе).
  // На failed ввод заблокирован UI — повтор кнопкой «Повтор» в карточке.
  const sendTaskMessage = useCallback(async (text: string) => {
    const id = stateRef.current.activeId
    const trimmed = text.trim()
    if (id == null || !trimmed || stateRef.current.taskRunning) return
    const task = stateRef.current.tasks[id]
    if (task && task.active) {
      if (task.stage === 'paused') {
        await sendTaskInstruction(trimmed)
        return
      }
      if (task.stage === 'failed' || task.stage === 'done') return
    }
    await startTask(trimmed)
    await reloadDialogue()
    await runTask()
  }, [startTask, sendTaskInstruction, reloadTask, reloadDialogue])

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
    // Инварианты (день 14) — отдельным запросом: недоступность эндпоинта
    // (старый бэкенд) не ломает основную загрузку (паттерн models)
    getInvariants()
      .then((invariants) => dispatch({ type: 'invariants', invariants }))
      .catch((err) => console.error('invariants:', err))
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
    const [d, det, memory, invariants] = await Promise.all([
      apiGet<DialoguesResponse>('/dialogues'),
      apiGet<DialogueDetailResponse>(`/dialogues/${id}`),
      // Панели памяти: WM читаем в том же батче — иначе после переключения
      // диалога «текущая задача» останется от прежнего
      apiGet<MemoryState>('/memory'),
      // Инварианты (день 14): перечитываем при переключении диалога
      getInvariants(),
    ])
    dispatch({
      type: 'activated',
      activeId: d.active_id ?? id,
      dialogues: d.dialogues,
      messages: det.dialogue.messages,
    })
    dispatch({ type: 'memory', memory })
    dispatch({ type: 'invariants', invariants })
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
          } else if (e.type === 'invariant_violation') {
            // нарушение активного инварианта (до done): бейдж в шапке чата
            dispatch({ type: 'invariant-violation', patterns: e.patterns })
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

  // Перечитать инварианты (день 14)
  const refreshInvariants = useCallback(async () => {
    dispatch({ type: 'invariants', invariants: await getInvariants() })
  }, [])

  // Добавить инвариант: POST /api/invariants {title, description, forbidden, is_active}
  // → перечитать список
  const addInvariant = useCallback(
    async (title: string, description: string, forbidden: string[], is_active: boolean = true) => {
      await apiAddInvariant(title, description, forbidden, is_active)
      await refreshInvariants()
    },
    [refreshInvariants],
  )

  // Переключить инвариант: POST /api/invariants/{id}/toggle → перечитать список
  const toggleInvariant = useCallback(
    async (id: string) => {
      await apiToggleInvariant(id)
      await refreshInvariants()
    },
    [refreshInvariants],
  )

  // Удалить инвариант: DELETE /api/invariants/{id} → перечитать список
  const deleteInvariant = useCallback(
    async (id: string) => {
      await apiDeleteInvariant(id)
      await refreshInvariants()
    },
    [refreshInvariants],
  )

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
    chatMode: state.chatMode,
    setChatMode,
    sendTaskMessage,
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
    refreshInvariants,
    addInvariant,
    toggleInvariant,
    deleteInvariant,
    invariantViolation: state.invariantViolation,
    reloadDialogue,
    deleteDialogues,
    renameDialogue,
  }

  return <StudioCtx.Provider value={api}>{children}</StudioCtx.Provider>
}
