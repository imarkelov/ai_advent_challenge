// Левая панель: бренд, список диалогов, сводка памяти с точками слоёв.
// У имён слоёв title — английские имена (hover-EN).
import { useStudio } from '../state'

export default function Sidebar() {
  const { state, newDialogue, activateDialogue } = useStudio()
  const { dialogues, activeId, memory } = state

  return (
    <aside className="panel sidebar">
      <div className="brand">◆ День 11</div>

      <section className="side-block">
        <h2 className="side-title">Диалоги</h2>
        <ul className="dialogue-list">
          {dialogues.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                className={d.id === activeId ? 'dialogue active' : 'dialogue'}
                onClick={() => void activateDialogue(d.id)}
              >
                <span className="dialogue-title">{d.title}</span>
                <span className="dialogue-count">{d.message_count}</span>
              </button>
            </li>
          ))}
          {dialogues.length === 0 && <li className="kv-empty">Пока нет диалогов</li>}
        </ul>
        <button type="button" className="btn new-dialogue" onClick={() => void newDialogue()}>
          + Новый диалог
        </button>
      </section>

      <section className="side-block">
        <h2 className="side-title">Память</h2>
        <ul className="memory-rows">
          <li className="memory-row" title="Short-Term Memory">
            <span className="dot dot-st" aria-hidden />
            <span className="memory-label">Диалог</span>
            <span className="memory-count">{memory ? memory.dialogue.message_count : '—'}</span>
          </li>
          <li className="memory-row" title="Working Memory">
            <span className="dot dot-wm" aria-hidden />
            <span className="memory-label">Текущая задача</span>
            <span className="memory-count">{memory ? memory.working.entries : '—'}</span>
          </li>
          <li className="memory-row" title="Long-Term Memory">
            <span className="dot dot-lt" aria-hidden />
            <span className="memory-label">Долговременная</span>
            <span className="memory-count">{memory ? memory.long_term.entries : '—'}</span>
          </li>
        </ul>
      </section>
    </aside>
  )
}
