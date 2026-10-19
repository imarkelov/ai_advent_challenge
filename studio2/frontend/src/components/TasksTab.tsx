import { useEffect, useState } from 'react'
import { getTasks, type TaskCase } from '../api'

export default function TasksTab({ onSelect }: { onSelect: (task: TaskCase) => void }) {
  const [tasks, setTasks] = useState<TaskCase[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    getTasks()
      .then((result) => setTasks(result.tasks))
      .catch((exc: Error) => setError(exc.message))
  }, [])

  return (
    <div>
      <h2>Тест-кейсы</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead><tr><th>ID</th><th>Название</th><th>Экран</th><th>Приоритет</th><th>Статус</th><th /></tr></thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.id}>
              <td>{task.id}</td>
              <td>{task.title}</td>
              <td>{task.screen ?? '—'}</td>
              <td>{task.priority ?? '—'}</td>
              <td>{task.status}</td>
              <td><button onClick={() => onSelect(task)}>Выбрать</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
