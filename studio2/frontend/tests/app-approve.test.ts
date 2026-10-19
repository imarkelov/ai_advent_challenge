import { afterEach, describe, expect, it, vi } from 'vitest'

const approveSkeleton = vi.fn(async () => ({ run: {} }))
const chooseExecution = vi.fn(async () => ({}))
const resumeRun = vi.fn(async () => ({}))

vi.mock('../src/api', () => ({
  approveSkeleton,
  chooseExecution,
  resumeRun,
}))

describe('approveOnly', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('approves the skeleton without choosing an execution mode', async () => {
    const { approveOnly } = await import('../src/App')
    await approveOnly('r1')
    expect(approveSkeleton).toHaveBeenCalledWith('r1')
    expect(chooseExecution).not.toHaveBeenCalled()
    expect(resumeRun).not.toHaveBeenCalled()
  })

  it('does nothing without a run', async () => {
    const { approveOnly } = await import('../src/App')
    expect(approveOnly(null)).toBeNull()
    expect(approveSkeleton).not.toHaveBeenCalled()
  })
})
