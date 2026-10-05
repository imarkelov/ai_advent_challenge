// Вкладка «Память задачи» (день 25): task_state активного диалога —
// цель диалога (goal), уточнения пользователя (clarifications),
// зафиксированные ограничения/термины (constraints). Загружается при
// открытии вкладки и при смене диалога (GET /api/dialogues/{id}/task-state);
// редактируется вручную и сохраняется ЦЕЛЫМ состоянием
// (POST /api/dialogues/{id}/task-state). Бэкенд также обновляет память
// задачи автоматически после каждого хода чата (non-stream LLM-вызов) —
// панель перечитывается после done (chat-done перечитывает диалоги).
import { useCallback, useEffect, useState } from 'react'
import { apiGetTaskState, apiSetTaskState, type TaskMemoryState } from '../api'
import { useStudio } from '../state'

const EMPTY: TaskMemoryState = { clarifications: [], constraints: [], goal: '' }

export default function TaskStateTab() {
  const { state } = useStudio()
  const dialogueId = state.activeId
  const [ts, setTs] = useState<TaskMemoryState>(EMPTY)
  const [goal, setGoal] = useState('')
  const [clarText, setClarText] = useState('')
  const [consText, setConsText] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')

  const load = useCallback(() => {
    if (!dialogueId) return
    void (async () => {
      setLoading(true)
      try {
        const { task_state } = await apiGetTaskState(dialogueId)
        setTs(task_state)
        setGoal(task_state.goal ?? '')
        setClarText((task_state.clarifications ?? []).join('\n'))
        setConsText((task_state.constraints ?? []).join('\n'))
        setHint('')
      } catch (err) {
        console.error('task-state load:', err)
        setHint('Не удалось загрузить память задачи')
      } finally {
        setLoading(false)
      }
    })()
  }, [dialogueId])

  // Загрузка при смене диалога/открытии вкладки (ключ — активный диалог).
  useEffect(() => {
    load()
  }, [load])

  if (!dialogueId) {
    return (
      <div className="ctx-sections">
        <div className="kv-empty">Нет активного диалога — создайте новый.</div>
      </div>
    )
  }

  const save = () => {
    const next: TaskMemoryState = {
      goal: goal.trim(),
      clarifications: clarText.split('\n').map((s) => s.trim()).filter(Boolean),
      constraints: consText.split('\n').map((s) => s.trim()).filter(Boolean),
    }
    void (async () => {
      setBusy(true)
      try {
        const { task_state } = await apiSetTaskState(dialogueId, next)
        setTs(task_state)
        setGoal(task_state.goal)
        setClarText(task_state.clarifications.join('\n'))
        setConsText(task_state.constraints.join('\n'))
        setHint('Сохранено')
      } catch (err) {
        console.error('task-state save:', err)
        setHint('Ошибка сохранения: ' + (err instanceof Error ? err.message : String(err)))
      } finally {
        setBusy(false)
      }
    })()
  }

  const reset = () => {
    setGoal('')
    setClarText('')
    setConsText('')
  }

  return (
    <div className="ctx-sections">
      <section className="ctx-card" title="Память задачи (день 25)">
        <header className="ctx-card-head">
          <h3>Память задачи</h3>
          {ts.goal ? <span className="task-state-chip">цель зафиксирована</span> : <span className="task-state-chip empty">не заполнена</span>}
        </header>
        <label className="profile-field">
          <span className="ts-label">Цель диалога</span>
          <textarea
            className="input ts-textarea"
            rows={2}
            placeholder="чему диалог должен в конечном счёте привести"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
          />
        </label>
        <label className="profile-field">
          <span className="ts-label">Уточнения (по одному в строке)</span>
          <textarea
            className="input ts-textarea"
            rows={4}
            placeholder={'какой язык\nсколько страниц'}
            value={clarText}
            onChange={(e) => setClarText(e.target.value)}
          />
        </label>
        <label className="profile-field">
          <span className="ts-label">Ограничения и термины (по одному в строке)</span>
          <textarea
            className="input ts-textarea"
            rows={4}
            placeholder={'без Python\nтермин «сборка» = build'}
            value={consText}
            onChange={(e) => setConsText(e.target.value)}
          />
        </label>
        <div className="ctx-toolbar">
          <button type="button" className="btn" disabled={busy} onClick={save}>Сохранить</button>
          <button type="button" className="btn" disabled={busy || loading} onClick={load}>Обновить</button>
          <button type="button" className="btn btn-clear" disabled={busy} onClick={reset}>Очистить</button>
        </div>
        {hint && <p className="profile-hint">{hint}</p>}
        <p className="ts-note">
          Память задачи обновляется автоматически после каждого ответа
          ассистента (LLM-вызов) и инжектится в system-промпт: ассистент
          не теряет цель диалога на длинных сценариях.
        </p>
      </section>
    </div>
  )
}
