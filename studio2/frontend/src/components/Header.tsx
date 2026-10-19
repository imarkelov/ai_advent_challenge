export default function Header({
  task,
  stage,
  running,
  onStart,
}: {
  task: string
  stage: string
  running: boolean
  onStart: () => void
}) {
  return (
    <header className="header">
      <span className="brand">QA-LEAD</span>
      <span className="hint">{task || 'задача не выбрана'}</span>
      <span className="hint">стадия: {stage}</span>
      <span className="spacer" />
      <button onClick={onStart} disabled={running}>
        {running ? 'Выполняется…' : '▶ Запустить'}
      </button>
    </header>
  )
}
