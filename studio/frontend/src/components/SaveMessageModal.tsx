// Модалка «Сохранить в память»: текст сообщения (редактируемый) + key +
// выбор слоя. Слой «Диалог (ST)» в select намеренно отсутствует: ST — сам
// диалог, key/value туда не кладутся. Сохранить → POST /api/memory/{layer}
// {key, value} → refreshMemory → onClose.
import { useState } from 'react'
import { apiPost } from '../api'
import { useStudio } from '../state'
import Modal from './Modal'

export default function SaveMessageModal({
  message,
  onClose,
}: {
  message: string
  onClose: () => void
}) {
  const { refreshMemory } = useStudio()
  const [text, setText] = useState(message)
  const [key, setKey] = useState('')
  const [layer, setLayer] = useState<'working' | 'longterm'>('working')

  const canSave = text.trim().length > 0 && key.trim().length > 0

  const save = async () => {
    if (!canSave) return
    try {
      await apiPost(`/memory/${layer}`, { key: key.trim(), value: text })
      await refreshMemory()
      onClose()
    } catch (err) {
      console.error('save to memory:', err)
    }
  }

  return (
    <Modal title="Сохранить в память" onClose={onClose}>
      <textarea
        className="input"
        rows={6}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <input
        className="input"
        placeholder="key (например, ТЗ)"
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />
      <select
        className="input"
        value={layer}
        onChange={(e) => setLayer(e.target.value as 'working' | 'longterm')}
      >
        <option value="working">Текущая задача (Working Memory)</option>
        <option value="longterm">Долговременная (Long-Term Memory)</option>
      </select>
      <div className="modal-inline-actions">
        <button type="button" className="btn" onClick={onClose}>
          Отмена
        </button>
        <button type="button" className="btn" disabled={!canSave} onClick={() => void save()}>
          Сохранить
        </button>
      </div>
    </Modal>
  )
}
