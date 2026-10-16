// Overlay «Ассистент» (дни 30–34): панель ассистента разработчика и
// поддержки выезжает справа по своей кнопке в шапке чата (как MCP/Сервис).
// Контент — <AssistantTab />.
import AssistantTab from './AssistantTab'
import { useStudio } from '../state'

export default function AssistantOverlay() {
  const { assistantOpen, closeAssistant } = useStudio()

  return (
    <div className={assistantOpen ? 'settings-overlay open' : 'settings-overlay'}>
      <div className="settings-backdrop" aria-hidden="true" onClick={closeAssistant} />
      <aside
        className="settings-panel"
        role="dialog"
        aria-label="Ассистент разработчика и поддержки"
        aria-hidden={!assistantOpen}
      >
        <div className="settings-head">
          <h2 className="settings-title">Ассистент</h2>
          {assistantOpen && (
            <button
              type="button"
              className="btn-icon settings-close"
              aria-label="Закрыть ассистента"
              title="Закрыть ассистента"
              onClick={closeAssistant}
            >
              ×
            </button>
          )}
        </div>
        {assistantOpen && <AssistantTab />}
      </aside>
    </div>
  )
}
