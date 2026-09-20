// Правая панель «Контекст»: вкладки Память / Токены / Запрос / Профили /
// Инварианты (активная вкладка подчёркнута). День 12: активная вкладка
// хранится в общем состоянии StudioProvider — бейдж профиля в шапке чата
// открывает вкладку «Профили» извне панели.
import MemoryTab from './MemoryTab'
import TokensTab from './TokensTab'
import RequestsTab from './RequestsTab'
import ProfileTab from './ProfileTab'
import InvariantsTab from './InvariantsTab'
import { useStudio } from '../state'

const TABS = [
  { id: 'memory', label: 'Память' },
  { id: 'tokens', label: 'Токены' },
  { id: 'request', label: 'Запрос' },
  { id: 'profile', label: 'Профили' },
  { id: 'invariants', label: 'Инварианты' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function ContextPanel() {
  const { state, setContextTab } = useStudio()
  const tab: TabId = state.contextTab

  return (
    <aside className="panel context">
      <nav className="tabs" role="tablist" aria-label="Контекст">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? 'tab active' : 'tab'}
            onClick={() => setContextTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="context-body">
        {tab === 'memory' && <MemoryTab />}
        {tab === 'tokens' && <TokensTab />}
        {tab === 'request' && <RequestsTab />}
        {tab === 'profile' && <ProfileTab />}
        {tab === 'invariants' && <InvariantsTab />}
      </div>
    </aside>
  )
}
