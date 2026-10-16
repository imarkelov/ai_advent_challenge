// Вкладка «Ассистент» (дни 30–34): единый AI-ассистент разработчика и
// поддержки. Четыре секции по модулям задания:
//   А. Вопрос по проекту — RAG по документации и коду, ответ + источники;
//   Б. Ревью изменений — diff → находки по severity с файлом и строкой;
//   В. Тикеты — персонализированный ответ по данным CRM;
//   Г. Сценарии — автономная работа с файлами по цели.
import { useCallback, useEffect, useState } from 'react'
import {
  apiAssistantAsk,
  apiAssistantGit,
  apiAssistantIndex,
  apiAssistantReview,
  apiAssistantScenario,
  apiAssistantScenarios,
  apiAssistantStatus,
  apiAssistantSupport,
  apiAssistantTickets,
  type AssistantAsk,
  type AssistantGit,
  type AssistantReview,
  type AssistantScenario,
  type AssistantScenarioResult,
  type AssistantStatus,
  type AssistantSupport,
  type AssistantTicket,
  type AssistantTickets,
} from '../api'

const SEVERITY_LABEL: Record<string, string> = {
  high: 'критично',
  medium: 'средне',
  low: 'низко',
}

// SPSC: строковый параметр сценария → значение по умолчанию.
const DEFAULT_PARAMS: Record<string, string> = {
  symbol: '',
  path: '',
  kind: 'adr',
  title: '',
}

export default function AssistantTab() {
  const [status, setStatus] = useState<AssistantStatus | null>(null)
  const [git, setGit] = useState<AssistantGit | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  // А. Вопрос по проекту
  const [question, setQuestion] = useState('')
  const [ask, setAsk] = useState<AssistantAsk | null>(null)

  // Б. Ревью
  const [diff, setDiff] = useState('')
  const [review, setReview] = useState<AssistantReview | null>(null)

  // В. Тикеты
  const [tickets, setTickets] = useState<AssistantTickets | null>(null)
  const [ticket, setTicket] = useState<AssistantTicket | null>(null)
  const [support, setSupport] = useState<AssistantSupport | null>(null)

  // Г. Сценарии
  const [scenarios, setScenarios] = useState<AssistantScenario[]>([])
  const [scenario, setScenario] = useState('find_usages')
  const [params, setParams] = useState<Record<string, string>>({ ...DEFAULT_PARAMS })
  const [result, setResult] = useState<AssistantScenarioResult | null>(null)

  const refresh = useCallback(async () => {
    const [s, g, t, sc] = await Promise.all([
      apiAssistantStatus().catch(() => null),
      apiAssistantGit().catch(() => null),
      apiAssistantTickets().catch(() => null),
      apiAssistantScenarios().catch(() => ({ scenarios: [] })),
    ])
    setStatus(s)
    setGit(g)
    setTickets(t)
    setScenarios(sc.scenarios)
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  function fail(e: unknown, fallback: string) {
    setErr(e instanceof Error ? e.message : fallback)
  }

  async function buildIndex() {
    if (busy) return
    setBusy('index')
    setErr(null)
    try {
      await apiAssistantIndex()
      await refresh()
    } catch (e) {
      fail(e, 'Не удалось собрать индекс')
    } finally {
      setBusy(null)
    }
  }

  async function runAsk() {
    const q = question.trim()
    if (!q || busy) return
    setBusy('ask')
    setErr(null)
    try {
      setAsk(await apiAssistantAsk(q))
    } catch (e) {
      fail(e, 'Не удалось получить ответ')
    } finally {
      setBusy(null)
    }
  }

  async function runReview() {
    if (busy) return
    setBusy('review')
    setErr(null)
    // diff текстом; пустая строка → ревью изменений рабочего дерева (git).
    try {
      const body = diff.trim() ? { diff } : {}
      setReview(await apiAssistantReview(body))
    } catch (e) {
      fail(e, 'Не удалось выполнить ревью')
    } finally {
      setBusy(null)
    }
  }

  async function runSupport(t: AssistantTicket) {
    if (busy) return
    setBusy('support')
    setErr(null)
    setTicket(t)
    try {
      setSupport(await apiAssistantSupport(t.id))
    } catch (e) {
      fail(e, 'Не удалось получить ответ по тикету')
    } finally {
      setBusy(null)
    }
  }

  async function runScenario() {
    if (busy) return
    setBusy('scenario')
    setErr(null)
    // Передаём только заполненные параметры — пустые бэкенд трактует
    // как «не задано» (сценарий подставит своё).
    const body: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(params)) {
      const t = (v ?? '').trim()
      if (t) body[k] = t
    }
    try {
      setResult(await apiAssistantScenario(scenario, body))
      await refresh()
    } catch (e) {
      fail(e, 'Не удалось выполнить сценарий')
    } finally {
      setBusy(null)
    }
  }

  const current = scenarios.find((s) => s.name === scenario)

  return (
    <div className="series-tab">
      {err && <div className="series-error">{err}</div>}

      {/* Состояние ассистента */}
      <div className="series-block">
        <span className="series-label">Состояние</span>
        <div className="series-hint">
          {status ? (
            <>
              корпус проекта: {status.corpus.files} файл(ов)
              {status.indexed
                ? ` · индекс: ${status.documents} док. / ${status.chunks} чанков (${status.embedder ?? '—'})`
                : ' · индекс НЕ построен'}
              <br />
              LLM: {status.llm_available ? 'подключена' : 'недоступна (ответы в детерминированном режиме)'}
              {' · '}CRM: {status.crm.tickets} тикет(ов), {status.crm.users} польз.
              {' · '}git: {status.git.available ? `${status.git.branch ?? '—'}, изменений ${status.git.changed}` : 'нет'}
            </>
          ) : (
            'загрузка…'
          )}
        </div>
        <div className="series-row">
          <button
            type="button"
            className="btn"
            onClick={() => void buildIndex()}
            disabled={busy !== null}
          >
            {busy === 'index' ? 'Собираю…' : 'Собрать индекс проекта'}
          </button>
        </div>
      </div>

      {/* А. Вопрос по проекту */}
      <div className="series-block">
        <span className="series-label">А. Вопрос по проекту (RAG по документации и коду)</span>
        <div className="series-row">
          <input
            className="series-input"
            placeholder="например: как устроен шлюз локальной LLM?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void runAsk()
            }}
            aria-label="Вопрос по проекту"
          />
          <button
            type="button"
            className="btn"
            onClick={() => void runAsk()}
            disabled={busy !== null || !question.trim()}
          >
            {busy === 'ask' ? '…' : 'Спросить'}
          </button>
        </div>
        {ask && (
          <div className="series-hint">
            <strong>
              {ask.found ? (ask.llm ? 'Ответ модели' : 'Ответ по найденному (без LLM)') : 'Не найдено'}
            </strong>
            {!ask.llm && ask.llm_error && (
              <div className="assistant-warn">
                LLM недоступна: {ask.llm_error}
              </div>
            )}
            <div className="assistant-answer">{ask.answer}</div>
            {ask.sources.length > 0 && (
              <>
                <span className="series-label">Источники ({ask.sources.length})</span>
                <ul className="assistant-sources">
                  {ask.sources.map((s, i) => (
                    <li key={`${s.file}-${i}`}>
                      <code>{s.file}</code>
                      {s.section ? ` · ${s.section}` : ''}
                      {typeof s.score === 'number' ? ` · ${s.score.toFixed(3)}` : ''}
                      <div className="assistant-quote">{s.quote}</div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>

      {/* Б. Ревью изменений */}
      <div className="series-block">
        <span className="series-label">Б. Ревью изменений (diff или изменения в git)</span>
        <textarea
          className="series-input assistant-textarea"
          placeholder="Вставьте unified diff (git diff) или оставьте пустым — возьму изменения рабочего дерева"
          value={diff}
          onChange={(e) => setDiff(e.target.value)}
          rows={4}
          aria-label="Diff для ревью"
        />
        <div className="series-row">
          <button
            type="button"
            className="btn"
            onClick={() => void runReview()}
            disabled={busy !== null}
          >
            {busy === 'review' ? 'Проверяю…' : 'Проверить'}
          </button>
        </div>
        {review && (
          <div className="series-hint">
            источник: {review.source} · файлов {review.summary.files} ·{' '}
            <span className="assistant-add">+{review.summary.added}</span>{' '}
            <span className="assistant-del">−{review.summary.removed}</span> · находок{' '}
            {review.summary.findings} (критично {review.summary.by_severity.high}, средне{' '}
            {review.summary.by_severity.medium}, низко {review.summary.by_severity.low})
            {review.findings.length > 0 && (
              <ul className="assistant-findings">
                {review.findings.map((f, i) => (
                  <li key={`${f.rule}-${i}`} className={`sev-${f.severity}`}>
                    <span className="assistant-sev">{SEVERITY_LABEL[f.severity] ?? f.severity}</span>{' '}
                    <code>{f.file ?? '—'}</code>
                    {f.line ? `:${f.line}` : ''} — {f.message}
                  </li>
                ))}
              </ul>
            )}
            {review.recommendations.length > 0 && (
              <>
                <span className="series-label">Рекомендации</span>
                <ul className="assistant-sources">
                  {review.recommendations.map((r) => (
                    <li key={r.rule}>{r.advice}</li>
                  ))}
                </ul>
              </>
            )}
            {review.llm_note && (
              <>
                <span className="series-label">Замечания модели</span>
                <div className="assistant-answer">{review.llm_note}</div>
              </>
            )}
            {review.affected_docs.length > 0 && (
              <>
                <span className="series-label">Затронутая документация</span>
                <ul className="assistant-sources">
                  {review.affected_docs.map((d, i) => (
                    <li key={`${d.file}-${i}`}>
                      <code>{d.file}</code>
                      {d.section ? ` · ${d.section}` : ''}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>

      {/* В. Тикеты и поддержка */}
      <div className="series-block">
        <span className="series-label">
          В. Тикеты и поддержка (CRM: {tickets?.stats.users ?? 0} польз., {tickets?.stats.tickets ?? 0} тикетов)
        </span>
        {tickets && tickets.tickets.length > 0 ? (
          <table className="series-table">
            <thead>
              <tr>
                <th>Тикет</th>
                <th>Тема</th>
                <th>Тип</th>
                <th>Приоритет</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tickets.tickets.map((t) => (
                <tr key={t.id} className={ticket?.id === t.id ? 'active' : ''}>
                  <td>
                    <code>{t.id}</code>
                  </td>
                  <td>{t.subject}</td>
                  <td>{t.topic}</td>
                  <td>{t.priority}</td>
                  <td>
                    <button
                      type="button"
                      className="btn-icon"
                      title="Ответить с учётом CRM"
                      onClick={() => void runSupport(t)}
                      disabled={busy !== null}
                    >
                      →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="series-hint">тикетов нет (проверьте data/crm/tickets.json)</div>
        )}
        {support && (
          <div className="series-hint">
            <strong>
              {support.found ? `Тикет ${support.ticket_id}` : 'Тикет не найден — общий ответ'}
              {support.llm ? ' · ответ модели' : ' · ответ по правилам'}
            </strong>
            {!support.llm && support.llm_error && (
              <div className="assistant-warn">LLM недоступна: {support.llm_error}</div>
            )}
            <div className="assistant-answer">{support.answer}</div>
            {support.context.applied.length > 0 && (
              <>
                <span className="series-label">Учтено из CRM</span>
                <ul className="assistant-sources">
                  {support.context.applied.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>

      {/* Г. Сценарии */}
      <div className="series-block">
        <span className="series-label">Г. Сценарии (автономная работа с файлами по цели)</span>
        <div className="series-row">
          <select
            className="series-input"
            value={scenario}
            onChange={(e) => {
              setScenario(e.target.value)
              setResult(null)
            }}
            aria-label="Сценарий"
          >
            {scenarios.map((s) => (
              <option key={s.name} value={s.name}>
                {s.title}
              </option>
            ))}
          </select>
        </div>
        {current && <div className="series-hint">{current.description}</div>}
        {current?.params.map((p) => (
          <div className="series-row" key={p}>
            <input
              className="series-input"
              placeholder={p}
              value={params[p] ?? ''}
              onChange={(e) => setParams((prev) => ({ ...prev, [p]: e.target.value }))}
              aria-label={`Параметр ${p}`}
            />
          </div>
        ))}
        <div className="series-row">
          <button
            type="button"
            className="btn"
            onClick={() => void runScenario()}
            disabled={busy !== null}
          >
            {busy === 'scenario' ? 'Выполняю…' : 'Выполнить'}
          </button>
        </div>
        {result && (
          <div className="series-hint">
            <strong>{result.summary}</strong>
            {result.changed_files.length > 0 && (
              <>
                <span className="series-label">Изменённые файлы</span>
                <ul className="assistant-sources">
                  {result.changed_files.map((f) => (
                    <li key={f}>
                      <code>{f}</code>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {result.diff && (
              <pre className="assistant-diff">{result.diff.slice(0, 4000)}</pre>
            )}
          </div>
        )}
      </div>

      {/* Git-контекст */}
      <div className="series-block">
        <span className="series-label">Git-контекст</span>
        <div className="series-hint">
          {git?.available ? (
            <>
              ветка <code>{git.branch}</code> · изменённых файлов{' '}
              {git.tracked.length + git.untracked.length} · строк diff {git.diff_lines}
              {git.tracked.length + git.untracked.length > 0 && (
                <ul className="assistant-sources">
                  {[...git.tracked, ...git.untracked].slice(0, 15).map((f) => (
                    <li key={f}>
                      <code>{f}</code>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            'git недоступен'
          )}
        </div>
      </div>
    </div>
  )
}
