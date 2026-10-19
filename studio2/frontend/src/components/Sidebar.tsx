const TABS = [
  ['models', 'Модели'],
  ['rag', 'RAG'],
  ['mcp', 'MCP'],
  ['tasks', 'Задачи'],
  ['pipeline', 'Pipeline'],
  ['runs', 'Запуски'],
] as const

export type TabId = (typeof TABS)[number][0]

export default function Sidebar({ active, onChange }: { active: TabId; onChange: (tab: TabId) => void }) {
  return (
    <nav className="sidebar">
      {TABS.map(([id, label]) => (
        <button key={id} className={id === active ? 'active' : ''} onClick={() => onChange(id)}>
          {label}
        </button>
      ))}
    </nav>
  )
}
