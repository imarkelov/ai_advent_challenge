import { useReducer, useState } from 'react'
import {
  approveSkeleton, cancelRun, chooseExecution, createRun, createRunStream, resumeRun, startRun,
  answerQuestion, type RunInfo,
} from './api'
import Header from './components/Header'
import Inspector from './components/Inspector'
import McpTab from './components/McpTab'
import ModelsTab from './components/ModelsTab'
import PipelineTab from './components/PipelineTab'
import QuestionModal from './components/QuestionModal'
import RagTab from './components/RagTab'
import RunsTab from './components/RunsTab'
import Sidebar, { type TabId } from './components/Sidebar'
import SkeletonEditor from './components/SkeletonEditor'
import TasksTab from './components/TasksTab'
import { initialState, pipelineReducer } from './state'

const DEFAULT_REPO = 'C:\\Users\\migor\\StudioProjects\\lmru--mobile-customer--app'

export default function App() {
  const [tab, setTab] = useState<TabId>('pipeline')
  const [state, dispatch] = useReducer(pipelineReducer, undefined, initialState)
  const [taskId, setTaskId] = useState('')
  const [branch, setBranch] = useState('develop')
  const [stream, setStream] = useState<EventSource | null>(null)

  const follow = (runId: string) => {
    stream?.close()
    setStream(createRunStream(runId, (event) => dispatch({ type: 'event', payload: event })))
  }

  const onStart = async () => {
    try {
      const { run } = await createRun(taskId, DEFAULT_REPO, branch)
      follow(run.id)
      await startRun(run.id)
    } catch (exc) {
      dispatch({ type: 'error', message: (exc as Error).message })
    }
  }

  const onApprove = async () => {
    if (!state.runId) return
    try {
      await approveSkeleton(state.runId)
      await chooseExecution(state.runId, 'generated')
      await resumeRun(state.runId)
    } catch (exc) {
      dispatch({ type: 'error', message: (exc as Error).message })
    }
  }

  const onChoose = async (choice: 'as_loaded' | 'generated') => {
    if (!state.runId) return
    try {
      await chooseExecution(state.runId, choice)
    } catch (exc) {
      dispatch({ type: 'error', message: (exc as Error).message })
    }
  }

  const openRun = (run: RunInfo) => {
    setTaskId(run.task_id)
    setBranch(run.branch)
    dispatch({ type: 'reset' })
    follow(run.id)
  }

  return (
    <div className="app">
      <Header task={taskId} stage={state.stage} running={state.status === 'running'} onStart={onStart} />
      <div className="columns">
        <Sidebar active={tab} onChange={setTab} />
        <main className="main">
          <div className="panel row">
            <span className="hint">ТК</span>
            <input value={taskId} onChange={(event) => setTaskId(event.target.value)} placeholder="TC-4217" />
            <span className="hint">ветка</span>
            <input value={branch} onChange={(event) => setBranch(event.target.value)} placeholder="develop" />
            {state.runId && <button onClick={() => cancelRun(state.runId!)}>Отменить</button>}
          </div>
          {tab === 'models' && <ModelsTab />}
          {tab === 'rag' && <RagTab />}
          {tab === 'mcp' && <McpTab />}
          {tab === 'tasks' && <TasksTab onSelect={(task) => { setTaskId(task.id); setTab('pipeline') }} />}
          {tab === 'runs' && <RunsTab onOpen={openRun} />}
          {tab === 'pipeline' && (
            <>
              <PipelineTab state={state} dispatch={dispatch} onApprove={onApprove} onChooseExecution={onChoose} />
              {state.runId && state.skeletonReady && (
                <SkeletonEditor runId={state.runId} onApproved={onApprove} />
              )}
            </>
          )}
        </main>
        <Inspector state={state} />
      </div>
      {state.question && state.runId && (
        <QuestionModal
          question={state.question}
          onSubmit={(text) => {
            void answerQuestion(state.runId as string, state.question!.id, text)
            dispatch({ type: 'event', payload: { type: 'question_answered', question_id: state.question!.id } })
          }}
        />
      )}
    </div>
  )
}
