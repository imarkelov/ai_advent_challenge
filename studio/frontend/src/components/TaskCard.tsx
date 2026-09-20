// Карточка процесса задачи (день 13b): stage-агенты в потоке чата.
// Шапка (описание, статус, действия), прогресс, секции plan[]
// (details/summary, время спавна), чек-лист work-шагов + живой бокс.
// История задачи (record перезаписан новой) — из маркеров сообщений.
import { useEffect, useRef, useState } from 'react'
import { useStudio, type Message } from '../state'
import type { TaskPlanEntry, TaskPlanStatus, TaskState, TaskUsage, TaskWorkStep } from '../api'

// Подписи стадий (6 — unified FSM; переезд из TaskTab)
export const STAGE_LABELS: Record<string, string> = {
  planning: 'Планирование',
  execution: 'Исполнение',
  validation: 'Валидация',
  done: 'Завершение',
  paused: 'Пауза',
  failed: 'Ошибка',
}

const PIPELINE: TaskPlanEntry['agent'][] = ['planning', 'execution', 'validation', 'done']

function statusChip(status: TaskPlanStatus): { cls: string; label: string } {
  if (status === 'completed') return { cls: 'tc-chip ok', label: 'готово' }
  if (status === 'in_progress') return { cls: 'tc-chip run', label: 'выполняется' }
  return { cls: 'tc-chip', label: 'ожидает' }
}

// Relative-время «спавн N с назад»; тик 1 c только когда живая
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

// 'YYYY-MM-DD HH:MM:SS' → ms (null — битый формат)
function tsToMs(ts: string | null): number | null {
  if (!ts) return null
  const t = new Date(ts.replace(' ', 'T')).getTime()
  return Number.isNaN(t) ? null : t
}

function spawnAge(spawnTs: string | null, now: number): string | null {
  const t = tsToMs(spawnTs)
  if (t == null) return null
  const s = Math.max(0, Math.round((now - t) / 1000))
  return s < 90 ? `спавн ${s} с назад` : `спавн ${Math.round(s / 60)} мин назад`
}

// ── время работы и токены (день 13b) ────────────────────────────────────────
const numFmt = new Intl.NumberFormat('ru-RU')

// Длительность: < 60с — «45с», иначе — «М мин С с»
export function fmtDuration(s: number): string {
  const v = Math.max(0, Math.round(s))
  if (v < 60) return `${v}с`
  return `${Math.floor(v / 60)} мин ${v % 60} с`
}

// Токены LLM-вызова: «1 234 токенов» (ru-RU)
export function fmtTokens(usage: TaskUsage): string {
  return `${numFmt.format(usage.total)} токенов`
}

// Мета строки стадии: «спавн X назад · работа Nс · T токенов».
// completed — duration_s/usage из записи; in_progress — live-время от spawn_ts;
// без данных (старые задачи/маркеры) — null.
function stageMeta(entry: TaskPlanEntry, now: number): string | null {
  const parts: string[] = []
  const age = spawnAge(entry.spawn_ts, now)
  if (age) parts.push(age)
  if (entry.status === 'completed') {
    if (entry.duration_s != null) parts.push(`работа ${fmtDuration(entry.duration_s)}`)
    if (entry.usage) parts.push(fmtTokens(entry.usage))
  } else if (entry.status === 'in_progress') {
    const t = tsToMs(entry.spawn_ts)
    if (t != null) parts.push(`работа ${fmtDuration((now - t) / 1000)}`)
  }
  return parts.length ? parts.join(' · ') : null
}

// Мета строки work-шага: completed с usage → «45с · 812 токенов»;
// in_progress → live-таймер (startMs — момент, когда карточка увидела шаг;
// тикает с тем же useNow, что и «спавн N назад»).
function stepMeta(ws: TaskWorkStep, startMs: number | null, now: number): string | null {
  if (ws.status === 'completed') {
    const parts: string[] = []
    if (ws.duration_s != null) parts.push(fmtDuration(ws.duration_s))
    if (ws.usage) parts.push(fmtTokens(ws.usage))
    return parts.length ? parts.join(' · ') : null
  }
  if (ws.status === 'in_progress' && startMs != null) {
    return fmtDuration((now - startMs) / 1000)
  }
  return null
}

// Иконки-агенты (inline SVG 14px, паттерн ProfileTab)
function AgentIcon({ agent }: { agent: string }) {
  const common = { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const }
  if (agent === 'planning')
    return <svg {...common}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
  if (agent === 'execution')
    return <svg {...common}><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z" /></svg>
  if (agent === 'validation')
    return <svg {...common}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
  return <svg {...common}><circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" /></svg>
}

// Восстановить состояние задачи из маркеров сообщений (история: record
// перезаписан новой задачей). done-вывод = assistant-сообщение с task_id
// без task_stage (финальный синтез).
export function taskFromMarkers(messages: Message[], taskId: string): TaskState {
  const msgs = messages.filter((m) => m.task_id === taskId)
  const stageOut = (s: string): string =>
    msgs.find((m) => m.task_stage === s && m.role === 'assistant')?.content ?? ''
  const planOut = stageOut
  const doneOut = msgs.find((m) => m.role === 'assistant' && !m.task_stage)?.content ?? ''
  const stepMsgs = msgs.filter((m) => m.task_stage === 'execution' && m.task_step)
  const work_steps: TaskWorkStep[] = stepMsgs.map((m) => ({
    name: m.task_step as string,
    status: 'completed',
    output: m.content,
    ts: null,
    start_ts: null,
    usage: m.task_usage ?? null,
    duration_s: m.task_duration ?? null,
  }))
  const execOut = work_steps.map((w) => `## ${w.name}\n${w.output}`).join('\n\n')
  const hasDone = doneOut !== ''
  // Источник usage/duration стадии: planning/validation — их stage-сообщения,
  // done — финальное сообщение (task_id без task_stage); execution — не
  // восстанавливаем (маркер стадии нет, вывод собран из work-шагов)
  const usageMsgOf = (agent: TaskPlanEntry['agent']): Message | undefined =>
    agent === 'planning' ? msgs.find((m) => m.task_stage === 'planning' && m.role === 'assistant')
      : agent === 'validation' ? msgs.find((m) => m.task_stage === 'validation' && m.role === 'assistant')
        : agent === 'done' ? msgs.find((m) => m.role === 'assistant' && !m.task_stage)
          : undefined
  const plan: TaskPlanEntry[] = PIPELINE.map((agent, i) => {
    const usageMsg = usageMsgOf(agent)
    return {
      step: i + 1,
      agent,
      status: (agent === 'planning' ? planOut('planning') : agent === 'execution' ? execOut : agent === 'validation' ? planOut('validation') : doneOut) ? 'completed' : 'pending',
      output: agent === 'planning' ? planOut('planning') || null
        : agent === 'execution' ? execOut || null
        : agent === 'validation' ? planOut('validation') || null
        : doneOut || null,
      verdict: agent === 'validation'
        ? /<verdict>\s*fail\s*<\/verdict>/i.test(planOut('validation')) ? 'fail' : 'pass'
        : null,
      spawn_ts: null,
      ts: null,
      usage: usageMsg?.task_usage ?? null,
      duration_s: usageMsg?.task_duration ?? null,
    }
  })
  return {
    active: true,
    task_id: taskId,
    stage: hasDone ? 'done' : plan[0].status === 'completed' ? 'execution' : 'planning',
    current_step: hasDone ? 4 : plan.findIndex((p) => p.status !== 'completed') + 1 || 1,
    total_steps: 4,
    expected_action: null,
    plan,
    work_steps,
    context_snapshot: null,
    description: msgs.find((m) => m.role === 'user')?.content ?? '',
    instruction: '',
    retries: 0,
    error: null,
    updated: null,
  }
}

export interface TaskCardProps {
  task: TaskState
  live: boolean // true — живая задача (record активного диалога)
}

export default function TaskCard({ task, live }: TaskCardProps) {
  const { state, pauseTask, resumeTask } = useStudio()
  const running = state.taskRunning && live
  const now = useNow(live)
  const liveStep = live ? state.taskLive : null

  // Live-таймеры work-шагов: момент, когда карточка (re)увидела шаг в
  // in_progress, по индексу шага. Шаг перестал быть in_progress — запись
  // убирается (ретрай не наследует чужой старт).
  const stepStart = useRef<Map<number, number>>(new Map())
  useEffect(() => {
    if (!live) {
      stepStart.current.clear()
      return
    }
    task.work_steps.forEach((ws, i) => {
      if (ws.status === 'in_progress') {
        if (!stepStart.current.has(i)) stepStart.current.set(i, Date.now())
      } else if (stepStart.current.has(i)) {
        stepStart.current.delete(i)
      }
    })
  }, [live, task.work_steps])

  const completedCount = task.plan.filter((e) => e.status === 'completed').length
  const stageLabel = task.stage && STAGE_LABELS[task.stage]
    ? STAGE_LABELS[task.stage]
    : STAGE_LABELS[PIPELINE[task.current_step - 1] ?? 'planning']
  const badge = !task.active
    ? { cls: '', label: '' }
    : task.stage === 'done' ? { cls: 'ok', label: 'Готово' }
    : task.stage === 'paused' ? { cls: 'warn', label: 'Пауза' }
    : task.stage === 'failed' ? { cls: 'err', label: 'Ошибка' }
    : running ? { cls: 'run', label: 'Выполняется' }
    : { cls: '', label: 'Готово к запуску' }

  // Авто-развёртывание: live — активная запись; failed — упавшая; иная — всё свёрнуто
  const activeEntry = live
    ? task.plan.find((e) => e.status === 'in_progress')
    : null
  const failedEntry = task.stage === 'failed'
    ? task.plan.find((e) => e.status !== 'completed')
    : null

  const stepIcon = (st: TaskPlanStatus) =>
    st === 'completed' ? '✓' : st === 'in_progress' ? '⏳' : '○'

  return (
    <div className="task-card">
      <div className="task-card-head">
        <span className="task-card-title" title={task.description}>
          Задача: {task.description}
        </span>
        {badge.label && <span className={`tc-badge ${badge.cls}`}>{badge.label}</span>}
        {live && task.stage && task.stage !== 'paused' && task.stage !== 'failed' && running && (
          <button type="button" className="btn danger tc-btn" onClick={() => void pauseTask()}>
            Пауза
          </button>
        )}
        {live && task.stage === 'paused' && (
          <button type="button" className="btn tc-btn" onClick={() => void resumeTask()}>
            Продолжить
          </button>
        )}
        {live && task.stage === 'failed' && (
          <button type="button" className="btn tc-btn" onClick={() => void resumeTask()}>
            Повтор
          </button>
        )}
      </div>

      <div className="task-card-progress" aria-label="Прогресс задачи">
        <div className="task-card-bar">
          <div className="task-card-bar-fill"
               style={{ width: `${(completedCount / task.total_steps) * 100}%` }} />
        </div>
        <span className="task-card-step">
          Этап {task.current_step}/{task.total_steps}: {stageLabel}
        </span>
      </div>

      {task.error && task.stage === 'failed' && (
        <div className="task-card-error">{task.error}</div>
      )}

      {task.plan.map((entry) => {
        const chip = statusChip(entry.status)
        const meta = stageMeta(entry, now)
        const open = entry === activeEntry || entry === failedEntry
        return (
          <details key={entry.agent + entry.status} className="task-agent" open={open}>
            <summary>
              <AgentIcon agent={entry.agent} />
              <span className="task-agent-name">{STAGE_LABELS[entry.agent]}</span>
              <span className={chip.cls}>{chip.label}</span>
              {meta && <span className="task-agent-age">{meta}</span>}
              {entry.verdict && (
                <span className={`task-verdict ${entry.verdict}`}>
                  {entry.verdict === 'pass' ? 'прошла' : 'не прошла'}
                </span>
              )}
            </summary>
            <div className="task-agent-body">
              {entry.agent === 'execution' ? (
                <ul className="task-checklist">
                  {task.work_steps.map((ws, i) => {
                    const smeta = stepMeta(
                      ws,
                      live ? stepStart.current.get(i) ?? null : null,
                      now,
                    )
                    return (
                      <li key={i} className={`task-check ${ws.status}`}>
                        <span className="task-check-icon" aria-hidden>{stepIcon(ws.status)}</span>
                        <span className="task-check-name">{ws.name}</span>
                        {smeta && <span className="task-check-meta">{smeta}</span>}
                        {ws.output && <pre className="task-check-output">{ws.output}</pre>}
                      </li>
                    )
                  })}
                  {liveStep && entry.status === 'in_progress' && (
                    <div className="task-live">
                      <span className="task-live-prompt" aria-hidden>»</span>
                      {liveStep.text}
                      <span className="caret" aria-hidden />
                    </div>
                  )}
                </ul>
              ) : (
                entry.output && <pre className="task-agent-output">{entry.output}</pre>
              )}
            </div>
          </details>
        )
      })}
    </div>
  )
}
