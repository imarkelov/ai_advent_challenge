// Левая панель: бренд, список диалогов, сводка памяти с точками слоёв.
// Строка диалога: клик — ТОЛЬКО активация; карандаш (title="Переименовать") —
// inline-ренейм; корзина (title="Удалить") — одиночное удаление с confirm.
// Иконка «Режим выбора» (title) в заголовке «Диалоги»: чекбоксы на всех строках,
// массовое удаление «Удалить (N)»; иконки-действия в этом режиме скрыты.
import { useRef, useState } from 'react'
import { useStudio } from '../state'
import TokensTab from './TokensTab'
import RequestsTab from './RequestsTab'

// Inline-SVG иконки 15px (stroke: currentColor) — без icon-библиотек
function PencilIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  )
}

function SelectModeIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 5l2 2 3-3" />
      <path d="M3 12l2 2 3-3" />
      <path d="M3 19l2 2 3-3" />
      <path d="M13 5h8M13 12h8M13 19h8" />
    </svg>
  )
}

// «Задача использовалась» (день 13b): три связанных узла (flow) — 13px,
// stroke-стиль как у иконок-действий
function TaskUsedIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="5" cy="6" r="2.5" />
      <circle cx="19" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M7.5 6h9" />
      <path d="M6.7 8.2l4.1 7" />
      <path d="M17.3 8.2l-4.1 7" />
    </svg>
  )
}

export default function Sidebar() {
  const { state, newDialogue, activateDialogue, deleteDialogues, renameDialogue } = useStudio()
  const { dialogues, activeId, memory } = state

  // Режим выбора: чекбоксы на строках, переключатель в заголовке «Диалоги»
  const [selectMode, setSelectMode] = useState(false)
  // Выбор для удаления — локальное состояние, сбрасывается после удаления
  // и при выключении режима выбора
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [deleting, setDeleting] = useState(false)
  // Inline-ренейм: id редактируемого диалога + текущее значение инпута.
  // Открывается ТОЛЬКО по клику на иконку карандаша.
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null)
  const renameCancelled = useRef(false)
  // Локальный таб «Токены»/«Запрос» в сайдбаре (перенесён из ContextPanel)
  const [toolTab, setToolTab] = useState<'tokens' | 'request'>('tokens')
  // Список диалогов: по умолчанию — 5 свежих (массив oldest→newest,
  // свежие внизу); старые свёрнуты за «Показать ещё». В режиме выбора —
  // все строки (чекбоксы должны быть доступны)
  const [showAll, setShowAll] = useState(false)
  const visibleDialogues = showAll || selectMode
    ? dialogues
    : dialogues.slice(-5)

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelected(new Set())
  }

  const onDeleteSelected = async () => {
    const ids = [...selected]
    if (ids.length === 0 || deleting) return
    if (!window.confirm(`Удалить ${ids.length} диалог(а)?`)) return
    setDeleting(true)
    try {
      await deleteDialogues(ids)
      // после успешного удаления режим выбора выключается
      exitSelectMode()
    } finally {
      setDeleting(false)
    }
  }

  // Корзина: одиночное удаление с confirm
  const onDeleteOne = (id: string, title: string) => {
    if (!window.confirm(`Удалить диалог «${title}»?`)) return
    void deleteDialogues([id]).then(() => setSelected(new Set()))
  }

  // Карандаш — режим редактирования (единственный способ переименовать)
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
      <div className="brand">◆ AI Studio</div>

      <section className="side-block">
        <div className="side-title-row">
          <h2 className="side-title">Диалоги</h2>
          <button
            type="button"
            className={selectMode ? 'btn-icon select-mode active' : 'btn-icon select-mode'}
            title="Режим выбора"
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
          >
            <SelectModeIcon />
          </button>
        </div>
        <ul className="dialogue-list">
          {dialogues.length > 5 && !selectMode && (
            <li className="dialogue-item">
              <button
                type="button"
                className="dialogue-more"
                onClick={() => setShowAll((v) => !v)}
              >
                {showAll ? 'Свернуть ▴' : `Показать ещё ${dialogues.length - 5} (старые) ▾`}
              </button>
            </li>
          )}
          {visibleDialogues.map((d) => (
            <li key={d.id} className="dialogue-item">
              {selectMode && (
                <input
                  type="checkbox"
                  className="dialogue-check"
                  checked={selected.has(d.id)}
                  onChange={() => toggleSelect(d.id)}
                  onClick={(e) => e.stopPropagation()}
                  aria-label={`Выбрать «${d.title}»`}
                />
              )}
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
                <>
                  <button
                    type="button"
                    className={d.id === activeId ? 'dialogue active' : 'dialogue'}
                    onClick={() => (selectMode ? toggleSelect(d.id) : void activateDialogue(d.id))}
                  >
                    <span className="dialogue-title">{d.title}</span>
                    {d.used_task && (
                      <span className="dialogue-task-flag" title="Проект использовался">
                        <TaskUsedIcon />
                      </span>
                    )}
                    <span className="dialogue-count">{d.message_count}</span>
                  </button>
                  {!selectMode && (
                    <div className="dialogue-actions">
                      <button
                        type="button"
                        className="btn-icon"
                        title="Переименовать"
                        onClick={(e) => startRename(e, d.id, d.title)}
                      >
                        <PencilIcon />
                      </button>
                      <button
                        type="button"
                        className="btn-icon btn-icon-danger"
                        title="Удалить"
                        onClick={() => onDeleteOne(d.id, d.title)}
                      >
                        <TrashIcon />
                      </button>
                    </div>
                  )}
                </>
              )}
            </li>
          ))}
          {dialogues.length === 0 && <li className="kv-empty">Пока нет диалогов</li>}
        </ul>
        {selectMode && selected.size > 0 && (
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

      {/* Инструменты: вкладки «Токены»/«Запрос», перенесённые из ContextPanel */}
      <section className="side-block">
        <h2 className="side-title">Инструменты</h2>
        <nav className="tabs" role="tablist" aria-label="Инструменты">
          <button
            type="button"
            role="tab"
            aria-selected={toolTab === 'tokens'}
            className={toolTab === 'tokens' ? 'tab active' : 'tab'}
            onClick={() => setToolTab('tokens')}
          >
            Токены
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={toolTab === 'request'}
            className={toolTab === 'request' ? 'tab active' : 'tab'}
            onClick={() => setToolTab('request')}
          >
            Запрос
          </button>
        </nav>
        <div className="context-body">
          {toolTab === 'tokens' && <TokensTab />}
          {toolTab === 'request' && <RequestsTab />}
        </div>
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
