// Overlay «Сериалы» (день 29): панель поиска сериалов выезжает справа по
// своей кнопке в шапке чата (как MCP/настройки). Контент — <SeriesTab />.
import SeriesTab from './SeriesTab'
import { useStudio } from '../state'

export default function SeriesOverlay() {
  const { seriesOpen, closeSeries } = useStudio()

  return (
    <div className={seriesOpen ? 'settings-overlay open' : 'settings-overlay'}>
      <div className="settings-backdrop" aria-hidden="true" onClick={closeSeries} />
      <aside
        className="settings-panel"
        role="dialog"
        aria-label="Поиск сериалов"
        aria-hidden={!seriesOpen}
      >
        <div className="settings-head">
          <h2 className="settings-title">Сериалы</h2>
          {seriesOpen && (
            <button
              type="button"
              className="btn-icon settings-close"
              aria-label="Закрыть поиск сериалов"
              title="Закрыть поиск сериалов"
              onClick={closeSeries}
            >
              ×
            </button>
          )}
        </div>
        {seriesOpen && <SeriesTab />}
      </aside>
    </div>
  )
}
