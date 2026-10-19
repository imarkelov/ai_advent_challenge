import { useEffect, useState } from 'react'
import { getSkeleton, saveSkeleton } from '../api'

export default function SkeletonEditor({
  runId,
  onApproved,
}: {
  runId: string
  onApproved: () => void
}) {
  const [content, setContent] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  useEffect(() => {
    getSkeleton(runId)
      .then((result) => setContent(result.content))
      .catch((exc: Error) => setError(exc.message))
  }, [runId])

  const save = async () => {
    try {
      await saveSkeleton(runId, content)
      setStatus('каркас сохранён')
      setError('')
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  return (
    <div className="panel">
      <h2>Каркас теста</h2>
      {error && <div className="notice err">{error}</div>}
      {status && <div className="notice">{status}</div>}
      <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={18} />
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={save}>Сохранить каркас</button>
        <button onClick={onApproved}>Утвердить и реализовать</button>
      </div>
    </div>
  )
}
