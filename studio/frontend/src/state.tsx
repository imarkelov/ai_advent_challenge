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
import { apiGet, apiPost, chatStream, type ChatEvent } from './api'

// ── Типы по контракту API дня 11 ────────────────────────────────────────────
export type Role = 'system' | 'user' | 'assistant'

export interface Message {
  role: Role
  content: string
}

export interface DialogueMeta {
  id: number
  title: string
  created: string
  message_count: number
}

export interface MemoryLayer {
  entries: number
  tokens_est: number
  items: Record<string, string>
}

export interface MemoryState {
  active_id: number | null
  dialogue: { message_count: number; tokens_est: number }
  working: MemoryLayer
  long_term: MemoryLayer
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

export interface StudioState {
  loaded: boolean
  config: Config | null
  dialogues: DialogueMeta[]
  activeId: number | null
  messages: Message[]
  memory: MemoryState | null
  tokens: TokenState | null
  requests: RequestSummary[]
  showRequests: boolean
  streaming: boolean
  lastRequest: RequestDetail | null
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
    activeId: null,
    messages: [],
    memory: null,
    tokens: null,
    requests: [],
    showRequests: show,
    streaming: false,
    lastRequest: null,
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

export type StudioAction =
  | {
      type: 'loaded'
      config: Config
      dialogues: DialogueMeta[]
      activeId: number | null
      memory: MemoryState
      tokens: TokenState
      requests: RequestSummary[]
    }
  | { type: 'messages'; messages: Message[] }
  | { type: 'created'; dialogue: DialogueMeta; activeId: number }
  | { type: 'activated'; activeId: number; dialogues: DialogueMeta[]; messages: Message[] }
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
  | { type: 'last-request'; detail: RequestDetail | null }
  | { type: 'show-requests'; on: boolean }

// Чистый reducer: все переходы состояния без побочных эффектов
export function reducer(state: StudioState, action: StudioAction): StudioState {
  switch (action.type) {
    case 'loaded':
      return {
        ...state,
        loaded: true,
        config: action.config,
        dialogues: action.dialogues,
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
        activeId: action.activeId,
        messages: [],
      }
    case 'activated':
      return {
        ...state,
        activeId: action.activeId,
        dialogues: action.dialogues,
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
    case 'last-request':
      return { ...state, lastRequest: action.detail }
    case 'show-requests':
      return { ...state, showRequests: action.on }
  }
}

// ── React-слой: провайдер + хук ─────────────────────────────────────────────

export interface StudioApi {
  state: StudioState
  newDialogue: () => Promise<void>
  activateDialogue: (id: number) => Promise<void>
  sendMessage: (text: string) => Promise<void>
  setShowRequests: (on: boolean) => void
  refreshMemory: () => Promise<void>
  reloadDialogue: () => Promise<void>
}

const StudioCtx = createContext<StudioApi | null>(null)

// Хук доступа к состоянию «Студии» (только внутри <StudioProvider>)
export function useStudio(): StudioApi {
  const ctx = useContext(StudioCtx)
  if (!ctx) throw new Error('useStudio должен вызываться внутри <StudioProvider>')
  return ctx
}

interface DialoguesResponse {
  active_id: number | null
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
  }, [])

  useEffect(() => {
    // Бэкенд недоступен — приложение останется в пустом состоянии (поллинга нет)
    void loadAll().catch((err) => console.error('loadAll:', err))
  }, [loadAll])

  // Новый диалог: POST /api/dialogues → 201 {dialogue, active_id}
  const newDialogue = useCallback(async () => {
    const r = await apiPost<{ dialogue: DialogueMeta; active_id: number }>('/dialogues')
    dispatch({ type: 'created', dialogue: r.dialogue, activeId: r.active_id })
    await refreshPanels()
  }, [refreshPanels])

  // Активация диалога: POST .../activate + загрузка его сообщений
  const activateDialogue = useCallback(async (id: number) => {
    if (id === stateRef.current.activeId) return
    await apiPost<{ active_id: number }>(`/dialogues/${id}/activate`)
    const [d, det] = await Promise.all([
      apiGet<DialoguesResponse>('/dialogues'),
      apiGet<DialogueDetailResponse>(`/dialogues/${id}`),
    ])
    dispatch({
      type: 'activated',
      activeId: d.active_id ?? id,
      dialogues: d.dialogues,
      messages: det.dialogue.messages,
    })
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

  // Тумблер «Показывать запросы» (персистится в localStorage)
  const setShowRequests = useCallback((on: boolean) => {
    try {
      localStorage.setItem(SHOW_REQUESTS_KEY, on ? '1' : '0')
    } catch {
      // нет localStorage — переключатель просто не сохранится
    }
    dispatch({ type: 'show-requests', on })
  }, [])

  // Перечитать память (после изменений в MemoryTab)
  const refreshMemory = useCallback(async () => {
    dispatch({ type: 'memory', memory: await apiGet<MemoryState>('/memory') })
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
    newDialogue,
    activateDialogue,
    sendMessage,
    setShowRequests,
    refreshMemory,
    reloadDialogue,
  }

  return <StudioCtx.Provider value={api}>{children}</StudioCtx.Provider>
}
