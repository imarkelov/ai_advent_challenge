import { useState } from 'react'
import type { PendingQuestion } from '../state'

export default function QuestionModal({
  question,
  onSubmit,
}: {
  question: PendingQuestion
  onSubmit: (text: string) => void
}) {
  const [answer, setAnswer] = useState('')

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>Вопрос от {question.role}</h2>
        <p>{question.text}</p>
        {question.options.map((option, index) => (
          <div key={option} className="row">
            <button onClick={() => onSubmit(option)}>{index + 1}. {option}</button>
          </div>
        ))}
        <textarea value={answer} onChange={(event) => setAnswer(event.target.value)}
                  placeholder="Свободный ответ" />
        <div className="row" style={{ marginTop: 8 }}>
          <button onClick={() => onSubmit(answer)} disabled={!answer.trim()}>Ответить</button>
        </div>
      </div>
    </div>
  )
}
