import type { Dispatch } from 'react'
import type { Action, PipelineState } from '../state'

const STAGES = ['sdd', 'study', 'skeleton', 'write', 'provision', 'execute', 'debug', 'review', 'done']

export default function PipelineTab({
  state,
  dispatch,
  onApprove,
  onChooseExecution,
}: {
  state: PipelineState
  dispatch: Dispatch<Action>
  onApprove?: () => void
  onChooseExecution?: (choice: 'as_loaded' | 'generated') => void
}) {
  const currentIndex = STAGES.indexOf(state.stage)

  return (
    <div>
      {state.error && <div className="notice err">{state.error}</div>}
      <h2>SDD</h2>
      <div className="panel">
        {state.sddFiles.length === 0
          ? <span className="hint">SDD пока не сформированы</span>
          : state.sddFiles.map((file) => (
            <div key={file.path} className="journal"><span>{file.path}</span></div>
          ))}
      </div>

      <h2>Стадии</h2>
      <div className="stages">
        {STAGES.map((stage, index) => (
          <span key={stage}
                className={`stage ${stage === state.stage ? 'current' : ''} ${index < currentIndex ? 'passed' : ''}`}>
            {stage}
          </span>
        ))}
      </div>

      {state.skeletonReady && (
        <div className="panel">
          <h2>Гейты</h2>
          <div className="row">
            <button onClick={onApprove}>Утвердить каркас</button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={() => onChooseExecution?.('as_loaded')}>Исполнить загруженный (as_loaded)</button>
            <button onClick={() => onChooseExecution?.('generated')}>Исполнить сгенерированный (generated)</button>
            {state.executionChosen && <span className="hint">выбрано: {state.executionChosen}</span>}
          </div>
        </div>
      )}

      <h2>Протокол</h2>
      <table>
        <thead><tr><th>Роль</th><th>Модель</th><th>Статус</th><th>Итог</th></tr></thead>
        <tbody>
          {state.agents.map((agent) => (
            <tr key={agent.role}>
              <td><span className={`dot ${agent.status}`} /> {agent.role}</td>
              <td>{agent.model}</td>
              <td>{agent.status}</td>
              <td>{agent.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ marginTop: 16 }}>Журнал</h2>
      <div className="journal">
        {state.journal.slice(-80).map((row, index) => (
          <div key={index}>{row.at} [{row.role}] {row.text}</div>
        ))}
      </div>
    </div>
  )
}
