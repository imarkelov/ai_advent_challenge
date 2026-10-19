import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PipelineTab from '../src/components/PipelineTab'
import { initialState } from '../src/state'

const base = {
  ...initialState(),
}

describe('PipelineTab', () => {
  it('renders all stages', () => {
    render(<PipelineTab state={base} dispatch={vi.fn()} />)
    for (const stage of ['sdd', 'study', 'skeleton', 'write', 'provision', 'execute', 'debug', 'review', 'done']) {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0)
    }
  })

  it('shows agent rows with models', () => {
    const state = {
      ...base,
      agents: [
        { role: 'testwriter', model: 'qwen3-coder:30b', status: 'done' as const, text: '', summary: '' },
        { role: 'reviewer', model: 'gemma4:12b', status: 'running' as const, text: '', summary: '' },
      ],
    }
    render(<PipelineTab state={state} dispatch={vi.fn()} />)
    expect(screen.getByText('qwen3-coder:30b')).toBeInTheDocument()
    expect(screen.getByText('gemma4:12b')).toBeInTheDocument()
  })

  it('shows skeleton gate actions when ready', () => {
    render(<PipelineTab state={{ ...base, skeletonReady: true, runId: 'r1' }} dispatch={vi.fn()} />)
    expect(screen.getByText('Утвердить каркас')).toBeInTheDocument()
    expect(screen.getByText('Исполнить загруженный (as_loaded)')).toBeInTheDocument()
    expect(screen.getByText('Исполнить сгенерированный (generated)')).toBeInTheDocument()
  })

  it('approve button does not choose an execution mode', async () => {
    const onChoose = vi.fn()
    const onApprove = vi.fn()
    render(<PipelineTab state={{ ...base, skeletonReady: true, runId: 'r1' }} dispatch={vi.fn()}
      onApprove={onApprove} onChooseExecution={onChoose} />)
    fireEvent.click(screen.getByText('Утвердить каркас'))
    expect(onApprove).toHaveBeenCalledTimes(1)
    expect(onChoose).not.toHaveBeenCalled()
  })

  it('does not show gates without a skeleton', () => {
    render(<PipelineTab state={base} dispatch={vi.fn()} />)
    expect(screen.queryByText('Утвердить каркас')).toBeNull()
  })
})
