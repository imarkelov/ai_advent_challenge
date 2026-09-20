// Вкладка «Инварианты» (день 14): жёсткие неизменяемые правила ассистента —
// список key → value с удалением по id и форма добавления [key][value][Добавить].
// В отличие от слоёв памяти — БЕЗ свитчей вкл/выкл и без очистки:
// инварианты всегда активны в промпте и не отключаемы.
// Все действия перечитывают список после ответа API (state.addInvariant /
// state.deleteInvariant внутри вызывают refreshInvariants).
import { useState } from 'react'
import { useStudio } from '../state'

export default function InvariantsTab() {
  const { state, addInvariant, deleteInvariant } = useStudio()
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const list = state.invariants

  // Добавить инвариант: POST /api/invariants {key, value} → refreshInvariants
  const add = () => {
    const k = key.trim()
    const v = value.trim()
    if (!k || !v) return
    void (async () => {
      try {
        await addInvariant(k, v)
        setKey('')
        setValue('')
      } catch (err) {
        console.error('add invariant:', err)
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
          <span className="invariants-chip" title="Всегда активны, не отключаемы">
            неизменяемые
          </span>
          <span className="ctx-count">{list.length} шт.</span>
        </header>
        <p className="invariants-note">
          Жёсткие правила ассистента: всегда активны в промпте, не отключаемы.
        </p>
        <ul className="kv-list">
          {list.map((inv) => (
            <li key={inv.id} className="kv-row">
              <span className="kv-key">{inv.key}</span>
              <span className="kv-arrow" aria-hidden>→</span>
              <span className="kv-value">{inv.value}</span>
              <button
                type="button"
                className="btn-icon"
                title="Удалить"
                aria-label={`Удалить ${inv.key}`}
                onClick={() => del(inv.id)}
              >
                ×
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="kv-empty">Пусто</li>}
        </ul>
        <div className="kv-form">
          <input
            className="input"
            placeholder="key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <input
            className="input"
            placeholder="value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-add"
            disabled={!key.trim() || !value.trim()}
            onClick={add}
          >
            Добавить
          </button>
        </div>
      </section>
    </div>
  )
}
