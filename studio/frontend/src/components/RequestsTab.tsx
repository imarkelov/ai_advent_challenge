// Вкладка «Запрос»: аннотированный журнал API-запросов (день 11).
// Известные (из PARAM_GLOSSARY) параметры — цветное имя (--st) + RU-пояснение;
// неизвестные — серые, пунктирная рамка вокруг имени + UNKNOWN_HINT.
// Вложенные объекты (messages[], *_details) раскрываются рекурсивно.
import { useState } from 'react'
import { apiGet } from '../api'
import { PARAM_GLOSSARY, UNKNOWN_HINT } from '../glossary'
import { useStudio, type RequestDetail } from '../state'
import Modal from './Modal'

// Усекание длинных значений для отображения
const truncate = (s: string, n: number): string => (s.length > n ? s.slice(0, n) + '…' : s)

// Отображение скалярного значения
function scalarValue(value: unknown): string {
  if (typeof value === 'string') return `"${truncate(value, 120)}"`
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return '—'
}

// Строка параметра: имя = значение — пояснение
function ParamLine({ name, value }: { name: string; value: unknown }) {
  const known = Object.prototype.hasOwnProperty.call(PARAM_GLOSSARY, name)
  const isObject = value !== null && typeof value === 'object'
  return (
    <div className="param-line">
      <div className="param-head">
        <span className={known ? 'param-name known' : 'param-name unknown'}>{name}</span>
        {!isObject && (
          <>
            <span className="param-eq">=</span>
            <span className="param-value">{scalarValue(value)}</span>
          </>
        )}
        <span className="param-note">{known ? PARAM_GLOSSARY[name] : UNKNOWN_HINT}</span>
      </div>
      {isObject && (
        <div className="param-children">{renderChildren(name, value)}</div>
      )}
    </div>
  )
}

// Рекурсивное раскрытие вложенных значений:
// messages[] — список «role: первые N символов content», остальное — по полям
function renderChildren(name: string, value: unknown) {
  if (Array.isArray(value)) {
    if (name === 'messages') {
      return value.map((m, i) => {
        const item = (m ?? {}) as { role?: unknown; content?: unknown }
        const role = typeof item.role === 'string' ? item.role : '?'
        const content = typeof item.content === 'string' ? item.content : ''
        return (
          <div key={i} className="msg-item">
            {role}: {truncate(content, 80)}
          </div>
        )
      })
    }
    return value.map((item, i) =>
      item !== null && typeof item === 'object' ? (
        <ParamLine key={i} name={`${name}[${i}]`} value={item} />
      ) : (
        <div key={i} className="param-line">
          <div className="param-head">
            <span className="param-name unknown">{`${name}[${i}]`}</span>
            <span className="param-eq">=</span>
            <span className="param-value">{scalarValue(item)}</span>
            <span className="param-note">{UNKNOWN_HINT}</span>
          </div>
        </div>
      ),
    )
  }
  if (value !== null && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).map(([k, v]) => (
      <ParamLine key={k} name={k} value={v} />
    ))
  }
  return null
}

// Аннотированная карточка запроса: «→ Запрос #N» + «← Ответ · расход токенов».
// Кнопка «Полный запрос (JSON)» — resizable-модалка с pretty-JSON и копированием.
export function RequestCard({ detail }: { detail: RequestDetail }) {
  const [jsonOpen, setJsonOpen] = useState(false)
  const json = JSON.stringify(detail.request, null, 2)
  return (
    <article className="req-card">
      <header className="req-head">
        → Запрос #{detail.id} · {detail.ts} · {detail.model}
      </header>
      <div className="req-body">
        {Object.entries(detail.request).map(([k, v]) => (
          <ParamLine key={k} name={k} value={v} />
        ))}
      </div>
      <header className="req-head req-head-resp">← Ответ · расход токенов</header>
      <div className="req-body">
        {detail.usage ? (
          Object.entries(detail.usage).map(([k, v]) => <ParamLine key={k} name={k} value={v} />)
        ) : (
          <p className="req-empty">usage нет</p>
        )}
      </div>
      {detail.error && <p className="req-error">Ошибка: {detail.error}</p>}
      <button type="button" className="btn req-json-btn" onClick={() => setJsonOpen(true)}>
        Полный запрос (JSON)
      </button>
      {jsonOpen && (
        <Modal
          title={`Запрос #${detail.id} — JSON`}
          onClose={() => setJsonOpen(false)}
          resizable
          copyText={json}
        >
          <pre className="json-pre">{json}</pre>
        </Modal>
      )}
    </article>
  )
}

export default function RequestsTab() {
  const { state, setShowRequests } = useStudio()
  // null → показываем lastRequest (последний); клик по журналу → его запись
  const [viewing, setViewing] = useState<RequestDetail | null>(null)
  const open = state.showRequests
  const detail = viewing ?? state.lastRequest

  const openJournal = (id: number) => {
    apiGet<RequestDetail>(`/requests/${id}`)
      .then(setViewing)
      .catch((err) => console.error('openJournal:', err))
  }

  return (
    <div className="ctx-sections">
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={open}
          onChange={(e) => setShowRequests(e.target.checked)}
        />
        <span>Показывать запросы</span>
      </label>
      {!open ? (
        <p className="req-hidden">Журнал запросов скрыт</p>
      ) : (
        <>
          {detail ? (
            <RequestCard detail={detail} />
          ) : (
            <p className="req-empty">Запросов пока нет</p>
          )}
          {state.requests.length > 0 && (
            <div className="journal">
              <h3 className="journal-title">Журнал</h3>
              <ul className="journal-list">
                {state.requests.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      className={viewing?.id === r.id ? 'journal-item active' : 'journal-item'}
                      onClick={() => openJournal(r.id)}
                    >
                      {`#${r.id} · ${r.ts} · ${r.total_tokens}`}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}
