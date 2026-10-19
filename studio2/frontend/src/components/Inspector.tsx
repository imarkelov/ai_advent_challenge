import type { PipelineState } from '../state'

export default function Inspector({ state }: { state: PipelineState }) {
  const active = state.agents.find((agent) => agent.status === 'running') ?? state.agents.at(-1)

  return (
    <aside className="inspector">
      <h2>Инспектор</h2>
      {state.verdict && (
        <div className="panel">
          <h2>Вердикт</h2>
          <span className={`dot ${state.verdict === 'green' ? 'done' : 'failed'}`} /> {state.verdict}
        </div>
      )}
      {state.reportPath && (
        <div className="panel">
          <h2>Отчёт</h2>
          <span className="hint">{state.reportPath}</span>
        </div>
      )}
      <div className="panel">
        <h2>Текущий агент</h2>
        {active ? (
          <>
            <div className="row"><span className={`dot ${active.status}`} /> {active.role}</div>
            <div className="hint">{active.model}</div>
            <pre className="journal">{active.text.slice(-1200)}</pre>
          </>
        ) : <span className="hint">агент не запущен</span>}
      </div>
    </aside>
  )
}
