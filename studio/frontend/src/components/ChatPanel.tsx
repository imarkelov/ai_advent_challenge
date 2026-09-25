// Центральная панель: шапка (название + бейдж профиля + дропдаун модели),
// лента сообщений (включая карточки процесса задачи, день 13b, и карточки
// результатов MCP-инструментов, день 16), тумблер режимов чат/задача и
// инпут-капсула. В режиме «чат» команда «/» открывает автодополнение
// MCP-серверов/инструментов (день 16, tool-loop).
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useStudio, type Message } from '../state'
import type { McpServer, McpTool } from '../api'
import TaskCard, { taskFromMarkers } from './TaskCard'
import SaveMessageModal from './SaveMessageModal'
import ToolCallModal from './ToolCallModal'

// Строка автодополнения «/» (день 16): сервер (tier-1), инструмент
// (tier-2) или подсказка (сервер не подключён)
type AcItem =
  | { kind: 'server'; server: McpServer }
  | { kind: 'tool'; tool: McpTool; serverId: string; serverName: string }
  | { kind: 'hint'; text: string }

// Выбранный инструмент — данные модалки вызова (день 16)
type ToolSel = { serverId: string; serverName: string; tool: McpTool }

// Вызов инструмента LLM (день 17, LLM-driven tool-loop): форма OpenAI
// tool_calls (assistant-сообщение из бэкенда; поля могут отсутствовать)
interface LlmToolCall {
  id?: string
  type?: string
  function?: { name?: string; arguments?: string }
}

const STATUS_HINT: Record<string, string> = {
  idle: 'не подключён',
  connected: 'подключён',
  error: 'ошибка',
}

// Оценка токенов (клиентская эвристика, без API): норма ~0.44 токена/символ,
// откалибрована на кириллической норме рабочей модели qwen3.8-27b
function estTokens(s?: string | null): number {
  return s ? Math.max(1, Math.round(s.length * 0.44)) : 0
}

// Строка шага «Шагов агента»: один вызов (tool_call) или один результат
// (role:"tool") — самостоятельный сворачиваемый ряд (своё состояние,
// свёрнут по умолчанию): шапка = chip + ≈ токенов + caret; тело — payload в <pre>
function StepRow({ variant, chip, chipTitle, payload }: {
  variant: 'call' | 'result'
  chip: string
  chipTitle: string
  payload: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className={variant === 'call' ? 'step-row call' : 'step-row result'}>
      <button
        type="button"
        className="step-row-header"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="tool-card-chip" title={chipTitle}>{chip}</span>
        <span className="step-tokens" title="Оценка токенов (эвристика)">≈ {estTokens(payload)} tok</span>
        <span className="agent-steps-caret" aria-hidden>{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="step-row-body">
          <pre className="tool-card-pre">{payload}</pre>
        </div>
      )}
    </div>
  )
}

// Сворачиваемый блок «Шаги агента»: подряд идущие tool-сообщения
// (assistant.tool_calls / role:"tool") — в одной группе, свёрнута по
// умолчанию; в развёрнутом теле каждый шаг — свой сворачиваемый ряд (StepRow).
function AgentSteps({ messages }: { messages: Message[] }) {
  const [open, setOpen] = useState(false)
  const calls = messages.flatMap((m) =>
    Array.isArray(m.tool_calls) ? (m.tool_calls as LlmToolCall[]) : [],
  )
  // Уникальные имена тулов в порядке первого появления (для бейджей)
  const names: string[] = []
  for (const tc of calls) {
    const n = tc.function?.name
    if (n && !names.includes(n)) names.push(n)
  }
  // Суммарная оценка токенов группы: аргументы всех вызовов + содержимое
  // всех результатов
  const totalTokens = messages.reduce((sum, m) => {
    if (Array.isArray(m.tool_calls)) {
      for (const tc of m.tool_calls as LlmToolCall[]) sum += estTokens(tc.function?.arguments)
    } else if (m.role === 'tool') {
      sum += estTokens(m.content)
    }
    return sum
  }, 0)
  return (
    <div className="agent-steps">
      <button
        type="button"
        className="agent-steps-header"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="agent-steps-title">🧩 Шаги агента · {calls.length}</span>
        {names.map((n) => (
          <span key={n} className="tool-badge">{n}</span>
        ))}
        <span className="step-tokens" title="Оценка токенов (эвристика)">≈ {totalTokens} tok</span>
        <span className="agent-steps-caret" aria-hidden>{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="agent-steps-body">
          {messages.map((m, j) => {
            // Вызов инструмента (assistant.tool_calls): каждый вызов — свой
            // сворачиваемый ряд; текст assistant — строка-заметка сверху
            // (не сворачивается)
            if (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) {
              return (
                <div key={j} className="agent-steps-msg">
                  {m.content && <div className="tool-card-note">{m.content}</div>}
                  {(m.tool_calls as LlmToolCall[]).map((tc, k) => (
                    <StepRow
                      key={tc.id ?? k}
                      variant="call"
                      chip={`🔧 ${tc.function?.name ?? 'инструмент'}`}
                      chipTitle="Модель вызвала инструмент"
                      payload={tc.function?.arguments ?? ''}
                    />
                  ))}
                </div>
              )
            }
            // Результат инструмента (role:"tool"): свой сворачиваемый ряд
            if (m.role === 'tool') {
              return (
                <StepRow
                  key={j}
                  variant="result"
                  chip={`↳ ${m.name ?? 'инструмент'}`}
                  chipTitle="Результат инструмента"
                  payload={m.content}
                />
              )
            }
            return null
          })}
        </div>
      )}
    </div>
  )
}

export default function ChatPanel() {
  const {
    state, activeProfile, sendMessage, setModel,
    openSettings, closeSettings, openMcp, closeMcp,
    activeTask, chatMode, setChatMode, sendTaskMessage,
    callMcpTool,
  } = useStudio()
  const [draft, setDraft] = useState('')
  const [saveMsg, setSaveMsg] = useState<Message | null>(null)
  // Автодополнение «/» (день 16): выбранная модалка инструмента + позиция
  // подсветки + флаг скрытия по Escape (сбрасывается при вводе)
  const [toolSel, setToolSel] = useState<ToolSel | null>(null)
  const [acIndex, setAcIndex] = useState(0)
  const [acDismissed, setAcDismissed] = useState(false)
  const feedRef = useRef<HTMLDivElement>(null)
  const active = state.dialogues.find((d) => d.id === state.activeId)

  // День 13b: живая задача активного диалога
  const task = activeTask && activeTask.active ? activeTask : null
  const taskRunning = state.taskRunning
  const taskBusy = task != null && taskRunning
    && task.stage != null
    && !['done', 'paused', 'failed'].includes(task.stage)

  // Карточки: каждое первое сообщение с данным task_id — якорь карточки
  const cardTaskIds = useMemo(() => {
    const seen = new Set<string>()
    const out: { taskId: string; index: number }[] = []
    state.messages.forEach((m, i) => {
      if (m.task_id && !seen.has(m.task_id)) {
        seen.add(m.task_id)
        out.push({ taskId: m.task_id, index: i })
      }
    })
    return out
  }, [state.messages])
  const cardAt = useMemo(() => {
    const map = new Map<number, string>()
    for (const c of cardTaskIds) map.set(c.index, c.taskId)
    return map
  }, [cardTaskIds])

  // Данные карточки: live — record активного диалога (если task_id совпадает),
  // иначе восстановление из маркеров (история задачи)
  const cardData = (taskId: string) => {
    if (task && task.task_id === taskId) return { t: task, live: true }
    return { t: taskFromMarkers(state.messages, taskId), live: false }
  }

  // ── Лента: группировка tool-сообщений (LLM-driven tool-loop, день 17) ──
  // Подряд идущие tool-сообщения (assistant.tool_calls / role:"tool") —
  // в одной сворачиваемой группе «Шаги агента»; остальное — по одному
  // элементу с ИСХОДНЫМ индексом сообщения (якорь TaskCard cardAt.has(i)
  // и caret isTail от него зависят). task_stage-сообщения — внутри карточки.
  type FeedItem =
    | { kind: 'group'; key: number; messages: Message[] }
    | { kind: 'msg'; index: number }
  const feedItems: FeedItem[] = []
  state.messages.forEach((m, i) => {
    if (m.task_stage) return
    const isTool = (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) || m.role === 'tool'
    if (isTool) {
      const last = feedItems[feedItems.length - 1]
      if (last && last.kind === 'group') last.messages.push(m)
      else feedItems.push({ kind: 'group', key: i, messages: [m] })
    } else {
      feedItems.push({ kind: 'msg', index: i })
    }
  })

  // Обычное сообщение (user/assistant) или карточка MCP (день 16);
  // key = исходный индекс сообщения
  const renderMsg = (m: Message, i: number) => {
    const isTail = i === state.messages.length - 1
    // Результат вызова MCP-инструмента (день 16): отдельная карточка
    // (chip + вывод), без «в памяти»
    if (m.mcp_tool) {
      const mt = m.mcp_tool
      const serverName = state.mcpServers.find((s) => s.id === mt.server)?.name ?? mt.server
      return (
        <div key={i} className="mcp-card">
          <span className="mcp-card-chip" title="Результат вызова MCP-инструмента">
            MCP {serverName}/{mt.tool}
          </span>
          <pre className="mcp-card-pre">{m.content}</pre>
        </div>
      )
    }
    return (
      <div key={i}>
        <div className={m.role === 'user' ? 'msg user' : 'msg assistant'}>
          <div className="msg-role">
            {m.role === 'user' ? 'вы' : 'модель'}
            <button
              type="button"
              className="btn-icon msg-save"
              title="Сохранить в память"
              onClick={() => setSaveMsg(m)}
            >
              в память
            </button>
          </div>
          <div className="msg-text">
            {m.content}
            {state.streaming && isTail && m.role === 'assistant' && (
              <span className="caret" aria-hidden />
            )}
          </div>
          {m.role === 'assistant' && m.model && (
            <span className="msg-model-chip" title="Модель, которой выполнен запрос">
              {m.model}
            </span>
          )}
        </div>
        {cardAt.has(i) && (() => {
          const { t, live } = cardData(cardAt.get(i) as string)
          return <TaskCard task={t} live={live} />
        })()}
      </div>
    )
  }

  const currentModel = state.config?.model ?? ''
  const currentInList = state.models.some((m) => m.id === currentModel)
  const modelOptions = currentInList
    ? state.models
    : currentModel
      ? [{ id: currentModel, context_limit: 0 }, ...state.models]
      : state.models

  useEffect(() => {
    const el = feedRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.messages, state.streaming, state.taskLive])

  // ── Автодополнение команд «/» (день 16, tool-loop; только режим «чат») ──
  // Tier-1 (один токен «/query»): серверы (enabled) по префиксу имени.
  // Tier-2 («/Server tool»): инструменты сервера по префиксу; сервер не
  // подключён — строка-подсказка без выбора.
  const trimmed = draft.trim()
  const isCmd = chatMode === 'chat' && trimmed.startsWith('/')
  let acItems: AcItem[] = []
  if (isCmd && !acDismissed) {
    const parts = trimmed.split(/\s+/)
    if (parts.length === 1) {
      const q = parts[0].slice(1).toLowerCase()
      acItems = state.mcpServers
        .filter((s) => s.enabled && s.name.toLowerCase().startsWith(q))
        .map((server) => ({ kind: 'server' as const, server }))
    } else {
      // Имя сервера может быть многословным («Task Manager»): ищем самое
      // длинное имя реестра, являющееся префиксом введённого текста
      // (с границей слова); остаток — запрос по инструменту (первый токен,
      // как раньше — по parts[1])
      const typed = trimmed.slice(1)
      const server = state.mcpServers
        .filter((s) => typed === s.name || typed.startsWith(s.name + ' '))
        .sort((a, b) => b.name.length - a.name.length)[0]
      if (!server) {
        acItems = []
      } else if (server.status !== 'connected') {
        acItems = [
          { kind: 'hint' as const, text: 'Сервер не подключён — сначала подключите в настройках' },
        ]
      } else {
        const q = (typed.slice(server.name.length).trim().split(/\s+/)[0] ?? '').toLowerCase()
        acItems = state.mcpTools
          .filter((t) => t.server === server.id && t.name.toLowerCase().startsWith(q))
          .map((tool) => ({ kind: 'tool' as const, tool, serverId: server.id, serverName: server.name }))
      }
    }
  }
  const acOpen = acItems.length > 0
  // Индекс подсветки не выходит за список (фильтр мог сузить список)
  const acClamped = acItems.length === 0 ? 0 : Math.min(acIndex, acItems.length - 1)

  const selectAc = (item: AcItem) => {
    if (item.kind === 'server') {
      setDraft(`/${item.server.name} `)
    } else if (item.kind === 'tool') {
      setDraft(`/${item.serverName} ${item.tool.name}`)
      setToolSel({ serverId: item.serverId, serverName: item.serverName, tool: item.tool })
    }
    setAcIndex(0)
    setAcDismissed(false)
  }

  const canSend = !state.streaming && state.activeId != null
    && draft.trim().length > 0 && !taskBusy
  const toggleLocked = taskRunning

  const submit = () => {
    if (!canSend) return
    if (chatMode === 'task') void sendTaskMessage(draft)
    else void sendMessage(draft)
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (acOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setAcIndex((i) => Math.min(i + 1, acItems.length - 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setAcIndex((i) => Math.max(i - 1, 0))
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setAcDismissed(true)
        return
      }
      // Enter/Tab — выбор подсвеченной строки; сообщение при открытом
      // дропдауне НЕ отправляется
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const item = acItems[acClamped]
        if (item && item.kind !== 'hint') selectAc(item)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const onDraftChange = (e: { target: { value: string } }) => {
    setDraft(e.target.value)
    setAcIndex(0)
    setAcDismissed(false)
  }

  const placeholder =
    state.activeId == null
      ? 'Сначала создайте диалог (слева)'
      : taskBusy
        ? 'Задача выполняется…'
        : chatMode === 'chat'
          ? 'Сообщение… (Enter — отправить, Shift+Enter — перенос)'
          : task?.stage === 'paused'
            ? 'Инструкция для агентов… (Enter — сохранить)'
            : task?.stage === 'failed'
              ? 'Задача упала — «Повтор» в карточке'
              : task?.stage === 'plan_review'
                ? 'Ожидание одобрения плана — кнопки в карточке'
                : 'Опишите проект… (Enter — запустить пайплайн)'

  return (
    <main className="panel chat">
      <header className="chat-head">
        <h1 className="chat-title">{active ? active.title : 'Нет активного диалога'}</h1>
        <div className="chat-head-actions">
          {state.invariantViolation != null && state.invariantViolation.length > 0 && (
            <span
              className="invariant-badge"
              title="Последний ответ противоречит активным инвариантам (день 14)"
            >
              ⚠ Нарушен инвариант: {state.invariantViolation.join(', ')}
            </span>
          )}
          {activeProfile && activeProfile.status !== 'active' && (
            <button
              type="button"
              className={`profile-chip profile-badge ${
                activeProfile.status === 'declined' ? 'declined' : 'pending'
              }`}
              title="Открыть настройки на вкладке «Профили»"
              onClick={() => openSettings('profile')}
            >
              {activeProfile.status === 'declined' ? 'Профиль отключён' : 'Профиль не заполнен'}
            </button>
          )}
          <select
            className="model-select"
            title="Модель LLM"
            value={currentModel}
            disabled={state.streaming || modelOptions.length === 0}
            onChange={(e) => void setModel(e.target.value)}
          >
            {modelOptions.map((m) => (
              <option key={m.id} value={m.id}>{m.id}</option>
            ))}
          </select>
          <button
            type="button"
            className="btn-icon mcp-toggle"
            aria-label="MCP"
            title="MCP-серверы"
            onClick={() => (state.mcpOpen ? closeMcp() : openMcp())}
          >
            🧩
          </button>
          <button
            type="button"
            className="btn-icon settings-toggle"
            aria-label="Настройки"
            title="Настройки (память, профили, инварианты)"
            onClick={() => (state.settingsOpen ? closeSettings() : openSettings())}
          >
            ⚙
          </button>
        </div>
      </header>

      <div className="chat-feed" ref={feedRef}>
        {state.messages.length === 0 && <p className="chat-empty">Отправьте первое сообщение…</p>}
        {feedItems.map((item) =>
          item.kind === 'group' ? (
            <AgentSteps key={`steps-${item.key}`} messages={item.messages} />
          ) : (
            renderMsg(state.messages[item.index], item.index)
          ),
        )}
        {task && !cardAt.has(state.messages.findIndex((m) => m.task_id === task.task_id)) && (
          // Живая задача, якорь ещё не в ленте (start в процессе) — карточка хвостом
          <TaskCard task={task} live />
        )}
      </div>

      <div className="chat-input">
        <div className="mode-toggle" role="tablist" aria-label="Режим ввода">
          <button
            type="button"
            role="tab"
            aria-selected={chatMode === 'chat'}
            className={chatMode === 'chat' ? 'mode-btn active' : 'mode-btn'}
            disabled={toggleLocked}
            onClick={() => setChatMode('chat')}
          >
            Диалог
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={chatMode === 'task'}
            className={chatMode === 'task' ? 'mode-btn active' : 'mode-btn'}
            disabled={toggleLocked}
            onClick={() => setChatMode('task')}
          >
            Проект
          </button>
        </div>
        <div className="input-wrap">
          {acOpen && (
            <ul className="ac-dropdown" role="listbox" aria-label="MCP-команды">
              {acItems.map((item, i) => {
                if (item.kind === 'hint') {
                  return <li key="hint" className="ac-hint">{item.text}</li>
                }
                const active = i === acClamped
                const cls = active ? 'ac-item active' : 'ac-item'
                return item.kind === 'server' ? (
                  <li
                    key={`s-${item.server.id}`}
                    className={cls}
                    role="option"
                    aria-selected={active}
                    onMouseDown={(e) => { e.preventDefault(); selectAc(item) }}
                    onMouseEnter={() => setAcIndex(i)}
                  >
                    <span className="ac-name">{item.server.name}</span>
                    <span className={`ac-status ${item.server.status}`}>
                      {STATUS_HINT[item.server.status] ?? item.server.status}
                    </span>
                  </li>
                ) : (
                  <li
                    key={`t-${item.serverId}:${item.tool.name}`}
                    className={cls}
                    role="option"
                    aria-selected={active}
                    onMouseDown={(e) => { e.preventDefault(); selectAc(item) }}
                    onMouseEnter={() => setAcIndex(i)}
                  >
                    <span className="ac-name">{item.tool.name}</span>
                    {item.tool.description && (
                      <span className="ac-desc">{item.tool.description}</span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          <textarea
            className="input-capsule"
            rows={2}
            value={draft}
            placeholder={placeholder}
            disabled={state.streaming || state.activeId == null || taskBusy
              || (chatMode === 'task' && task?.stage === 'failed')}
            onChange={onDraftChange}
            onKeyDown={onKeyDown}
          />
        </div>
        <button type="button" className="btn send" onClick={submit} disabled={!canSend}>
          {chatMode === 'chat' ? 'Отправить' : task?.stage === 'paused' ? 'Сохранить' : 'Запустить'}
        </button>
      </div>

      {saveMsg && (
        <SaveMessageModal message={saveMsg.content} onClose={() => setSaveMsg(null)} />
      )}

      {toolSel && (
        <ToolCallModal
          key={`${toolSel.serverId}/${toolSel.tool.name}`}
          serverName={toolSel.serverName}
          tool={toolSel.tool}
          onCancel={() => setToolSel(null)}
          onInvoke={async (args) => {
            const id = state.activeId
            if (id == null) return
            await callMcpTool(toolSel.serverId, toolSel.tool.name, id, args)
            // успех — модалку закрываем; результат придёт в ленте (mcp-card)
            setToolSel(null)
          }}
        />
      )}
    </main>
  )
}
