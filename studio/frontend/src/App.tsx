// Раскладка «Студия»: три панели — диалоги+память | чат | контекст
import { StudioProvider } from './state'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import ContextPanel from './components/ContextPanel'

export default function App() {
  return (
    <StudioProvider>
      <div className="studio-grid">
        <Sidebar />
        <ChatPanel />
        <ContextPanel />
      </div>
    </StudioProvider>
  )
}
