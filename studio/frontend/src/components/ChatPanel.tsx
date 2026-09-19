// Центральная панель: шапка (название диалога + бейдж модели), лента сообщений
// с авто-скроллом и дописыванием дельт при стриминге, инпут-капсула.
// Enter — отправить, Shift+Enter — перенос строки.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useStudio } from '../state'

export default function ChatPanel() {
  const { state, sendMessage } = useStudio()
  const [draft, setDraft] = useState('')
  const feedRef = useRef<HTMLDivElement>(null)
  const active = state.dialogues.find((d) => d.id === state.activeId)

  // Авто-скролл вниз при новых сообщениях и дельтах стрима
  useEffect(() => {
    const el = feedRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.messages, state.streaming])

  const canSend = !state.streaming && state.activeId != null && draft.trim().length > 0

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
        <span className="model-badge">{state.config?.model ?? '—'}</span>
      </header>

      <div className="chat-feed" ref={feedRef}>
        {state.messages.length === 0 && <p className="chat-empty">Отправьте первое сообщение…</p>}
        {state.messages.map((m, i) => {
          const isTail = i === state.messages.length - 1
          return (
            <div key={i} className={m.role === 'user' ? 'msg user' : 'msg assistant'}>
              <div className="msg-role">{m.role === 'user' ? 'вы' : 'модель'}</div>
              <div className="msg-text">
                {m.content}
                {state.streaming && isTail && m.role === 'assistant' && (
                  <span className="caret" aria-hidden />
                )}
              </div>
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
              : 'Сообщение… (Enter — отправить, Shift+Enter — перенос)'
          }
          disabled={state.streaming || state.activeId == null}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="btn send" onClick={submit} disabled={!canSend}>
          Отправить
        </button>
      </div>
    </main>
  )
}
