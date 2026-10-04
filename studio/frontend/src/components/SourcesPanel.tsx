// Панель «Источники и цитаты» (день 24, задача 3): мокап B.
// Кадры B-1 — карточки источников (номер 1..N, file · section, score-pill,
// «из #N» (stage1_rank) при реранке, цитата в «»); B-2 — красная карточка
// «🚫 Не знаю» (слабый контекст: ни один чанк не прошёл порог).
// Панель автономна от текста сообщения: [n]-маркеры в ответе не рендерит.
// Шкала score — паттерн RagContextInspector/KbTab/FlowInspector:
// зелёный ≥0.8, жёлтый 0.5–0.8, красный <0.5.
import type { RagContext } from '../api'

// Русское склонение «источник/источника/источников» (паттерн FlowInspector)
function pluralSources(n: number): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return 'источник'
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return 'источника'
  return 'источников'
}

// Цветовая шкала score (паттерн RagContextInspector/KbTab):
// зелёный ≥0.8, жёлтый 0.5–0.8, красный <0.5
function scoreClass(score: number): string {
  if (score >= 0.8) return 'src-score-pill ok'
  if (score >= 0.5) return 'src-score-pill warn'
  return 'src-score-pill bad'
}

export default function SourcesPanel({
  ragContext,
  minScore,
}: {
  ragContext: RagContext
  minScore?: number
}) {
  // Старые/чужие сообщения: поля могут отсутствовать — без краха
  const chunks = ragContext?.chunks ?? []
  const hasMinScore = minScore != null && minScore > 0

  // B-2: слабый контекст — бэкенд пометил ответ dont_know (A′)
  if (ragContext?.dont_know === true) {
    const reason = hasMinScore
      ? `порог ≥ ${minScore} — ни один чанк не прошёл`
      : 'в базе не нашлось релевантных материалов'
    return (
      <div className="src-dontknow">
        <div className="src-dk-head">
          <span className="src-dk-title">🚫 Не знаю</span>
          <span className="src-dk-badge">
            {chunks.length} ЧАНКОВ{hasMinScore ? ' ПОСЛЕ ПОРОГА' : ''}
          </span>
        </div>
        <div className="src-dk-body">{reason}</div>
        <div className="src-dk-sub">Уточните вопрос: о каком документе вы спрашиваете?</div>
      </div>
    )
  }

  if (chunks.length === 0) return null

  return (
    <div className="src-panel">
      <div className="src-head">
        <span className="src-head-title">📖 ИСТОЧНИКИ И ЦИТАТЫ</span>
        <span className="src-head-meta">
          {chunks.length} {pluralSources(chunks.length)}
          {ragContext.reranked ? ' · reranked' : ''}
        </span>
      </div>
      <div className="src-cards">
        {chunks.map((c, i) => {
          // «из #N» — только при реранке и с позиции этапа 1 (narrow: null-safe)
          const fromRank = c.reranked && c.stage1_rank != null ? c.stage1_rank : null
          const alert = fromRank != null && fromRank > 20
          return (
            <div key={c.chunk_id ?? c.rank} className="src-card">
              <div className="src-card-top">
                <span className="src-num">{i + 1}</span>
                <span className="src-file">
                  {c.file}
                  {c.section ? ` · ${c.section}` : ''}
                </span>
                <span className={scoreClass(c.score)}>{c.score.toFixed(2)}</span>
                {fromRank != null && (
                  <span
                    className={alert ? 'src-from src-alert' : 'src-from'}
                    title={`Реранкер поднял с позиции этапа 1 #${fromRank}`}
                  >
                    {alert ? '🚨 ' : ''}
                    {`из #${fromRank}`}
                  </span>
                )}
              </div>
              {c.chunk_id ? <div className="src-id">{c.chunk_id}</div> : null}
              <div className="src-quote">
                <span className="src-quote-gq src-quote-gq-open">«</span>
                {c.text}
                <span className="src-quote-gq src-quote-gq-close">»</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
