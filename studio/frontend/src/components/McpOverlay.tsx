// Overlay MCP (день 16): панель MCP-серверов выезжает справа по своей кнопке в шапке чата (отдельно от настроек «⚙»). Контент — <McpTab />.
import McpTab from './McpTab'
import { useStudio } from '../state'

export default function McpOverlay() {
  const { mcpOpen, closeMcp } = useStudio()

  return (
    <div className={mcpOpen ? 'settings-overlay open' : 'settings-overlay'}>
      <div className="settings-backdrop" aria-hidden="true" onClick={closeMcp} />
      <aside className="settings-panel" role="dialog" aria-label="MCP" aria-hidden={!mcpOpen}>
        <div className="settings-head">
          <h2 className="settings-title">MCP</h2>
          {mcpOpen && (
            <button
              type="button"
              className="btn-icon settings-close"
              aria-label="Закрыть MCP"
              title="Закрыть MCP"
              onClick={closeMcp}
            >
              ×
            </button>
          )}
        </div>
        {mcpOpen && <McpTab />}
      </aside>
    </div>
  )
}
