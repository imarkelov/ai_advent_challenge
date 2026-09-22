// Раскладка «Студия»: две панели — диалоги+память | чат. Панель «Контекст»
// (Память/Профили/Инварианты) — overlay настроек (SettingsOverlay), панель
// MCP — свой overlay (McpOverlay); оба открываются кнопками в шапке чата.
import { StudioProvider } from './state'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import SettingsOverlay from './components/SettingsOverlay'
import McpOverlay from './components/McpOverlay'

export default function App() {
  return (
    <StudioProvider>
      <div className="studio-grid">
        <Sidebar />
        <ChatPanel />
      </div>
      <SettingsOverlay />
      <McpOverlay />
    </StudioProvider>
  )
}
