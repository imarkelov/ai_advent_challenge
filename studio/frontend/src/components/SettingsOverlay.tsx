// Overlay настроек: панель «Контекст» (вкладки Память / Профили /
// Инварианты) больше не занимает постоянную правую колонку — она
// выезжает справа поверх остального по кнопке «⚙ Настройки» в шапке чата.
// Контент — готовый ContextPanel без изменений; здесь только обвязка:
// фон (backdrop, клик закрывает) + заголовок с кнопкой «×».
import ContextPanel from './ContextPanel'
import { useStudio } from '../state'

export default function SettingsOverlay() {
  const { settingsOpen, closeSettings } = useStudio()

  return (
    <div className={settingsOpen ? 'settings-overlay open' : 'settings-overlay'}>
      <div className="settings-backdrop" aria-hidden="true" onClick={closeSettings} />
      <aside className="settings-panel" role="dialog" aria-label="Настройки" aria-hidden={!settingsOpen}>
        <div className="settings-head">
          <h2 className="settings-title">Настройки</h2>
          {settingsOpen && (
            <button
              type="button"
              className="btn-icon settings-close"
              aria-label="Закрыть настройки"
              title="Закрыть настройки"
              onClick={closeSettings}
            >
              ×
            </button>
          )}
        </div>
        {settingsOpen && <ContextPanel />}
      </aside>
    </div>
  )
}
