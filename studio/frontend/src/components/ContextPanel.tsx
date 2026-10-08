// Правая панель «Контекст»: вкладки Memory / Users / Invariants / RAG /
// MemTask (активная вкладка подчёркнута). День 12: активная вкладка хранится
// в общем состоянии StudioProvider — бейдж профиля в шапке чата открывает
// вкладку «Users» извне панели. Вкладки «Токены»/«Запрос» перенесены в
// сайдбар (Sidebar).
//
// День 26: вкладки переименованы (Память→Memory, Профили→Users,
// Инварианты→Invariants, База знаний→RAG, Задача→MemTask) и получили иконки.
// Иконки — инлайновые SVG (stroke=currentColor): наследуют цвет вкладки,
// включая акцентный у активной, и не требуют шрифтов/эмодзи.
import type { ReactNode } from 'react'
import MemoryTab from './MemoryTab'
import ProfileTab from './ProfileTab'
import InvariantsTab from './InvariantsTab'
import KbTab from './KbTab'
import TaskStateTab from './TaskStateTab'
import { useStudio } from '../state'

// Иконки вкладок: 16×16, stroke=currentColor (цвет берётся от вкладки).
function TabIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      className="tab-icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

// Memory — слои памяти (диалог / рабочая / долговременная).
const iconMemory = (
  <TabIcon>
    <path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" />
    <path d="M3 12.5 12 17l9-4.5" />
    <path d="M3 17 12 21.5 21 17" />
  </TabIcon>
)

// Users — профиль пользователя.
const iconUsers = (
  <TabIcon>
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </TabIcon>
)

// Invariants — жёсткие правила (щит).
const iconInvariants = (
  <TabIcon>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
    <path d="m9 12 2 2 4-4" />
  </TabIcon>
)

// RAG — база знаний и поиск по документам.
const iconRag = (
  <TabIcon>
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
    <path d="M10 7h6M10 11h4" />
  </TabIcon>
)

// MemTask — память задачи (цель и уточнения диалога).
const iconMemTask = (
  <TabIcon>
    <path d="M9 11l3 3L22 4" />
    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
  </TabIcon>
)

const TABS: { id: 'memory' | 'profile' | 'invariants' | 'kb' | 'task'
  label: string
  icon: ReactNode
  title: string }[] = [
  { id: 'memory', label: 'Memory', icon: iconMemory,
    title: 'Слои памяти: диалог, рабочая, долговременная' },
  { id: 'profile', label: 'Users', icon: iconUsers,
    title: 'Профиль пользователя: имя, роль, тон, табу' },
  { id: 'invariants', label: 'Invariants', icon: iconInvariants,
    title: 'Инварианты — жёсткие правила с высшим приоритетом' },
  { id: 'kb', label: 'RAG', icon: iconRag,
    title: 'RAG: база знаний, индекс документов, поиск' },
  { id: 'task', label: 'MemTask', icon: iconMemTask,
    title: 'Память задачи: цель, уточнения, ограничения диалога' },
]

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
            title={t.title}
            aria-label={t.label}
            aria-selected={tab === t.id}
            className={tab === t.id ? 'tab active' : 'tab'}
            onClick={() => setContextTab(t.id)}
          >
            {t.icon}
            <span className="tab-label">{t.label}</span>
          </button>
        ))}
      </nav>
      <div className="context-body">
        {tab === 'memory' && <MemoryTab />}
        {tab === 'profile' && <ProfileTab />}
        {tab === 'invariants' && <InvariantsTab />}
        {tab === 'kb' && <KbTab />}
        {tab === 'task' && <TaskStateTab />}
      </div>
    </aside>
  )
}
