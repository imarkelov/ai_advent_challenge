import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ModelsTab from '../src/components/ModelsTab'

const CONFIG = {
  providers: {
    ollama: { base_url: 'http://localhost:11435', api_key_masked: '' },
    gpustack: { base_url: 'https://gpustack/v1', api_key_masked: 'gpus***7890' },
    fake: { base_url: null, api_key_masked: '' },
  },
  roles: {
    'qa-lead': { provider: 'ollama', model: 'qwen3-coder:30b', temperature: 0.2 },
    testwriter: { provider: 'ollama', model: 'qwen3-coder:30b', temperature: 0.4 },
    reviewer: { provider: 'ollama', model: 'gemma4:12b', temperature: 0.1 },
  },
  critic: { must_differ_from_author: true },
  android: {},
}

describe('ModelsTab', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders a row per role with provider and model', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(CONFIG), { status: 200 })))
    render(<ModelsTab />)
    await waitFor(() => expect(screen.getByText('reviewer')).toBeInTheDocument())
    expect(screen.getByDisplayValue('gemma4:12b')).toBeInTheDocument()
    expect(screen.getByText('qa-lead')).toBeInTheDocument()
  })

  it('masks provider keys', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(CONFIG), { status: 200 })))
    render(<ModelsTab />)
    await waitFor(() => expect(screen.getByText(/gpus\*\*\*7890/)).toBeInTheDocument())
  })

  it('shows an error when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'нет связи с сервером' }), { status: 400 })))
    render(<ModelsTab />)
    await waitFor(() => expect(screen.getByText(/нет связи с сервером/)).toBeInTheDocument())
  })
})
