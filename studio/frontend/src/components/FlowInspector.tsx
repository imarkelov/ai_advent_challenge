// Инспектор флоу обработки (ui-rework, задача 3): свёрнутая строка-чип под
// assistant-сообщением «📡 Обработка: RAG N чанков → Prompt ≈X tok →
// LLM {model} → {total} tok» (части без данных — пропускаются, стрелки без
// dangling). Клик — карточка inline: шаги RAG → Prompt-сборка → LLM (с
// пунктом «Запрос JSON»: ленивый fetch GET /api/requests/{id}, тело — поле
// `request` записи журнала) → Ответ (usage). Data-driven: шаг/поле не
// рендерится, если данных нет (task-финал без request_id — строки нет).
// Стили — flow.css (классы .flow-*, токены :root styles.css).
import { useEffect, useRef, useState } from 'react'
import { apiGet, type RagContext } from '../api'
import { useStudio, type Message, type RequestDetail } from '../state'
import './flow.css'

// Оценка токенов (клиентская эвристика, день 8/19): ~0.44 токена/символ
function estChars(chars: number): number {
  return chars > 0 ? Math.max(1, Math.round(chars * 0.44)) : 0
}

// Число в чип: 13338 → «13.3k», 640 → «640»
function fmtTok(n: number): string {
  if (n >= 1000) {
    const k = (n / 1000).toFixed(1)
    return `${k.endsWith('.0') ? k.slice(0, -2) : k}k`
  }
  return String(n)
}

// Русские склонения (чанк/чанка/чанков)
function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

// Цветовая шкала score (паттерн RagContextInspector/KbTab):
// зелёный ≥0.8, жёлтый 0.5–0.8, красный <0.5
function scoreClass(score: number): string {
  if (score >= 0.8) return 'flow-score-g'
  if (score >= 0.5) return 'flow-score-y'
  return 'flow-score-r'
}

// Блоки GET /api/rules (дни 12/14): профиль и инварианты инжектятся в
// system-промпт — для оценок в «Prompt-сборке» нужны их размеры
interface RulesBlocks {
  profile_block?: string
  invariants_block?: string
}

// Чип блока Prompt-сборки (оценка токенов)
interface PromptChip {
  key: string
  label: string
  tokens: number
  cls?: string
}

// ── Шаг RAG (только если rag_context с чанками) ────────────────────────────
function RagStep({ ctx }: { ctx: RagContext }) {
  return (
    <div className="flow-step">
      <span className="flow-step-ic" aria-hidden>🔎</span>
      <div className="flow-step-body">
        <div className="flow-step-title">RAG · retrieval</div>
        <div className="flow-step-detail">
          {ctx.query ? `«${ctx.query}» · ` : ''}
          recall {ctx.recall_total}
          {ctx.reranked ? ' (reranked)' : ''}
        </div>
        <div className="flow-chips">
          {ctx.chunks.map((c) => (
            <span key={c.rank} className="flow-chunk" title={c.text}>
              {c.file}
              {c.section ? ` · ${c.section}` : ''}
              <b className={`flow-score ${scoreClass(c.score)}`}>
                {c.score.toFixed(2)}
              </b>
              {c.stage1_rank != null && c.stage1_rank > 20 && (
                <span
                  className="flow-chunk-from"
                  title={`Реранкер поднял с позиции этапа 1 #${c.stage1_rank}`}
                >
                  из #{c.stage1_rank}
                </span>
              )}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Шаг Prompt-сборка: чипы блоков с оценкой токенов + итог ────────────────
function PromptStep({ chips, total }: { chips: PromptChip[]; total: number }) {
  return (
    <div className="flow-step">
      <span className="flow-step-ic" aria-hidden>🧩</span>
      <div className="flow-step-body">
        <div className="flow-step-title">Prompt-сборка</div>
        <div className="flow-chips">
          {chips.map((c) => (
            <span
              key={c.key}
              className={c.cls ? `flow-pb ${c.cls}` : 'flow-pb'}
              title="Оценка токенов (эвристика 0.44 tok/символ)"
            >
              {c.label} ≈{fmtTok(c.tokens)}
            </span>
          ))}
        </div>
        <div className="flow-step-meta">≈ {total} tok system</div>
      </div>
    </div>
  )
}

// ── Шаг LLM: модель + пункт «Запрос JSON» (ленивый fetch, кэш в состоянии) ─
function LlmStep({ msg }: { msg: Message }) {
  const [jsonOpen, setJsonOpen] = useState(false)
  const [detail, setDetail] = useState<RequestDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const copyTimer = useRef<number | undefined>(undefined)

  // Таймер «Скопировано» не должен дёрнуть компонент после размонтирования
  useEffect(() => () => window.clearTimeout(copyTimer.current), [])

  const toggle = () => {
    if (jsonOpen) {
      setJsonOpen(false)
      return
    }
    setJsonOpen(true)
    // Ленивый fetch — по первому раскрытию; загруженное тело живёт в
    // состоянии (сворачивание не сбрасывает кэш)
    if (detail == null && !loading) {
      setLoading(true)
      setError(null)
      apiGet<RequestDetail>(`/requests/${msg.request_id}`)
        .then(setDetail)
        .catch((err) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() => setLoading(false))
    }
  }

  const jsonText = detail ? JSON.stringify(detail.request, null, 2) : ''

  // Копирование: clipboard API, фолбэк — временный textarea + execCommand
  // (паттерн Modal.tsx); «Скопировано» ~1.5s
  const copy = async () => {
    if (!jsonText) return
    try {
      await navigator.clipboard.writeText(jsonText)
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = jsonText
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      } catch {
        // буфер обмена недоступен — молча
      }
    }
    setCopied(true)
    window.clearTimeout(copyTimer.current)
    copyTimer.current = window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="flow-step">
      <span className="flow-step-ic" aria-hidden>🤖</span>
      <div className="flow-step-body">
        <div className="flow-step-title">LLM</div>
        {msg.model && <div className="flow-step-detail">{msg.model}</div>}
        {msg.request_id != null && (
          <>
            <div className="flow-json">
              <button
                type="button"
                className="flow-json-toggle"
                aria-expanded={jsonOpen}
                title="Тело LLM-запроса из журнала (GET /api/requests/{id})"
                onClick={toggle}
              >
                📄 Запрос JSON · req_{msg.request_id}
              </button>
              {jsonOpen && detail && (
                <button
                  type="button"
                  className={copied ? 'flow-json-copy flow-json-copy--done' : 'flow-json-copy'}
                  onClick={() => void copy()}
                >
                  {copied ? 'Скопировано' : 'Скопировать'}
                </button>
              )}
              <span className="flow-json-arr" aria-hidden>{jsonOpen ? '▾' : '▸'}</span>
            </div>
            {jsonOpen && (
              loading ? (
                <p className="flow-json-wait">Загрузка…</p>
              ) : error ? (
                <p className="flow-json-err">Ошибка: {error}</p>
              ) : detail ? (
                <div className="flow-json-wrap">
                  <pre className="flow-json-pre">{jsonText}</pre>
                  <div className="flow-json-fade" aria-hidden />
                </div>
              ) : null
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Шаг Ответ: usage из сообщения (сырой usage-чанк LLM) ───────────────────
function AnswerStep({ usage }: { usage: Message['usage'] }) {
  const has = usage
    && usage.prompt_tokens != null
    && usage.completion_tokens != null
    && usage.total_tokens != null
  return (
    <div className="flow-step">
      <span className="flow-step-ic" aria-hidden>✅</span>
      <div className="flow-step-body">
        <div className="flow-step-title">Ответ</div>
        <div className="flow-step-meta">
          {has
            ? `prompt ${usage.prompt_tokens} · completion ${usage.completion_tokens} · total ${usage.total_tokens}`
            : '—'}
        </div>
      </div>
    </div>
  )
}

export default function FlowInspector({ msg }: { msg: Message }) {
  const { state } = useStudio()
  const [open, setOpen] = useState(false)

  const ctx = msg.rag_context
  const hasChunks = ctx != null && ctx.chunks.length > 0

  // ── Prompt-сборка: блоки и оценки (data-driven — пустые блоки не чипы) ──
  // Базовый — config.system_prompt; профиль/инварианты — GET /api/rules
  // (лениво при раскрытии, кэш на компонент); память — state.memory
  // (tokens_est бэкенда, фолбэк — эвристика по тексту); RAG-блок — сумма
  // chars чанков.
  const [rules, setRules] = useState<RulesBlocks | null>(null)
  const rulesFetched = useRef(false)
  useEffect(() => {
    if (!open || rulesFetched.current) return
    rulesFetched.current = true
    apiGet<RulesBlocks>('/rules')
      .then(setRules)
      .catch(() => setRules({})) // старый бэкенд без полей — чипы просто пустые
  }, [open])

  const chips: PromptChip[] = []
  if (state.config?.system_prompt) {
    chips.push({ key: 'base', label: 'базовый', tokens: estChars(state.config.system_prompt.length), cls: 'flow-pb--base' })
  }
  if (rules?.profile_block) {
    chips.push({ key: 'profile', label: 'профиль', tokens: estChars(rules.profile_block.length) })
  }
  if (rules?.invariants_block) {
    chips.push({ key: 'inv', label: 'инварианты', tokens: estChars(rules.invariants_block.length) })
  }
  const wm = state.memory?.working
  if (wm && (wm.entries > 0 || wm.tokens_est > 0)) {
    chips.push({ key: 'wm', label: 'память WM', tokens: wm.tokens_est || estChars(Object.values(wm.items).join(' ').length) })
  }
  const lt = state.memory?.long_term
  if (lt && (lt.entries > 0 || lt.tokens_est > 0)) {
    chips.push({ key: 'lt', label: 'память LT', tokens: lt.tokens_est || estChars(Object.values(lt.items).join(' ').length) })
  }
  if (hasChunks) {
    chips.push({
      key: 'rag',
      label: 'RAG-блок',
      tokens: estChars(ctx.chunks.reduce((s, c) => s + c.text.length, 0)),
      cls: 'flow-pb--rag',
    })
  }
  const promptTotal = chips.reduce((s, c) => s + c.tokens, 0)

  // ── Свёрнутая строка: части без данных пропускаются (стрелки без
  //    dangling — сегменты склеиваются « → », пустой список — без хвоста) ──
  const segments: string[] = []
  if (hasChunks) segments.push(`RAG ${ctx.chunks.length} ${plural(ctx.chunks.length, 'чанк', 'чанка', 'чанков')}`)
  if (promptTotal > 0) segments.push(`Prompt ≈${fmtTok(promptTotal)} tok`)
  if (msg.model) segments.push(`LLM ${msg.model}`)
  if (msg.usage?.total_tokens != null) segments.push(`${fmtTok(msg.usage.total_tokens)} tok`)

  if (!open) {
    return (
      <button
        type="button"
        className="flow-row"
        aria-expanded={false}
        title="Раскрыть обработку: RAG → Prompt → LLM → Ответ"
        onClick={() => setOpen(true)}
      >
        <span className="flow-row-ic" aria-hidden>📡</span>
        <span className="flow-row-text">
          Обработка:{segments.length > 0 ? ` ${segments.join(' → ')}` : ''}
        </span>
        <span className="flow-row-arr" aria-hidden>▸</span>
      </button>
    )
  }

  return (
    <div className="flow-card">
      <button
        type="button"
        className="flow-head"
        aria-expanded
        title="Свернуть"
        onClick={() => setOpen(false)}
      >
        <span>📡 Обработка запроса</span>
        <span className="flow-head-arr" aria-hidden>▾</span>
      </button>
      {hasChunks && <RagStep ctx={ctx} />}
      {chips.length > 0 && <PromptStep chips={chips} total={promptTotal} />}
      {(msg.model != null || msg.request_id != null) && <LlmStep msg={msg} />}
      <AnswerStep usage={msg.usage} />
    </div>
  )
}
