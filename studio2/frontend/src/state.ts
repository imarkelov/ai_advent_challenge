import type { RunEvent } from './api'

export type AgentStatus = 'running' | 'done' | 'failed' | 'idle'

export interface AgentRow {
  role: string
  model: string
  status: AgentStatus
  text: string
  summary: string
}

export interface JournalRow {
  at: string
  role: string
  text: string
}

export interface SkeletonFile {
  role: string
  path: string
}

export interface PendingQuestion {
  id: string
  role: string
  text: string
  options: string[]
}

export interface PipelineState {
  runId: string | null
  task: string
  stage: string
  round: number
  status: string
  verdict: string | null
  reportPath: string
  agents: AgentRow[]
  sddFiles: SkeletonFile[]
  skeletonReady: boolean
  executionChosen: string | null
  question: PendingQuestion | null
  journal: JournalRow[]
  error: string | null
}

export type Action =
  | { type: 'event'; payload: RunEvent }
  | { type: 'reset' }
  | { type: 'error'; message: string }

export function initialState(): PipelineState {
  return {
    runId: null,
    task: '',
    stage: 'idle',
    round: 1,
    status: 'idle',
    verdict: null,
    reportPath: '',
    agents: [],
    sddFiles: [],
    skeletonReady: false,
    executionChosen: null,
    question: null,
    journal: [],
    error: null,
  }
}

function stamp(): string {
  return new Date().toLocaleTimeString('ru-RU', { hour12: false })
}

function withAgent(agents: AgentRow[], role: string): AgentRow[] {
  return agents.some((agent) => agent.role === role)
    ? agents
    : [...agents, { role, model: '', status: 'idle', text: '', summary: '' }]
}

export function pipelineReducer(state: PipelineState, action: Action): PipelineState {
  if (action.type === 'reset') return initialState()
  if (action.type === 'error') return { ...state, error: action.message }

  const event = action.payload
  switch (event.type) {
    case 'run_started':
      return {
        ...initialState(),
        runId: String(event.run_id ?? state.runId ?? ''),
        task: String(event.task ?? ''),
        stage: 'sdd',
        status: 'running',
      }
    case 'stage_changed':
      return {
        ...state,
        stage: String(event.stage ?? state.stage),
        round: Number(event.round ?? state.round),
        journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: `стадия: ${event.stage}` }],
      }
    case 'agent_started': {
      const role = String(event.role ?? '')
      const agents = withAgent(state.agents, role).map((agent) =>
        agent.role === role ? { ...agent, model: String(event.model ?? ''), status: 'running' as AgentStatus } : agent,
      )
      return { ...state, agents, journal: [...state.journal, { at: stamp(), role, text: 'старт' }] }
    }
    case 'agent_delta': {
      const role = String(event.role ?? '')
      const agents = withAgent(state.agents, role).map((agent) =>
        agent.role === role ? { ...agent, text: agent.text + String(event.text ?? '') } : agent,
      )
      return { ...state, agents, journal: [...state.journal, { at: stamp(), role, text: String(event.text ?? '') }] }
    }
    case 'agent_done': {
      const role = String(event.role ?? '')
      const status = String(event.status ?? 'done')
      const agents = withAgent(state.agents, role).map((agent) =>
        agent.role === role
          ? { ...agent, status: (status === 'fail' ? 'failed' : 'done') as AgentStatus, summary: String(event.summary ?? '') }
          : agent,
      )
      return { ...state, agents, journal: [...state.journal, { at: stamp(), role, text: `завершён: ${status}` }] }
    }
    case 'sdd_written':
      return {
        ...state,
        sddFiles: [...state.sddFiles, { role: String(event.role), path: String(event.path) }],
        journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: `SDD: ${event.path}` }],
      }
    case 'skeleton_ready':
      return { ...state, skeletonReady: true, journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: 'каркас готов' }] }
    case 'execution_chosen':
      return { ...state, executionChosen: String(event.choice) }
    case 'review_verdict':
      return { ...state, verdict: String(event.verdict) }
    case 'question':
      return {
        ...state,
        question: {
          id: String(event.question_id),
          role: String(event.role ?? ''),
          text: String(event.text ?? ''),
          options: (event.options as string[]) ?? [],
        },
      }
    case 'question_answered':
      return { ...state, question: null }
    case 'run_done':
      return {
        ...state,
        status: String(event.status ?? 'done'),
        reportPath: String(event.report_path ?? ''),
        journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: `итог: ${event.status}` }],
      }
    case 'error':
      return { ...state, error: String(event.message ?? 'ошибка'), status: 'failed' }
    default:
      return state
  }
}
