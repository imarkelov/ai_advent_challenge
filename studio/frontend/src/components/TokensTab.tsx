// Вкладка «Токены»: последний запрос (prompt/reasoning/total),
// «Лимит контекста» с progress-баром (зелёный, при >90% — акцент),
// итоги за сессию и оценка по слоям памяти.
import { useStudio } from '../state'

function Row({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="tok-row">
      <span className="tok-label">{label}</span>
      <span className="tok-value">{value == null ? '—' : value}</span>
    </div>
  )
}

export default function TokensTab() {
  const { state } = useStudio()
  const t = state.tokens
  const m = state.memory
  const limit = t?.context_limit ?? 0
  const lastTotal = t?.last?.total
  // Заполнение progress-бара: total последнего запроса / лимит контекста
  const pct = limit > 0 && lastTotal != null ? Math.min(100, (lastTotal / limit) * 100) : 0
  const warn = pct > 90

  return (
    <div className="ctx-sections">
      <section className="ctx-card">
        <h3>Последний запрос</h3>
        <Row label="prompt" value={t?.last?.prompt} />
        <Row label="reasoning" value={t?.last?.reasoning} />
        <Row label="total" value={t?.last?.total} />
        <div className="tok-row limit-row">
          <span className="tok-label">Лимит контекста</span>
          <span className="tok-value">
            {lastTotal ?? '—'} / {t ? limit : '—'}
          </span>
        </div>
        <div
          className="progress"
          role="progressbar"
          aria-label="Лимит контекста"
          aria-valuenow={Math.round(pct)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={warn ? 'progress-fill warn' : 'progress-fill'}
            style={{ width: `${pct}%` }}
          />
        </div>
      </section>

      <section className="ctx-card">
        <h3>За сессию</h3>
        <Row label="prompt" value={t?.session?.prompt} />
        <Row label="completion" value={t?.session?.completion} />
        <Row label="total" value={t?.session?.total} />
      </section>

      <section className="ctx-card">
        <h3>По слоям (оценка)</h3>
        <Row label="dialogue" value={m?.dialogue?.tokens_est} />
        <Row label="working" value={m?.working?.tokens_est} />
        <Row label="long_term" value={m?.long_term?.tokens_est} />
      </section>
    </div>
  )
}
