// Вкладка «Задача» (день 13) правой панели «Контекст»: состояние задачи
// (FSM per-диалог) — описание, прогресс стадий, выводы stage-агентов с
// вердиктами, Стоп/Продолжить, инструкция на паузе, сброс.
import { useState } from 'react'
import { useStudio } from '../state'
import type { TaskStage } from '../api'

// Подписи стадий FSM (RU) — общий словарь для TaskTab и ChatPanel
export const STAGE_LABELS: Record<TaskStage, string> = {
  planning: 'Планирование',
  execution: 'Исполнение',
  validation: 'Валидация',
  done: 'Завершение',
}

export const STAGE_ORDER: TaskStage[] = ['planning', 'execution', 'validation', 'done']

export default function TaskTab() {
  const {
    state, activeTask, startTask, runTask,
    pauseTask, resumeTask, sendTaskInstruction, resetTask,
  } = useStudio()
  const [desc, setDesc] = useState('')
  const [instruction, setInstruction] = useState('')

  if (state.activeId == null) {
    return <p className="tab-hint">Сначала создайте диалог (слева)</p>
  }

  // Задача неактивна (или отсутствует) — форма запуска
  if (!activeTask || !activeTask.active) {
    return (
      <div className="task-tab">
        <label className="task-label" htmlFor="task-desc">Описание задачи</label>
        <textarea
          id="task-desc"
          className="task-desc"
          rows={4}
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="Что сделать? (например: «Создать калькулятор на Python»)"
        />
        <button
          type="button"
          className="btn"
          disabled={!desc.trim()}
          onClick={() => {
            void (async () => {
              await startTask(desc)
              setDesc('')
              await runTask()
            })()
          }}
        >
          Запустить задачу
        </button>
      </div>
    )
  }

  const task = activeTask
  const isDone = task.stage === 'done'
  const isPaused = task.paused
  const isRunning = state.taskRunning && !isPaused

  const stageClass = (s: TaskStage): string => {
    if (task.stages[s]?.output) return 'task-chip done'
    if (state.taskCurrentStage === s || (!isDone && task.stage === s)) {
      return 'task-chip active'
    }
    return 'task-chip'
  }

  return (
    <div className="task-tab">
      <div>
        <span className="task-label">Задача</span>
        <p className="task-desc-text">{task.description}</p>
      </div>

      <div className="task-strip" aria-label="Стадии задачи">
        {STAGE_ORDER.map((s) => (
          <span key={s} className={stageClass(s)}>
            {STAGE_LABELS[s]}
          </span>
        ))}
        {isPaused && <span className="task-chip paused">на паузе</span>}
      </div>

      <div className="task-actions">
        {isRunning && (
          <button type="button" className="btn danger" onClick={() => void pauseTask()}>
            Стоп
          </button>
        )}
        {isPaused && !isDone && (
          <button type="button" className="btn" onClick={() => void resumeTask()}>
            Продолжить
          </button>
        )}
        {isDone && (
          <button type="button" className="btn" onClick={() => void resetTask()}>
            Новая задача
          </button>
        )}
      </div>

      {task.error && <p className="task-error">{task.error}</p>}

      {isPaused && (
        <div className="task-instruction">
          <label className="task-label" htmlFor="task-instr">
            Инструкция (учтётся при продолжении)
          </label>
          <div className="task-instruction-row">
            <input
              id="task-instr"
              className="task-input"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="Например: используй Kotlin"
            />
            <button
              type="button"
              className="btn"
              disabled={!instruction.trim()}
              onClick={() => {
                void (async () => {
                  await sendTaskInstruction(instruction)
                  setInstruction('')
                })()
              }}
            >
              Сохранить
            </button>
          </div>
          {task.instruction && (
            <p className="task-instruction-current">Сохранено: {task.instruction}</p>
          )}
        </div>
      )}

      {STAGE_ORDER.filter((s) => task.stages[s]).map((s) => {
        const e = task.stages[s]
        return (
          <details key={s} className="task-output">
            <summary>
              {STAGE_LABELS[s]}
              {e.attempts != null && e.attempts > 1 && (
                <span className="task-attempts"> (попыток: {e.attempts})</span>
              )}
              {e.verdict && (
                <span className={'task-verdict ' + e.verdict}>
                  {e.verdict === 'pass' ? 'прошла' : 'не прошла'}
                </span>
              )}
            </summary>
            <pre className="task-output-text">{e.output}</pre>
          </details>
        )
      })}
    </div>
  )
}
