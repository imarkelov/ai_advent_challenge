// Вкладка «Инварианты» (день 14, новая схема): жёсткие правила ассистента —
// {title, description, forbidden, is_active}. Ассистент их не меняет и не
// удаляет (неизменяемый слой), но пользователь включает/выключает каждый
// инвариант тумблером: «активный» уходит в промпт, «спит» — нет.
// Форма добавления: [Название][Описание][Запрещённое (через запятую)][вкл]
// [Добавить]; строка списка: title / description / forbidden-чипы /
// статус-чип / тумблер is_active / [×].
// Все действия перечитывают список после ответа API (state.addInvariant /
// state.toggleInvariant / state.deleteInvariant внутри вызывают
// refreshInvariants).
import { useState } from 'react'
import { useStudio } from '../state'

// Запрещённые паттерны из строки формы: по запятой и/или переносу строки,
// trim, пустые убираем
function parseForbidden(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

export default function InvariantsTab() {
  const { state, addInvariant, toggleInvariant, deleteInvariant } = useStudio()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [forbidden, setForbidden] = useState('')
  const [active, setActive] = useState(true)
  const list = state.invariants

  // Добавить инвариант: POST /api/invariants → refreshInvariants
  const add = () => {
    const t = title.trim()
    const d = description.trim()
    if (!t || !d) return
    const forbiddenArr = parseForbidden(forbidden)
    void (async () => {
      try {
        await addInvariant(t, d, forbiddenArr, active)
        setTitle('')
        setDescription('')
        setForbidden('')
        setActive(true)
      } catch (err) {
        console.error('add invariant:', err)
      }
    })()
  }

  // Переключить инвариант: POST /api/invariants/{id}/toggle → refreshInvariants
  const toggle = (id: string) => {
    void (async () => {
      try {
        await toggleInvariant(id)
      } catch (err) {
        console.error('toggle invariant:', err)
      }
    })()
  }

  // Удалить инвариант: DELETE /api/invariants/{id} → refreshInvariants
  const del = (id: string) => {
    void (async () => {
      try {
        await deleteInvariant(id)
      } catch (err) {
        console.error('delete invariant:', err)
      }
    })()
  }

  return (
    <div className="ctx-sections">
      <section className="ctx-card" title="Invariants">
        <header className="ctx-card-head">
          <h3>Инварианты</h3>
          <span
            className="invariants-chip"
            title="Ассистент инварианты не меняет и не удаляет — тумблер только у пользователя"
          >
            неизменяемые
          </span>
          <span className="ctx-count">{list.length} шт.</span>
        </header>
        <p className="invariants-note">
          Жёсткие правила ассистента: ассистент их не меняет и не удаляет.
          Тумблер — только ваш: «спит»-инвариант не уходит в промпт.
        </p>
        <ul className="kv-list inv-list">
          {list.map((inv) => (
            <li key={inv.id} className={'inv-row' + (inv.is_active ? '' : ' off')}>
              <div className="inv-main">
                <span className="inv-title">{inv.title}</span>
                <span className="inv-desc">{inv.description}</span>
                {inv.forbidden.length > 0 && (
                  <span className="inv-chips">
                    {inv.forbidden.map((p) => (
                      <span key={p} className="inv-chip">{p}</span>
                    ))}
                  </span>
                )}
              </div>
              <span
                className={'inv-status' + (inv.is_active ? ' on' : '')}
                title={inv.is_active ? 'Активен: уходит в промпт' : 'Спит: не уходит в промпт'}
              >
                {inv.is_active ? 'активный' : 'спит'}
              </span>
              <input
                type="checkbox"
                className="layer-toggle"
                role="switch"
                aria-checked={inv.is_active}
                checked={inv.is_active}
                aria-label={inv.is_active ? `Выключить ${inv.title}` : `Включить ${inv.title}`}
                onChange={() => toggle(inv.id)}
              />
              <button
                type="button"
                className="btn-icon"
                title="Удалить"
                aria-label={`Удалить ${inv.title}`}
                onClick={() => del(inv.id)}
              >
                ×
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="kv-empty">Пусто</li>}
        </ul>
        <div className="inv-form">
          <input
            className="input"
            placeholder="Название"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            className="input inv-desc-input"
            placeholder="Описание"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <textarea
            className="input inv-desc-input"
            placeholder="Запрещённое (через запятую или перенос строки)"
            rows={2}
            value={forbidden}
            onChange={(e) => setForbidden(e.target.value)}
          />
          <div className="inv-form-foot">
            <label className="inv-form-toggle" title="Активность нового инварианта">
              <input
                type="checkbox"
                className="layer-toggle"
                role="switch"
                aria-checked={active}
                checked={active}
                aria-label="Активность нового инварианта"
                onChange={(e) => setActive(e.target.checked)}
              />
              <span>{active ? 'активный' : 'спит'}</span>
            </label>
            <button
              type="button"
              className="btn btn-add"
              disabled={!title.trim() || !description.trim()}
              onClick={add}
            >
              Добавить
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
