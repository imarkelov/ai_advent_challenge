import { useCallback, useEffect, useState } from 'react'
import {
  addMcpServer, connectMcpServer, deleteMcpServer, disconnectMcpServer,
  getMcpServers, type McpServer,
} from '../api'

export default function McpTab() {
  const [servers, setServers] = useState<McpServer[]>([])
  const [name, setName] = useState('allure')
  const [type, setType] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setServers((await getMcpServers()).servers)
    } catch (exc) {
      setError((exc as Error).message)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      setError('')
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  return (
    <div>
      <h2>MCP-серверы</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead><tr><th>Имя</th><th>Тип</th><th>Статус</th><th>Инструменты</th><th /></tr></thead>
        <tbody>
          {servers.map((server) => (
            <tr key={server.id}>
              <td>{server.name}</td>
              <td>{server.type}</td>
              <td>{server.status.state}</td>
              <td>{server.status.tools_count}</td>
              <td>
                <button onClick={() => act(() => connectMcpServer(server.id))}>Подключить</button>
                <button onClick={() => act(() => disconnectMcpServer(server.id))}>Отключить</button>
                <button onClick={() => act(() => deleteMcpServer(server.id))}>Удалить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="row">
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="имя" />
          <select value={type} onChange={(event) => setType(event.target.value as 'stdio' | 'http')}>
            <option value="stdio">stdio</option>
            <option value="http">http</option>
          </select>
        </div>
        {type === 'stdio' ? (
          <input value={command} onChange={(event) => setCommand(event.target.value)}
                 placeholder="python -m allure_mcp" style={{ marginTop: 8 }} />
        ) : (
          <input value={url} onChange={(event) => setUrl(event.target.value)}
                 placeholder="https://allure.lmru.tech/api" style={{ marginTop: 8 }} />
        )}
        <button style={{ marginTop: 8 }} onClick={() => act(() => addMcpServer(
          type === 'stdio'
            ? { name, type, command: command.split(/\s+/).filter(Boolean) }
            : { name, type, url },
        ))}>
          Добавить
        </button>
      </div>
    </div>
  )
}
