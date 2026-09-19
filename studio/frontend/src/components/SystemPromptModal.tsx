// Модалка «Системный промпт агента»: редактируемый textarea + «Сохранить»
// → updateConfig({system_prompt}) (POST /api/config).
import { useState } from 'react'
import { useStudio } from '../state'
import Modal from './Modal'

export default function SystemPromptModal({ onClose }: { onClose: () => void }) {
  const { state, updateConfig } = useStudio()
  const [draft, setDraft] = useState(state.config?.system_prompt ?? '')

  const save = async () => {
    try {
      await updateConfig({ system_prompt: draft })
      onClose()
    } catch (err) {
      console.error('updateConfig:', err)
    }
  }

  return (
    <Modal title="Системный промпт агента" onClose={onClose}>
      <textarea
        className="input prompt-edit"
        rows={8}
        spellCheck={false}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="modal-inline-actions">
        <button type="button" className="btn" onClick={onClose}>
          Отмена
        </button>
        <button type="button" className="btn" disabled={!draft.trim()} onClick={() => void save()}>
          Сохранить
        </button>
      </div>
    </Modal>
  )
}
