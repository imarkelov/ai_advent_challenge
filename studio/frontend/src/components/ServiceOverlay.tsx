// Overlay «Сервис» (день 30): панель управления сетевым сервисом локальной
// LLM выезжает справа по своей кнопке в шапке чата (как MCP/Сериалы).
// Контент — <ServiceTab />.
import ServiceTab from './ServiceTab'
import { useStudio } from '../state'

export default function ServiceOverlay() {
  const { serviceOpen, closeService } = useStudio()

  return (
    <div className={serviceOpen ? 'settings-overlay open' : 'settings-overlay'}>
      <div className="settings-backdrop" aria-hidden="true" onClick={closeService} />
      <aside
        className="settings-panel"
        role="dialog"
        aria-label="Сервис локальной LLM"
        aria-hidden={!serviceOpen}
      >
        <div className="settings-head">
          <h2 className="settings-title">Сервис</h2>
          {serviceOpen && (
            <button
              type="button"
              className="btn-icon settings-close"
              aria-label="Закрыть сервис"
              title="Закрыть сервис"
              onClick={closeService}
            >
              ×
            </button>
          )}
        </div>
        {serviceOpen && <ServiceTab />}
      </aside>
    </div>
  )
}
