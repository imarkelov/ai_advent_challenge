// Модалка «Правила агента»: системный промпт + правило памяти (GET /api/rules).
// Правило памяти помечено активным, когда в памяти есть записи.
import { useEffect, useState } from 'react'
import { apiGet } from '../api'
import Modal from './Modal'

interface Rules {
  system_prompt: string
  memory_rule: string
  rule_active: boolean
}

export default function RulesModal({ onClose }: { onClose: () => void }) {
  const [rules, setRules] = useState<Rules | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<Rules>('/rules')
      .then(setRules)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  return (
    <Modal title="Правила агента" onClose={onClose}>
      {error ? (
        <p className="req-error">Ошибка: {error}</p>
      ) : rules ? (
        <>
          <h3 className="rules-h">Системный промпт</h3>
          <pre className="rules-pre">{rules.system_prompt}</pre>
          <h3 className="rules-h">Правило памяти</h3>
          <p className={rules.rule_active ? 'rules-status rules-status-on' : 'rules-status'}>
            {rules.rule_active
              ? 'Активно — в памяти есть записи'
              : 'Неактивно — записей в памяти нет'}
          </p>
          <pre className="rules-pre">{rules.memory_rule}</pre>
        </>
      ) : (
        <p className="req-empty">Загрузка…</p>
      )}
    </Modal>
  )
}
