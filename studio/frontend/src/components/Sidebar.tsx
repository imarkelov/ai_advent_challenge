// Левая панель: бренд, список диалогов (чекбоксы-выбор, удаление, inline-ренейм),
// сводка памяти с точками слоёв.
// У имён слоёв title — английские имена (hover-EN).
import { useRef, useState } from 'react'
import { useStudio } from '../state'

export default function Sidebar() {
  const { state, newDialogue, activateDialogue, deleteDialogues, renameDialogue } = useStudio()
  const { dialogues, activeId, memory } = state

  // Выбор для удаления — локальное состояние, сбрасывается после удаления
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [deleting, setDeleting] = useState(false)
  // Inline-ренейм: id редактируемого диалога + текущее значение инпута
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null)
  const renameCancelled = useRef(false)

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const onDeleteSelected = async () => {
    const ids = [...selected]
    if (ids.length === 0 || deleting) return
    if (!window.confirm(`Удалить ${ids.length} диалог(а)?`)) return
    setDeleting(true)
    try {
      await deleteDialogues(ids)
      setSelected(new Set())
    } finally {
      setDeleting(false)
    }
  }

  // Клик по заголовку — режим редактирования (не валим диалог)
  const startRename = (e: React.MouseEvent, id: string, title: string) => {
    e.stopPropagation()
    renameCancelled.current = false
    setRenaming({ id, value: title })
  }

  // Enter/blur — сохранить (trim; пустой title — отмена без запроса); Esc — отмена
  const commitRename = () => {
    if (!renaming) return
    const { id, value } = renaming
    const title = value.trim()
    const cancelled = renameCancelled.current
    setRenaming(null)
    renameCancelled.current = false
    if (cancelled) return
    if (!title) return
    void renameDialogue(id, title)
  }

  return (
    <aside className="panel sidebar">
      <div className="brand">◆ День 11</div>

      <section className="side-block">
        <h2 className="side-title">Диалоги</h2>
        <ul className="dialogue-list">
          {dialogues.map((d) => (
            <li key={d.id} className="dialogue-item">
              <input
                type="checkbox"
                className="dialogue-check"
                checked={selected.has(d.id)}
                onChange={() => toggleSelect(d.id)}
                onClick={(e) => e.stopPropagation()}
                aria-label={`Выбрать «${d.title}»`}
              />
              {renaming?.id === d.id ? (
                <div className="dialogue-rename" onClick={(e) => e.stopPropagation()}>
                  <input
                    className="input rename-input"
                    autoFocus
                    value={renaming.value}
                    onChange={(e) => setRenaming({ id: d.id, value: e.target.value })}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.currentTarget.blur()
                      else if (e.key === 'Escape') {
                        renameCancelled.current = true
                        e.currentTarget.blur()
                      }
                    }}
                  />
                </div>
              ) : (
                <button
                  type="button"
                  className={d.id === activeId ? 'dialogue active' : 'dialogue'}
                  onClick={() => void activateDialogue(d.id)}
                >
                  <span className="dialogue-title" onClick={(e) => startRename(e, d.id, d.title)}>
                    {d.title}
                  </span>
                  <span className="dialogue-count">{d.message_count}</span>
                </button>
              )}
            </li>
          ))}
          {dialogues.length === 0 && <li className="kv-empty">Пока нет диалогов</li>}
        </ul>
        {selected.size > 0 && (
          <button
            type="button"
            className="btn btn-delete"
            disabled={deleting}
            onClick={() => void onDeleteSelected()}
          >
            Удалить ({selected.size})
          </button>
        )}
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
