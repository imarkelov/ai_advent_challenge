import { describe, expect, it } from 'vitest'
import { initialState, pipelineReducer, type PipelineState } from '../src/state'

function withState(patch: Partial<PipelineState>): PipelineState {
  return { ...initialState(), ...patch }
}

describe('pipelineReducer', () => {
  it('starts at sdd with empty artifacts', () => {
    const state = initialState()
    expect(state.stage).toBe('idle')
    expect(state.agents).toEqual([])
    expect(state.journal).toEqual([])
  })

  it('run_started sets stage sdd and clears journal', () => {
    const state = pipelineReducer(withState({ journal: [{ at: 'x', role: 'r', text: 't' }] }), {
      type: 'event',
      payload: { type: 'run_started', run_id: 'r1', task: 'TC-4217' },
    })
    expect(state.stage).toBe('sdd')
    expect(state.runId).toBe('r1')
    expect(state.journal).toEqual([])
  })

  it('stage_changed updates stage and round', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'stage_changed', stage: 'study', round: 1 },
    })
    expect(state.stage).toBe('study')
    expect(state.round).toBe(1)
  })

  it('agent_started adds agent with running status', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'agent_started', role: 'researcher', model: 'qwen3.5:9b' },
    })
    expect(state.agents[0]).toMatchObject({ role: 'researcher', model: 'qwen3.5:9b', status: 'running' })
  })

  it('agent_delta appends text and a journal entry', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'agent_delta', role: 'researcher', text: 'изучаю' },
    })
    expect(state.agents[0].text).toBe('изучаю')
    expect(state.journal.at(-1)?.role).toBe('researcher')
  })

  it('agent_done marks status', () => {
    const started = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'agent_started', role: 'reviewer', model: 'gemma4:12b' },
    })
    const done = pipelineReducer(started, {
      type: 'event',
      payload: { type: 'agent_done', role: 'reviewer', status: 'done', summary: 'green' },
    })
    expect(done.agents[0].status).toBe('done')
  })

  it('sdd_written records sdd files', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'sdd_written', role: 'testwriter', path: 'sdd/agent_testwriter.md', title: 'testwriter' },
    })
    expect(state.sddFiles).toEqual([{ role: 'testwriter', path: 'sdd/agent_testwriter.md' }])
  })

  it('skeleton_ready sets skeleton path', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'skeleton_ready', path: 'skeleton.md' },
    })
    expect(state.skeletonReady).toBe(true)
  })

  it('question stores pending question', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'question', question_id: 'q1', role: 'researcher', text: 'Какой экран?', options: ['A'] },
    })
    expect(state.question).toMatchObject({ id: 'q1', text: 'Какой экран?' })
  })

  it('question_answered clears question', () => {
    const asked = withState({ question: { id: 'q1', role: 'r', text: 't', options: [] } })
    const state = pipelineReducer(asked, {
      type: 'event',
      payload: { type: 'question_answered', question_id: 'q1' },
    })
    expect(state.question).toBeNull()
  })

  it('review_verdict stores verdict', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'review_verdict', verdict: 'green', path: 'review_report.md' },
    })
    expect(state.verdict).toBe('green')
  })

  it('run_done sets finished status', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'run_done', status: 'done', report_path: 'test_report.md' },
    })
    expect(state.status).toBe('done')
    expect(state.reportPath).toBe('test_report.md')
  })

  it('error sets message', () => {
    const state = pipelineReducer(initialState(), { type: 'error', message: 'нет связи' })
    expect(state.error).toBe('нет связи')
  })

  it('reset returns to initial state', () => {
    const dirty = withState({ stage: 'write', error: 'x' })
    expect(pipelineReducer(dirty, { type: 'reset' })).toEqual(initialState())
  })
})
