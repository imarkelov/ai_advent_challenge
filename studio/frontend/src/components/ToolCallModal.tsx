// Модалка вызова инструмента MCP (день 16, tool-loop): форма, собранная из
// input_schema инструмента (JSON schema {type, properties, required}), кнопки
// [Отмена]/[Вызвать]. Вызвать → onInvoke(args); ошибка onInvoke или битый JSON
// в textarea — inline-сообщение, модалка остаётся открытой. На успехе родитель
// закрывает модалку (результат придёт как mcp-card в ленте).
import { useState } from 'react'
import Modal from './Modal'

type SchemaProp = { type?: string; description?: string }
type InputSchema = {
  type?: string
  properties?: Record<string, SchemaProp>
  required?: string[]
}

export interface ToolCallModalProps {
  serverName: string
  tool: { name: string; description?: string; input_schema?: Record<string, unknown> }
  onCancel: () => void
  onInvoke: (args: Record<string, unknown>) => Promise<void>
}

export default function ToolCallModal({ serverName, tool, onCancel, onInvoke }: ToolCallModalProps) {
  // input_schema приходит как Record<string, unknown> (McpTool из API) —
  // сужаем к форме JSON schema одним кастом (структуру задаёт бэкенд)
  const schema = (tool.input_schema ?? {}) as InputSchema
  const props = schema.properties ?? {}
  const required = schema.required ?? []
  const entries = Object.entries(props)

  const [values, setValues] = useState<Record<string, string | boolean>>(() => {
    const init: Record<string, string | boolean> = {}
    for (const [name, prop] of entries) init[name] = prop?.type === 'boolean' ? false : ''
    return init
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Собрать args из полей: number→Number, checkbox→bool, textarea→JSON.parse,
  // остальное→String. Пустой textarea (array/object) — поле пропускается.
  const collect = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {}
    for (const [name, prop] of entries) {
      const raw = values[name]
      const t = prop?.type
      if (t === 'boolean') {
        out[name] = Boolean(raw)
      } else if (t === 'number' || t === 'integer') {
        const s = typeof raw === 'string' ? raw : ''
        out[name] = s === '' ? 0 : Number(s)
      } else if (t === 'array' || t === 'object') {
        const s = typeof raw === 'string' ? raw : ''
        if (s.trim() === '') continue
        try {
          out[name] = JSON.parse(s)
        } catch {
          throw new Error(`Поле «${name}»: некорректный JSON`)
        }
      } else {
        out[name] = String(typeof raw === 'string' ? raw : '')
      }
    }
    return out
  }

  const invoke = async () => {
    setBusy(true)
    setError(null)
    try {
      const args = collect() // может бросить на битом JSON
      await onInvoke(args) // на успехе родитель закроет модалку
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <Modal title={`MCP ${serverName}/${tool.name}`} onClose={onCancel}>
      {tool.description && <p className="tool-modal-desc">{tool.description}</p>}
      {entries.length === 0 ? (
        <p className="tool-modal-note">Аргументы не требуются</p>
      ) : (
        <div className="tool-modal-form">
          {entries.map(([name, prop]) => {
            const isCheck = prop?.type === 'boolean'
            const isArea = prop?.type === 'array' || prop?.type === 'object'
            const isNumber = prop?.type === 'number' || prop?.type === 'integer'
            const val = typeof values[name] === 'string' ? values[name] : ''
            return (
              <label className="tool-modal-field" key={name}>
                <span className="tool-modal-label">
                  {name}
                  {required.includes(name) ? ' *' : ''}
                </span>
                {isCheck ? (
                  <input
                    type="checkbox"
                    checked={Boolean(values[name])}
                    onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.checked }))}
                  />
                ) : isArea ? (
                  <textarea
                    className="input tool-modal-json"
                    rows={3}
                    placeholder="JSON"
                    value={val}
                    onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.value }))}
                  />
                ) : (
                  <input
                    className="input"
                    type={isNumber ? 'number' : 'text'}
                    value={val}
                    onChange={(e) => setValues((v) => ({ ...v, [name]: e.target.value }))}
                  />
                )}
                {prop?.description && <span className="tool-modal-hint">{prop.description}</span>}
              </label>
            )
          })}
        </div>
      )}
      {error && <p className="tool-modal-error">{error}</p>}
      <div className="modal-inline-actions">
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>
          Отмена
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => void invoke()}>
          {busy ? 'Вызываем…' : 'Вызвать'}
        </button>
      </div>
    </Modal>
  )
}
