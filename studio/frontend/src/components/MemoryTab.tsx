// Вкладка «Память»: три карточки слоёв памяти —
// «Диалог» (Short-Term), «Текущая задача» (Working), «Долговременная» (Long-Term).
// Все действия перечитывают memory после ответа.
import { useState } from 'react'
import { apiDelete, apiPost } from '../api'
import { useStudio, type MemoryLayer } from '../state'
import RulesModal from './RulesModal'
import SystemPromptModal from './SystemPromptModal'

// Карточка диалогового слоя: счётчик сообщений + tokens_est,
// сворачиваемый список сообщений, [clear] → POST /api/memory/st/clear
function DialogueCard() {
  const { state, refreshMemory, reloadDialogue } = useStudio()
  const [busy, setBusy] = useState(false)
  const d = state.memory?.dialogue

  const clear = async () => {
    setBusy(true)
    try {
      await apiPost('/memory/st/clear')
      // после очистки перечитываем и память, и сообщения диалога
      await Promise.all([refreshMemory(), reloadDialogue()])
    } catch (err) {
      console.error('st/clear:', err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="ctx-card" title="Short-Term Memory">
      <header className="ctx-card-head">
        <h3>Диалог</h3>
        <span className="ctx-count">{d ? `${d.message_count} сообщ. · ~${d.tokens_est} tok` : '—'}</span>
      </header>
      <details className="msg-details">
        <summary>Сообщения ({state.messages.length})</summary>
        <ul className="dialogue-msgs">
          {state.messages.map((m, i) => (
            <li key={i} className="dialogue-msg">
              <span className="dialogue-msg-role">{m.role === 'user' ? 'вы' : 'модель'}</span>
              <span className="dialogue-msg-text">{m.content}</span>
            </li>
          ))}
          {state.messages.length === 0 && <li className="kv-empty">Пусто</li>}
        </ul>
      </details>
      <button type="button" className="btn btn-clear" disabled={busy} onClick={() => void clear()}>
        [clear]
      </button>
    </section>
  )
}

// Карточка key/value-слоя (working или longterm): строки «key → value»
// с удалением, форма добавления [key][value][+], [clear].
function LayerCard({
  label,
  title,
  base,
  layer,
}: {
  label: string
  title: string
  base: 'working' | 'longterm'
  layer?: MemoryLayer
}) {
  const { refreshMemory } = useStudio()
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')

  // Добавить запись: POST /api/memory/{base} {key, value}
  const add = () => {
    const k = key.trim()
    const v = value.trim()
    if (!k || !v) return
    void (async () => {
      try {
        await apiPost(`/memory/${base}`, { key: k, value: v })
        setKey('')
        setValue('')
        await refreshMemory()
      } catch (err) {
        console.error('add entry:', err)
      }
    })()
  }

  // Удалить запись: DELETE /api/memory/{base}/{key}
  const del = (k: string) => {
    void (async () => {
      try {
        await apiDelete(`/memory/${base}/${encodeURIComponent(k)}`)
        await refreshMemory()
      } catch (err) {
        console.error('delete entry:', err)
      }
    })()
  }

  // Очистить слой: POST /api/memory/{base}/clear
  const clear = () => {
    void (async () => {
      try {
        await apiPost(`/memory/${base}/clear`)
        await refreshMemory()
      } catch (err) {
        console.error('clear layer:', err)
      }
    })()
  }

  return (
    <section className="ctx-card" title={title}>
      <header className="ctx-card-head">
        <h3>{label}</h3>
        <span className="ctx-count">{layer ? `${layer.entries} · ~${layer.tokens_est} tok` : '—'}</span>
      </header>
      <ul className="kv-list">
        {layer &&
          Object.entries(layer.items).map(([k, v]) => (
            <li key={k} className="kv-row">
              <span className="kv-key">{k}</span>
              <span className="kv-arrow" aria-hidden>→</span>
              <span className="kv-value">{v}</span>
              <button type="button" className="btn-icon" title="Удалить" onClick={() => del(k)}>×</button>
            </li>
          ))}
        {(!layer || layer.entries === 0) && <li className="kv-empty">Пусто</li>}
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
        <button type="button" className="btn btn-add" disabled={!key.trim() || !value.trim()} onClick={add}>
          [+]
        </button>
      </div>
      <button type="button" className="btn btn-clear" onClick={clear}>[clear]</button>
    </section>
  )
}

export default function MemoryTab() {
  const { state } = useStudio()
  const [rulesOpen, setRulesOpen] = useState(false)
  const [promptOpen, setPromptOpen] = useState(false)
  return (
    <div className="ctx-sections">
      <div className="ctx-toolbar">
        <button type="button" className="btn" onClick={() => setRulesOpen(true)}>
          Правила
        </button>
        <button type="button" className="btn" onClick={() => setPromptOpen(true)}>
          Системный промпт
        </button>
      </div>
      <DialogueCard />
      <LayerCard
        label="Текущая задача"
        title="Working Memory"
        base="working"
        layer={state.memory?.working}
      />
      <LayerCard
        label="Долговременная"
        title="Long-Term Memory"
        base="longterm"
        layer={state.memory?.long_term}
      />
      {rulesOpen && <RulesModal onClose={() => setRulesOpen(false)} />}
      {promptOpen && <SystemPromptModal onClose={() => setPromptOpen(false)} />}
    </div>
  )
}
