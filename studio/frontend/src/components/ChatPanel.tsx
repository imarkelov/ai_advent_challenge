// Центральная панель: шапка (название диалога + бейдж инициализации профиля
// (день 12) + дропдаун модели), лента сообщений с авто-скроллом и
// дописыванием дельт при стриминге, инпут-капсула.
// Enter — отправить, Shift+Enter — перенос строки.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useStudio, type Message } from '../state'
import { STAGE_LABELS, STAGE_ORDER } from './TaskTab'
import type { TaskStage } from '../api'
import SaveMessageModal from './SaveMessageModal'

export default function ChatPanel() {
  const {
    state, activeProfile, sendMessage, setModel, setContextTab,
    activeTask, pauseTask, resumeTask,
  } = useStudio()
  const [draft, setDraft] = useState('')
  const [saveMsg, setSaveMsg] = useState<Message | null>(null)
  const feedRef = useRef<HTMLDivElement>(null)
  const active = state.dialogues.find((d) => d.id === state.activeId)

  // День 13: активная задача — режим задачи в чате
  const task = activeTask && activeTask.active ? activeTask : null
  const taskBusy = task != null && task.stage !== 'done' && !task.paused

  // Модели для дропдауна: список /api/models + гарантия, что текущая модель
  // из конфига всегда в списке (API недоступен → только текущая).
  const currentModel = state.config?.model ?? ''
  const currentInList = state.models.some((m) => m.id === currentModel)
  const modelOptions = currentInList
    ? state.models
    : currentModel
      ? [{ id: currentModel, context_limit: 0 }, ...state.models]
      : state.models

  // Авто-скролл вниз при новых сообщениях и дельтах стрима
  useEffect(() => {
    const el = feedRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.messages, state.streaming])

  const canSend = !state.streaming && state.activeId != null && draft.trim().length > 0 && !taskBusy

  const submit = () => {
    if (!canSend) return
    void sendMessage(draft)
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <main className="panel chat">
      <header className="chat-head">
        <h1 className="chat-title">{active ? active.title : 'Нет активного диалога'}</h1>
        <div className="chat-head-actions">
          {task && task.stage !== 'done' && (
            <>
              {taskBusy && state.taskRunning && (
                <button
                  type="button"
                  className="btn danger"
                  title="Остановить пайплайн (вступит на границе стадии)"
                  onClick={() => void pauseTask()}
                >
                  Стоп
                </button>
              )}
              {task.paused && !state.taskRunning && (
                <button
                  type="button"
                  className="btn"
                  title="Продолжить пайплайн задачи"
                  onClick={() => void resumeTask()}
                >
                  Продолжить
                </button>
              )}
            </>
          )}
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
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        </div>
      </header>

      {task && task.stage !== 'done' && (
        <div className="chat-task-strip" aria-label="Стадии задачи">
          {STAGE_ORDER.map((s) => {
            const cls = task.stages[s]?.output
              ? 'task-chip done'
              : state.taskCurrentStage === s || task.stage === s
                ? 'task-chip active'
                : 'task-chip'
            return (
              <span key={s} className={cls}>
                {STAGE_LABELS[s]}
              </span>
            )
          })}
          {task.paused && <span className="task-chip paused">на паузе</span>}
        </div>
      )}

      <div className="chat-feed" ref={feedRef}>
        {state.messages.length === 0 && <p className="chat-empty">Отправьте первое сообщение…</p>}
        {state.messages.map((m, i) => {
          const isTail = i === state.messages.length - 1
          return (
            <div key={i} className={m.role === 'user' ? 'msg user' : 'msg assistant'}>
              <div className="msg-role">
                {m.role === 'user' ? 'вы' : m.task_stage ? STAGE_LABELS[m.task_stage as TaskStage] : 'модель'}
                {m.task_stage && (
                  <span className="msg-task-chip" title="Сделано stage-агентом задачи">
                    задача
                  </span>
                )}
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
          )
        })}
      </div>

      <div className="chat-input">
        <textarea
          className="input-capsule"
          rows={2}
          value={draft}
          placeholder={
            state.activeId == null
              ? 'Сначала создайте диалог (слева)'
              : taskBusy
                ? 'Задача выполняется — чат на паузе (кнопка «Стоп» в шапке)'
                : 'Сообщение… (Enter — отправить, Shift+Enter — перенос)'
          }
          disabled={state.streaming || state.activeId == null || taskBusy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="btn send" onClick={submit} disabled={!canSend}>
          Отправить
        </button>
      </div>

      {saveMsg && (
        <SaveMessageModal message={saveMsg.content} onClose={() => setSaveMsg(null)} />
      )}
    </main>
  )
}
