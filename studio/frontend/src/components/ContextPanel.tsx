// Правая панель «Контекст»: вкладки Память / Токены / Запрос / Профили
// (активная вкладка подчёркнута).
import { useState } from 'react'
import MemoryTab from './MemoryTab'
import TokensTab from './TokensTab'
import RequestsTab from './RequestsTab'
import ProfileTab from './ProfileTab'

const TABS = [
  { id: 'memory', label: 'Память' },
  { id: 'tokens', label: 'Токены' },
  { id: 'request', label: 'Запрос' },
  { id: 'profile', label: 'Профили' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function ContextPanel() {
  const [tab, setTab] = useState<TabId>('memory')

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
            onClick={() => setTab(t.id)}
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
      </div>
    </aside>
  )
}
