import { useEffect, useState } from 'react'
import { listRuns, type RunInfo } from '../api'

export default function RunsTab({ onOpen }: { onOpen: (run: RunInfo) => void }) {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    listRuns()
      .then((result) => setRuns(result.runs))
      .catch((exc: Error) => setError(exc.message))
  }, [])

  return (
    <div>
      <h2>История запусков</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead><tr><th>Run</th><th>ТК</th><th>Ветка</th><th>Вердикт</th><th>Статус</th><th /></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>{run.id}</td>
              <td>{run.task_id}</td>
              <td>{run.branch}</td>
              <td>{run.verdict ?? '—'}</td>
              <td>{run.status}</td>
              <td><button onClick={() => onOpen(run)}>Открыть</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
