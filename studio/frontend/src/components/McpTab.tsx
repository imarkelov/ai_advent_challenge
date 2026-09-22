// Вкладка «MCP» (день 16): внешние Model Context Protocol серверы —
// stdio-процессы (npx) или streamable-http. Сценарий дня 16: подключить
// сервер и показать его инструменты в UI; вызов инструментов (tool-loop)
// — задел, не реализуется.
// Строка списка: имя / чип типа / статус-чип (не подключён | подключён |
// ошибка) / число инструментов / [Подключить] [×]. Секция «Инструменты
// подключённых серверов» — name + description каждого инструмента.
// Форма добавления: [Имя][тип][command или url][env KEY=VALUE по строкам]
// [Добавить]. Все действия перечитывают список после ответа API
// (state.addMcpServer / deleteMcpServer / connectMcpServer внутри вызывают
// refreshMcp).
import { useState } from 'react'
import { useStudio } from '../state'
import type { McpServerType } from '../api'

// Переменные окружения из формы: строки «KEY=VALUE»; строки без «=»
// или с пустым ключом пропускаем
function parseEnv(raw: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of raw.split('\n')) {
    const i = line.indexOf('=')
    if (i <= 0) continue
    const k = line.slice(0, i).trim()
    if (!k) continue
    out[k] = line.slice(i + 1).trim()
  }
  return out
}

const STATUS_LABEL: Record<string, string> = {
  idle: 'не подключён',
  connected: 'подключён',
  error: 'ошибка',
}

export default function McpTab() {
  const { state, addMcpServer, deleteMcpServer, connectMcpServer } = useStudio()
  const [name, setName] = useState('')
  const [type, setType] = useState<McpServerType>('stdio')
  const [command, setCommand] = useState('')
  const [url, setUrl] = useState('')
  const [envRaw, setEnvRaw] = useState('')
  const [busy, setBusy] = useState(false)
  const servers = state.mcpServers
  const tools = state.mcpTools

  // Подключить сервер: POST /api/mcp/servers/{id}/connect → refreshMcp
  // (статус error — не исключение, виден в чипе)
  const connect = (id: string) => {
    setBusy(true)
    void (async () => {
      try {
        await connectMcpServer(id)
      } catch (err) {
        console.error('connect mcp:', err)
      } finally {
        setBusy(false)
      }
    })()
  }

  // Удалить сервер: DELETE /api/mcp/servers/{id} → refreshMcp
  const del = (id: string) => {
    void (async () => {
      try {
        await deleteMcpServer(id)
      } catch (err) {
        console.error('delete mcp:', err)
      }
    })()
  }

  // Добавить сервер: POST /api/mcp/servers → refreshMcp
  const add = () => {
    const n = name.trim()
    if (!n) return
    const cmd = type === 'stdio' ? command.trim().split(/\s+/).filter(Boolean) : []
    const u = type === 'http' ? url.trim() : ''
    void (async () => {
      try {
        await addMcpServer(n, type, cmd, u, parseEnv(envRaw), true)
        setName('')
        setCommand('')
        setUrl('')
        setEnvRaw('')
      } catch (err) {
        console.error('add mcp:', err)
      }
    })()
  }

  return (
    <div className="ctx-sections">
      <section className="ctx-card" title="MCP servers">
        <header className="ctx-card-head">
          <h3>MCP-серверы</h3>
          <span className="mcp-chip" title="Внешние инструменты (Model Context Protocol)">
            внешние инструменты
          </span>
          <span className="ctx-count">{servers.length} шт.</span>
        </header>
        <p className="mcp-note">
          Внешние MCP-серверы: подключите — и увидите его инструменты.
          День 16: подключение и просмотр; вызов инструментов — задел.
        </p>
        <ul className="kv-list mcp-list">
          {servers.map((s) => (
            <li key={s.id} className={'mcp-row' + (s.enabled ? '' : ' off')}>
              <div className="mcp-main">
                <span className="mcp-name">{s.name}</span>
                <span className="mcp-type" title={s.type === 'stdio' ? s.command.join(' ') : s.url}>
                  {s.type}
                </span>
                {s.status === 'error' && s.error && (
                  <span className="mcp-error" title={s.error}>{s.error}</span>
                )}
              </div>
              <span className={`mcp-status ${s.status}`} title={s.error ?? ''}>
                {STATUS_LABEL[s.status] ?? s.status}
              </span>
              {s.tools_count > 0 && (
                <span className="mcp-count">{s.tools_count} инстр.</span>
              )}
              <button
                type="button"
                className="btn"
                disabled={busy || !s.enabled}
                onClick={() => connect(s.id)}
              >
                Подключить
              </button>
              <button
                type="button"
                className="btn-icon"
                title="Удалить"
                aria-label={`Удалить ${s.name}`}
                onClick={() => del(s.id)}
              >
                ×
              </button>
            </li>
          ))}
          {servers.length === 0 && <li className="kv-empty">Пусто</li>}
        </ul>
        {tools.length > 0 && (
          <div className="mcp-tools">
            <h4>Инструменты подключённых серверов</h4>
            <ul className="kv-list">
              {tools.map((t) => (
                <li key={`${t.server}:${t.name}`} className="mcp-tool-row">
                  <span className="mcp-tool-name">{t.name}</span>
                  <span className="mcp-tool-desc">{t.description}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mcp-form">
          <input
            className="input"
            placeholder="Имя"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <div className="mcp-form-row">
            <select
              className="input"
              value={type}
              aria-label="Тип сервера"
              onChange={(e) => setType(e.target.value as McpServerType)}
            >
              <option value="stdio">stdio (локальный процесс)</option>
              <option value="http">http (удалённый)</option>
            </select>
            {type === 'stdio' ? (
              <input
                className="input"
                placeholder="npx -y @upstash/context7-mcp"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
            ) : (
              <input
                className="input"
                placeholder="https://example.com/mcp"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            )}
          </div>
          <textarea
            className="input mcp-env-input"
            placeholder="Переменные окружения: KEY=VALUE (по строке)"
            rows={2}
            value={envRaw}
            onChange={(e) => setEnvRaw(e.target.value)}
          />
          <div className="inv-form-foot">
            <button
              type="button"
              className="btn btn-add"
              disabled={
                busy || !name.trim()
                || (type === 'stdio' && !command.trim())
                || (type === 'http' && !url.trim())
              }
              onClick={add}
            >
              Добавить
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
