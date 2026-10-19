const BASE = 'http://127.0.0.1:8100/api'

export function apiUrl(path: string): string {
  return `${BASE}${path}`
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `ошибка ${response.status}`
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* тело без json */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function apiGet<T>(path: string): Promise<T> {
  return handle<T>(await fetch(apiUrl(path)))
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(apiUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    }),
  )
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(apiUrl(path), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    }),
  )
}

export async function apiDelete<T>(path: string): Promise<T> {
  return handle<T>(await fetch(apiUrl(path), { method: 'DELETE' }))
}

export interface RoleConfig { provider: string; model: string; temperature: number }
export interface ProviderConfig { base_url: string | null; api_key_masked: string }
export interface StudioConfig {
  providers: Record<string, ProviderConfig>
  roles: Record<string, RoleConfig>
  critic: { must_differ_from_author: boolean }
  android: Record<string, string>
}

export const getHealth = () => apiGet<{ status: string; version: string }>('/health')
export const getConfig = () => apiGet<StudioConfig>('/config')
export const saveConfig = (patch: unknown) => apiPost<StudioConfig>('/config', patch)
export const getModels = (provider: string) =>
  apiGet<{ provider: string; models: string[] }>(`/models?provider=${provider}`)
export const testModel = (role: string) =>
  apiPost<{ ok: boolean; provider: string; model: string; error?: string }>('/models/test', { role })

export interface DocNode { name: string; type: 'dir' | 'file'; children: string[] }
export const getDocTree = () => apiGet<{ tree: DocNode[] }>('/doc/tree')
export const readDoc = (path: string) =>
  apiGet<{ path: string; content: string }>(`/doc/file?path=${encodeURIComponent(path)}`)
export const writeDoc = (path: string, content: string) =>
  apiPost<{ path: string; saved: boolean }>('/doc/file', { path, content })
export const deleteDoc = (path: string) =>
  apiDelete<{ deleted: string }>(`/doc/file?path=${encodeURIComponent(path)}`)
export async function uploadDoc(dir: string, file: File): Promise<{ path: string }> {
  const form = new FormData()
  form.append('dir', dir)
  form.append('file', file)
  return handle<{ path: string }>(await fetch(apiUrl('/doc/upload'), { method: 'POST', body: form }))
}
export const reindex = () =>
  apiPost<{ documents: number; stats: { documents: number; updated_at: string } }>('/rag/reindex')
export const searchRag = (q: string, limit = 5) =>
  apiGet<{ hits: { path: string; score: number; snippet: string }[] }>(
    `/rag/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  )

export interface TaskCase { id: string; title: string; screen?: string; priority?: string; status: string }
export const getTasks = () => apiGet<{ tasks: TaskCase[] }>('/tasks')

export interface McpServer {
  id: string
  name: string
  type: 'stdio' | 'http'
  command: string[]
  url: string
  status: { state: string; tools_count: number }
}
export const getMcpServers = () => apiGet<{ servers: McpServer[] }>('/mcp/servers')
export const addMcpServer = (spec: unknown) => apiPost<{ server: McpServer }>('/mcp/servers', spec)
export const deleteMcpServer = (id: string) => apiDelete(`/mcp/servers/${id}`)
export const connectMcpServer = (id: string) => apiPost(`/mcp/servers/${id}/connect`)
export const disconnectMcpServer = (id: string) => apiPost(`/mcp/servers/${id}/disconnect`)
export const getMcpTools = () =>
  apiGet<{ tools: { server_id: string; name: string; description: string }[] }>('/mcp/tools')

export interface RunInfo {
  id: string
  task_id: string
  repo_path: string
  branch: string
  stage: string
  status: string
  round: number
  verdict: string | null
  skeleton_approved: boolean
  execution_choice: string | null
}
export const createRun = (task_id: string, repo_path: string, branch: string) =>
  apiPost<{ run: RunInfo }>('/runs', { task_id, repo_path, branch })
export const listRuns = () => apiGet<{ runs: RunInfo[] }>('/runs')
export const getRun = (id: string) => apiGet<{ run: RunInfo }>(`/runs/${id}`)
export const startRun = (id: string) => apiPost<{ run: RunInfo }>(`/runs/${id}/start`)
export const resumeRun = (id: string) => apiPost<{ run: RunInfo }>(`/runs/${id}/resume`)
export const getSkeleton = (id: string) =>
  apiGet<{ path: string; content: string }>(`/runs/${id}/skeleton`)
export const saveSkeleton = (id: string, content: string) =>
  apiPut<{ path: string; saved: boolean }>(`/runs/${id}/skeleton`, { content })
export const approveSkeleton = (id: string) => apiPost(`/runs/${id}/approve-skeleton`)
export const chooseExecution = (id: string, choice: 'as_loaded' | 'generated') =>
  apiPost(`/runs/${id}/execution-choice`, { choice })
export const cancelRun = (id: string) => apiPost(`/runs/${id}/cancel`)
export const answerQuestion = (id: string, question_id: string, text: string) =>
  apiPost(`/runs/${id}/answer`, { question_id, text })

export interface RunEvent {
  type: string
  [key: string]: unknown
}

export function createRunStream(runId: string, onEvent: (event: RunEvent) => void): EventSource {
  const source = new EventSource(apiUrl(`/runs/${runId}/stream`))
  source.onmessage = (message) => {
    if (!message.data || message.data === '[DONE]') return
    let event: RunEvent
    try {
      event = JSON.parse(message.data) as RunEvent
    } catch {
      return
    }
    if (!event || typeof event.type !== 'string') return
    onEvent(event)
  }
  source.onerror = () => {
    source.close()
    onEvent({ type: 'error', message: 'поток прерван' })
  }
  return source
}
