// Центральная панель: шапка (название + бейдж профиля + дропдаун модели),
// лента сообщений (включая карточки процесса задачи, день 13b),
// тумблер режимов чат/задача и инпут-капсула.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useStudio, type Message } from '../state'
import TaskCard, { taskFromMarkers } from './TaskCard'
import SaveMessageModal from './SaveMessageModal'

export default function ChatPanel() {
  const {
    state, activeProfile, sendMessage, setModel, setContextTab,
    activeTask, chatMode, setChatMode, sendTaskMessage,
  } = useStudio()
  const [draft, setDraft] = useState('')
  const [saveMsg, setSaveMsg] = useState<Message | null>(null)
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
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
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
              : 'Опишите задачу… (Enter — запустить пайплайн)'

  return (
    <main className="panel chat">
      <header className="chat-head">
        <h1 className="chat-title">{active ? active.title : 'Нет активного диалога'}</h1>
        <div className="chat-head-actions">
          {activeProfile && activeProfile.status !== 'active' && (
            <button
              type="button"
              className={`profile-chip profile-badge ${
                activeProfile.status === 'declined' ? 'declined' : 'pending'
              }`}
              title="Открыть вкладку «Профили»"
              onClick={() => setContextTab('profile')}
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
        </div>
      </header>

      <div className="chat-feed" ref={feedRef}>
        {state.messages.length === 0 && <p className="chat-empty">Отправьте первое сообщение…</p>}
        {state.messages.map((m, i) => {
          const isTail = i === state.messages.length - 1
          // Stage/work-сообщения с task_stage рендерятся внутри карточки
          if (m.task_stage) return null
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
        })}
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
            Чат
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={chatMode === 'task'}
            className={chatMode === 'task' ? 'mode-btn active' : 'mode-btn'}
            disabled={toggleLocked}
            onClick={() => setChatMode('task')}
          >
            Задача
          </button>
        </div>
        <textarea
          className="input-capsule"
          rows={2}
          value={draft}
          placeholder={placeholder}
          disabled={state.streaming || state.activeId == null || taskBusy
            || (chatMode === 'task' && task?.stage === 'failed')}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="btn send" onClick={submit} disabled={!canSend}>
          {chatMode === 'chat' ? 'Отправить' : task?.stage === 'paused' ? 'Сохранить' : 'Запустить'}
        </button>
      </div>

      {saveMsg && (
        <SaveMessageModal message={saveMsg.content} onClose={() => setSaveMsg(null)} />
      )}
    </main>
  )
}
