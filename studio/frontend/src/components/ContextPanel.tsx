// Правая панель «Контекст»: вкладки Память / Профили / Инварианты (активная
// вкладка подчёркнута). День 12: активная вкладка хранится в общем состоянии
// StudioProvider — бейдж профиля в шапке чата открывает вкладку «Профили»
// извне панели. Вкладки «Токены»/«Запрос» перенесены в сайдбар (Sidebar).
import MemoryTab from './MemoryTab'
import ProfileTab from './ProfileTab'
import InvariantsTab from './InvariantsTab'
import { useStudio } from '../state'

const TABS = [
  { id: 'memory', label: 'Память' },
  { id: 'profile', label: 'Профили' },
  { id: 'invariants', label: 'Инварианты' },
] as const

export default function ContextPanel() {
  const { state, setContextTab } = useStudio()
  const tab = state.contextTab

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
        {tab === 'profile' && <ProfileTab />}
        {tab === 'invariants' && <InvariantsTab />}
      </div>
    </aside>
  )
}
