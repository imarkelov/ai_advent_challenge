// Вкладка «MCP» (день 16): внешние Model Context Protocol серверы —
// stdio-процессы (npx) или streamable-http. Сценарий дня 16: подключить
// сервер и показать его инструменты в UI; вызов инструментов (tool-loop)
// — задел, не реализуется.
// Строка списка: имя / чип типа / статус-чип (не подключён | подключён |
// ошибка) / число инструментов / [Подключить] [×]. Секция «Инструменты
// подключённых серверов» — name + description каждого инструмента.
// Добавление новых серверов в UI нет — реестр фиксирован (дефолты).
// Все действия перечитывают список после ответа API
// (state.deleteMcpServer / connectMcpServer внутри вызывают refreshMcp).
import { useState } from 'react'
import { useStudio } from '../state'

const STATUS_LABEL: Record<string, string> = {
  idle: 'не подключён',
  connected: 'подключён',
  error: 'ошибка',
}

export default function McpTab() {
  const { state, deleteMcpServer, connectMcpServer } = useStudio()
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
      </section>
    </div>
  )
}
