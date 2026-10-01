// Токен-гейдж в шапке чата: SVG-кольцо заполнения лимита контекста
// (total последнего запроса / context_limit — та же семантика, что
// progress-бар во вкладке «Токены») + компактное число и tooltip с
// полной токен-статистикой текущего диалога (по :hover, чистый CSS).
// Данные — только из state useStudio (memory/tokens/config), без
// новых API-вызовов.
import { useStudio } from '../state'

// Компактная форма числа токенов: ≥1000 — «12.4k», иначе как есть;
// null/undefined — «—»
function shortTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1000) {
    return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
  }
  return String(n)
}

// Кольцо 28px: радиус 11 (stroke 3 вписывается в viewBox)
const RADIUS = 11
const CIRC = 2 * Math.PI * RADIUS

export default function TokenGauge() {
  const { state } = useStudio()
  const t = state.tokens
  const m = state.memory
  const limit = t?.context_limit ?? 0
  const lastTotal = t?.last?.total ?? null
  // Заполнение: тот же расчёт, что progress-бар в TokensTab
  const pct = limit > 0 && lastTotal != null ? Math.min(1, lastTotal / limit) : 0
  const warn = pct > 0.9
  const last = t?.last

  return (
    <span
      className={`token-gauge${warn ? ' warn' : ''}`}
      role="progressbar"
      aria-label="Лимит контекста"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct * 100)}
    >
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
        <circle className="token-gauge-track" cx="14" cy="14" r={RADIUS} />
        <circle
          className="token-gauge-fill"
          cx="14"
          cy="14"
          r={RADIUS}
          strokeDasharray={CIRC}
          strokeDashoffset={CIRC * (1 - pct)}
        />
      </svg>
      <span className="token-gauge-num">{shortTokens(lastTotal)}</span>
      <div className="token-gauge-tip" role="tooltip">
        <div className="tok-row">
          <span className="tok-label">Диалог (оценка)</span>
          <span className="tok-value">
            {m
              ? `${m.dialogue.tokens_est} · ${m.dialogue.message_count} сообщений`
              : '—'}
          </span>
        </div>
        <div className="tok-row">
          <span className="tok-label">Последний запрос</span>
          <span className="tok-value">
            {last
              ? `prompt ${last.prompt} / reasoning ${last.reasoning} / total ${last.total}`
              : '—'}
          </span>
        </div>
        <div className="tok-row">
          <span className="tok-label">За сессию</span>
          <span className="tok-value">
            {t
              ? `prompt ${t.session.prompt} / completion ${t.session.completion} / total ${t.session.total}`
              : '—'}
          </span>
        </div>
        <div className="tok-row">
          <span className="tok-label">Лимит контекста</span>
          <span className="tok-value">{lastTotal ?? '—'} / {limit > 0 ? limit : '—'}</span>
        </div>
        <div className="tok-row">
          <span className="tok-label">Модель</span>
          <span className="tok-value">{state.config?.model ?? '—'}</span>
        </div>
      </div>
    </span>
  )
}
