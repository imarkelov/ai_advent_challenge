// Вкладка «Сериалы» (день 29): поиск сериалов оптимизированной локальной
// LLM. Показывает профиль (temperature / max tokens / context window),
// каталог, найденные сериалы с «почему подходит» и метрики ответа
// (латентность, токены/с, верность каталогу, соблюдение ограничений).
import { useEffect, useState } from 'react'
import {
  apiSeriesCatalog,
  apiSeriesCompare,
  apiSeriesSearch,
  apiSetTuningProfile,
  apiTuningProfiles,
  type SeriesCatalog,
  type SeriesCompareResult,
  type SeriesItem,
  type SeriesResult,
  type TuningProfiles,
} from '../api'

const SUGGESTIONS = [
  'Посоветуй мрачный криминальный сериал после 2018 года',
  'Хочу комедию из США 2020 года или новее',
  'российская драма не старше 2022',
  'британский детектив после 2016',
]

function pct(x?: number): string {
  return x == null ? '—' : `${Math.round(x * 100)}%`
}

export default function SeriesTab() {
  const [profiles, setProfiles] = useState<TuningProfiles | null>(null)
  const [catalog, setCatalog] = useState<SeriesCatalog | null>(null)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<SeriesResult | null>(null)
  const [compare, setCompare] = useState<SeriesCompareResult | null>(null)
  const [showPrompt, setShowPrompt] = useState(false)

  useEffect(() => {
    apiTuningProfiles().then(setProfiles).catch(() => setProfiles(null))
    apiSeriesCatalog().then(setCatalog).catch(() => setCatalog(null))
  }, [])

  const active = profiles?.active ?? 'studio'

  async function pickProfile(name: string) {
    try {
      const r = await apiSetTuningProfile(name)
      setProfiles((p) => (p ? { ...p, active: r.active } : p))
      setErr(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось выбрать профиль')
    }
  }

  async function run(q?: string) {
    const text = (q ?? query).trim()
    if (!text || busy) return
    setBusy(true)
    setErr(null)
    setCompare(null)
    try {
      const r = await apiSeriesSearch(text, active)
      setResult(r)
      setQuery(text)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Ошибка поиска')
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  async function runCompare() {
    const text = query.trim()
    if (!text || busy) return
    setBusy(true)
    setErr(null)
    try {
      const r = await apiSeriesCompare([text], ['series', 'series_json'])
      setCompare(r)
      setResult(null)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Ошибка сравнения')
    } finally {
      setBusy(false)
    }
  }

  const profile = profiles?.profiles.find((p) => p.name === active)

  return (
    <div className="series-tab">
      <p className="hint">
        Локальная LLM, оптимизированная под подбор сериалов: параметры
        инференса и промпт-шаблон подобраны под задачу, рекомендации
        берутся только из реального каталога.
      </p>

      {/* Профиль */}
      <div className="series-block">
        <label className="series-label" htmlFor="series-profile">
          Профиль оптимизации
        </label>
        <select
          id="series-profile"
          className="series-select"
          value={active}
          onChange={(e) => pickProfile(e.target.value)}
        >
          {(profiles?.profiles ?? []).map((p) => (
            <option key={p.name} value={p.name}>
              {p.title}
            </option>
          ))}
        </select>
        {profile && (
          <>
            <p className="series-profile-desc">{profile.description}</p>
            <div className="series-params">
              <span className="series-chip">
                temperature <b>{profile.temperature}</b>
              </span>
              <span className="series-chip">
                max tokens <b>{profile.num_predict}</b>
              </span>
              <span className="series-chip">
                context window{' '}
                <b>{profile.num_ctx == null ? 'по умолчанию' : profile.num_ctx}</b>
              </span>
              {profile.seed != null && (
                <span className="series-chip">
                  seed <b>{profile.seed}</b>
                </span>
              )}
              <span className="series-chip">
                JSON-режим <b>{profile.format_json ? 'да' : 'нет'}</b>
              </span>
              {profile.think === false && (
                <span className="series-chip">
                  размышление <b>выключено</b>
                </span>
              )}
            </div>
            <button
              type="button"
              className="series-link"
              onClick={() => setShowPrompt((v) => !v)}
            >
              {showPrompt ? 'Скрыть промпт-шаблон' : 'Показать промпт-шаблон'}
            </button>
            {showPrompt && (
              <pre className="series-prompt">{profile.system_prompt}</pre>
            )}
            {profile.notes && <p className="series-note-sm">{profile.notes}</p>}
          </>
        )}
      </div>

      {/* Каталог */}
      {catalog && (
        <div className="series-block">
          <div className="series-catalog-head">
            <span>
              Каталог: <b>{catalog.stats.total}</b> сериалов
              {catalog.stats.years &&
                `, ${catalog.stats.years[0]}–${catalog.stats.years[1]}`}
            </span>
          </div>
          {!catalog.ready && (
            <p className="series-warn">
              Каталог пуст — соберите его:{' '}
              <code>python scripts/fetch_series.py &amp;&amp; python
              scripts/build_series_catalog.py</code>
            </p>
          )}
          {catalog.ready && (
            <div className="series-genres">
              {Object.entries(catalog.stats.genres)
                .slice(0, 10)
                .map(([g, n]) => (
                  <span key={g} className="series-chip">
                    {g} <b>{n}</b>
                  </span>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Запрос */}
      <div className="series-block">
        <label className="series-label" htmlFor="series-query">
          Что хотите посмотреть?
        </label>
        <div className="series-row">
          <input
            id="series-query"
            className="series-input"
            value={query}
            placeholder="Например: мрачный детектив после 2018"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') run()
            }}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={busy || !query.trim()}
            onClick={() => run()}
          >
            {busy ? '…' : 'Найти'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={busy || !query.trim()}
            title="Сравнить профили series и series_json на этом запросе"
            onClick={runCompare}
          >
            Сравнить
          </button>
        </div>
        <div className="series-suggestions">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              className="series-suggestion"
              onClick={() => run(s)}
              disabled={busy}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {err && <p className="series-error">{err}</p>}

      {/* Результат */}
      {result && (
        <div className="series-block">
          <div className="series-metrics">
            <span className="series-chip">
              профиль <b>{result.profile}</b>
            </span>
            <span className="series-chip">
              модель <b>{result.model ?? '—'}</b>
            </span>
            <span className="series-chip">
              {result.metrics.latency_sec} с
            </span>
            {result.metrics.tokens_per_sec != null && (
              <span className="series-chip">
                {result.metrics.tokens_per_sec} ток/с
              </span>
            )}
            <span className="series-chip">
              верность каталогу{' '}
              <b>{pct(1 - result.metrics.hallucination_rate)}</b>
            </span>
            <span className="series-chip">
              по ограничениям{' '}
              <b>{pct(result.metrics.constraint_satisfaction)}</b>
            </span>
          </div>

          {result.recommendations.length === 0 ? (
            <p className="series-empty">
              Подходящих сериалов в каталоге нет.
              {result.note && ` ${result.note}`}
            </p>
          ) : (
            <ul className="series-list">
              {result.recommendations.map((r: SeriesItem, i) => (
                <li key={`${r.imdb}-${i}`} className="series-card">
                  <div className="series-card-head">
                    <b>{r.title}</b>
                    <span className="series-year">{r.year}</span>
                    {r.satisfies === false && (
                      <span className="series-badge-warn">
                        не по ограничениям
                      </span>
                    )}
                  </div>
                  <div className="series-card-meta">
                    {(r.genres ?? []).join(', ')}
                    {r.country ? ` · ${r.country}` : ''}
                  </div>
                  {r.why && <div className="series-why">{r.why}</div>}
                </li>
              ))}
            </ul>
          )}

          {result.hallucinated.length > 0 && (
            <p className="series-warn">
              Отброшено (нет в каталоге): {result.hallucinated.length}
            </p>
          )}
          {result.note && result.recommendations.length > 0 && (
            <p className="series-note-sm">{result.note}</p>
          )}

          <details className="series-details">
            <summary>
              Запрос к модели ({result.candidates.length} кандидатов)
            </summary>
            <pre className="series-prompt">
              {JSON.stringify(result.request_body, null, 2)}
            </pre>
          </details>
        </div>
      )}

      {/* Сравнение профилей */}
      {compare && (
        <div className="series-block">
          <h3 className="series-h3">Сравнение профилей</h3>
          <table className="series-table">
            <thead>
              <tr>
                <th>профиль</th>
                <th>формат</th>
                <th>по огранич.</th>
                <th>выдумки</th>
                <th>p50, с</th>
                <th>ток/с</th>
                <th>окно</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(compare.profiles).map(([name, m]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{pct(m.format_validity)}</td>
                  <td>{pct(m.constraint_satisfaction)}</td>
                  <td>{pct(m.hallucination_rate)}</td>
                  <td>{m.latency_p50 ?? '—'}</td>
                  <td>{m.tokens_per_sec ?? '—'}</td>
                  <td>{m.num_ctx ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
