// Раскладка «Студия»: две панели — диалоги+память | чат. Панель «Контекст»
// (Память/Профили/Инварианты/MCP) — overlay настроек, открывается кнопкой
// в шапке чата (SettingsOverlay).
import { StudioProvider } from './state'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import SettingsOverlay from './components/SettingsOverlay'

export default function App() {
  return (
    <StudioProvider>
      <div className="studio-grid">
        <Sidebar />
        <ChatPanel />
      </div>
      <SettingsOverlay />
    </StudioProvider>
  )
}
