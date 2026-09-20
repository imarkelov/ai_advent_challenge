// Вкладка «Профили» (день 12): профиль пользователя активного диалога —
// 4 текстовых поля (имя, роль/сфера, тон/стиль, стоп-слова) + статус-чип
// (pending/active/declined) + действия: «Сохранить» → POST /api/profile,
// «Провести интервью» / «Заполнить заново» / «Отказаться» →
// POST /api/profile/action. После каждого ответа профиль обновляется
// через setProfile (реducer 'profile-set'), поля синхронизируются за ним.
import { useId, useState } from 'react'
import { apiPostProfile, apiPostProfileAction, type UserProfile } from '../api'
import { useStudio } from '../state'

// Inline-SVG иконки 15px (stroke: currentColor) — без icon-библиотек (паттерн Sidebar)
function SaveIcon() {
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
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" />
      <path d="M17 21v-8H7v8" />
      <path d="M7 3v5h8" />
    </svg>
  )
}

function MicIcon() {
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
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <path d="M12 19v3" />
    </svg>
  )
}

function ResetIcon() {
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
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </svg>
  )
}

function XIcon() {
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
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  )
}

// Чип статуса профиля (pending — янтарный, active — зелёный, declined — приглушённый)
function StatusChip({ status }: { status: UserProfile['status'] }) {
  const label =
    status === 'active' ? 'активен' : status === 'declined' ? 'отказан' : 'ожидает заполнения'
  return <span className={`profile-chip ${status}`}>{label}</span>
}

// Текстовое поле профиля: RU-подпись над input (label — для getByLabelText)
function Field({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string
  placeholder: string
  value: string
  onChange: (v: string) => void
}) {
  const id = useId()
  return (
    <div className="profile-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="text"
        className="input"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

export default function ProfileTab() {
  const { state, activeProfile, setProfile } = useStudio()
  const dialogueId = state.activeId
  const p = activeProfile
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState('')

  // Ключ источника профиля: меняется при смене диалога или при смене
  // полей/статуса (ответы API после save/reset/decline, перечитанный
  // список диалогов после интервью в чате).
  const profileKey = p
    ? `${dialogueId}|${p.status}|${p.name}|${p.role}|${p.tone}|${p.taboos}`
    : ''

  // Черновик незаполненных в API полей. Сбрасывается синхронно в момент
  // рендера, когда источник профиля изменился (не через пассивный effect —
  // поля не отстают от обновления профиля и не «сбрасывают» введённое
  // между событиями ввода; паттерн «adjusting state during render»).
  const [draft, setDraft] = useState<{
    name: string
    role: string
    tone: string
    taboos: string
  } | null>(null)
  const [draftKey, setDraftKey] = useState(profileKey)
  if (draftKey !== profileKey) {
    setDraftKey(profileKey)
    if (draft !== null) setDraft(null)
    if (hint !== '') setHint('')
  }

  const values =
    draft ?? {
      name: p?.name ?? '',
      role: p?.role ?? '',
      tone: p?.tone ?? '',
      taboos: p?.taboos ?? '',
    }

  if (!dialogueId || !p) {
    return (
      <div className="ctx-sections">
        <div className="kv-empty">Нет активного диалога — создайте новый, чтобы заполнить профиль.</div>
      </div>
    )
  }

  // Сохранить 4 поля: POST /api/profile → обновить профиль диалога
  const save = () => {
    void (async () => {
      setBusy(true)
      try {
        const { profile } = await apiPostProfile(dialogueId, values.name, values.role, values.tone, values.taboos)
        setProfile(dialogueId, profile)
      } catch (err) {
        console.error('profile save:', err)
      } finally {
        setBusy(false)
      }
    })()
  }

  // Действие с профилем: interview / reset / decline → POST /api/profile/action
  const doAction = (a: 'interview' | 'decline' | 'reset') => {
    void (async () => {
      setBusy(true)
      try {
        const { profile } = await apiPostProfileAction(dialogueId, a)
        setProfile(dialogueId, profile)
        if (a === 'interview') setHint('Напишите «интервью» в чате, чтобы начать')
      } catch (err) {
        console.error('profile action:', err)
      } finally {
        setBusy(false)
      }
    })()
  }

  return (
    <div className="ctx-sections">
      <section className="ctx-card" title="Профиль пользователя">
        <header className="ctx-card-head">
          <h3>Профиль пользователя</h3>
          <StatusChip status={p.status} />
        </header>
        <Field label="Имя пользователя" placeholder="как к вам обращаться" value={values.name} onChange={(v) => setDraft({ ...values, name: v })} />
        <Field label="Роль и сфера" placeholder="профессия, область работы" value={values.role} onChange={(v) => setDraft({ ...values, role: v })} />
        <Field label="Тон и стиль общения" placeholder="например: кратко и по делу" value={values.tone} onChange={(v) => setDraft({ ...values, tone: v })} />
        <Field label="Стоп-слова / табу" placeholder="слова или темы, которых избегать" value={values.taboos} onChange={(v) => setDraft({ ...values, taboos: v })} />
        <div className="ctx-toolbar">
          <button type="button" className="btn btn-icon" title="Сохранить" aria-label="Сохранить" disabled={busy} onClick={save}>
            <SaveIcon />
          </button>
          <button
            type="button"
            className="btn btn-icon"
            title="Провести интервью"
            aria-label="Провести интервью"
            disabled={busy}
            onClick={() => doAction('interview')}
          >
            <MicIcon />
          </button>
          <button
            type="button"
            className="btn btn-icon"
            title="Заполнить заново"
            aria-label="Заполнить заново"
            disabled={busy}
            onClick={() => doAction('reset')}
          >
            <ResetIcon />
          </button>
          <button
            type="button"
            className="btn btn-clear btn-icon"
            title="Отказаться"
            aria-label="Отказаться"
            disabled={busy}
            onClick={() => doAction('decline')}
          >
            <XIcon />
          </button>
        </div>
        {hint && <p className="profile-hint">{hint}</p>}
      </section>
    </div>
  )
}
