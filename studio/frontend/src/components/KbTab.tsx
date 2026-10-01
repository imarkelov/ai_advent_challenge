// Вкладка «База знаний» (день 21): RAG поверх локального индекса документов.
// Двухэтапный поиск: этап 1 — recall (кандидаты, rag_recall), этап 2 — топ-K в
// диалоге (rag_top_k); опциональный реранкер (cross-encoder) пересортирует
// кандидатов этапа 1. Секции: «Включить» (тумблеры RAG/агент-цикл + реранкер
// + этапы 1/2), «Индексация» (стратегия + эмбеддер + «Индексировать»),
// «Файлы» (загрузка), «Статистика», «Сравнение стратегий» (таблица fixed vs
// structural), «Поиск по базе».
// Данные локальны в компоненте (паттерн ProfileTab): при открытии читаем
// GET /api/kb/stats + /api/kb/settings + /api/kb/uploads (список загрузок
// независим от индекса), после каждого действия перечитываем.
import { useEffect, useRef, useState } from 'react'
import {
  apiKbBuildStatus,
  apiKbDeleteUpload,
  apiKbIndex,
  apiKbSearch,
  apiKbSettings,
  apiKbSettingsPost,
  apiKbStats,
  apiKbUpload,
  apiKbUploads,
  apiKbWipe,
  type KbBuildStatus,
  type KbEmbedder,
  type KbReranker,
  type KbSearchResult,
  type KbSettings,
  type KbStats,
  type KbStrategy,
  type KbUpload,
} from '../api'

// Рисунок метрики сравнения: hit@3/precision@3/MRR — 3 знака, длины — целое
const metric3 = (v: number) => v.toFixed(3)
const len0 = (v: number) => String(Math.round(v))

// Размер файла для списка загрузок: КБ или МБ
const humanSize = (bytes: number) =>
  bytes >= 1048576
    ? `${(bytes / 1048576).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(bytes / 1024))} КБ`

// Фаза сборки → подпись для прогресс-бара (RU)
const phaseLabel = (p: KbBuildStatus) => {
  if (p.phase === 'embedding' && p.total > 0)
    return `Эмбеддинги: ${p.done}/${p.total}`
  const labels: Record<string, string> = {
    corpus: 'Корпус…',
    chunking: 'Чанкинг…',
    embedding: 'Эмбеддинги…',
    metrics: 'Метрики (gold-запросы)…',
    save: 'Сохранение индекса…',
    idle: 'Запуск…',
  }
  return labels[p.phase] ?? 'Сборка…'
}

// Общий процент по фазе: embedding — длинная фаза (3–90%), остальное —
// короткие вехи
const progressPct = (p: KbBuildStatus): number => {
  if (!p.running) return 100
  switch (p.phase) {
    case 'corpus': return 0
    case 'chunking': return 3
    case 'embedding':
      return p.total > 0 ? 3 + Math.round(87 * (p.done / p.total)) : 3
    case 'metrics': return 92
    case 'save': return 99
    default: return 0
  }
}

const EMBEDDER_LABEL: Record<KbEmbedder, string> = {
  hash: 'Локальный (быстрый, офлайн)',
  api: 'API qwen3-vl-embedding-8b',
}

const STRATEGY_LABEL: Record<KbStrategy, string> = {
  fixed: 'Фиксированный размер',
  structural: 'По структуре (заголовки)',
}

// Тумблер «Включить»: та же визуальная схема switch, что у слоёв памяти
// (MemoryTab, .layer-toggle) — label-обёртка даёт имя для screen reader
function KbSwitch({
  label,
  titleOn,
  titleOff,
  on,
  onChange,
}: {
  label: string
  titleOn: string
  titleOff: string
  on: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="kb-row">
      <span>{label}</span>
      <input
        type="checkbox"
        className="layer-toggle"
        role="switch"
        aria-checked={on}
        checked={on}
        title={on ? titleOn : titleOff}
        onChange={(e) => onChange(e.target.checked)}
      />
    </label>
  )
}

export default function KbTab() {
  // stats: null — ещё загружаем; {exists:false} — индекс не построен
  const [stats, setStats] = useState<KbStats | { exists: false } | null>(null)
  const [settings, setSettings] = useState<KbSettings | null>(null)
  const [strategy, setStrategy] = useState<KbStrategy>('fixed')
  const [embedder, setEmbedder] = useState<KbEmbedder>('hash')
  const [indexing, setIndexing] = useState(false)
  const [indexError, setIndexError] = useState('')
  // Прогресс сборки (polling /api/kb/build-status, пока идёт индексация)
  const [progress, setProgress] = useState<KbBuildStatus | null>(null)
  // Статус завершения: «Готово: N чанков, X c (…полная/инкремент…)»
  const [indexDone, setIndexDone] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Загрузки пользователя (data/kb/uploads): несёт отдельный
  // GET /api/kb/uploads — видны сразу после upload, даже без индекса
  const [uploads, setUploads] = useState<KbUpload[] | null>(null)
  const [uploading, setUploading] = useState(false)
  // Busy-флаг для delete/wipe (отдельно от upload-кнопки)
  const [deleting, setDeleting] = useState(false)
  const [uploadError, setUploadError] = useState('')
  // Подтверждение последней успешной загрузки (имена файлов)
  const [uploadOk, setUploadOk] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  // Поиск
  const [q, setQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<KbSearchResult[] | null>(null)
  const [searchError, setSearchError] = useState('')

  // Стратегия/эмбеддер синхронизируются из настроек один раз (пока
  // пользователь не трогал селекты — локальное состояние побеждает)
  const settingsApplied = useRef(false)

  const loadStats = async () => {
    try {
      setStats(await apiKbStats())
    } catch (err) {
      console.error('kb stats:', err)
    }
  }

  const loadUploads = async () => {
    try {
      setUploads(await apiKbUploads())
    } catch (err) {
      console.error('kb uploads:', err)
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        const [s, cfg] = await Promise.all([apiKbStats(), apiKbSettings()])
        setStats(s)
        setSettings(cfg)
        if (!settingsApplied.current) {
          settingsApplied.current = true
          setStrategy(cfg.strategy)
          setEmbedder(cfg.embedder)
        }
      } catch (err) {
        console.error('kb load:', err)
        setStats({ exists: false })
      }
    })()
    // Загрузки читаем отдельно: сбой не должен блокировать stats/settings
    void loadUploads()
  }, [])

  // Unmount во время индексации: остановить polling, чтобы fetch не летал
  // в отмонтированный компонент
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current)
  }, [])

  const hasIndex = stats !== null && stats !== undefined && 'exists' in stats && stats.exists

  // Частичное обновление настроек: POST → сервер отвечает актуальным
  // KbSettings (паттерн MemoryTab: POST → перечитать)
  const patchSettings = (patch: Partial<KbSettings>) => {
    void (async () => {
      try {
        setSettings(await apiKbSettingsPost(patch))
      } catch (err) {
        console.error('kb settings:', err)
      }
    })()
  }

  // Собрать индекс: POST /api/kb/index {strategy, embedder} (сервер сам
  // решает: индекс есть и params совпадают — только новые файлы).
  // Пока POST висит — polling build-status → прогресс-бар.
  const doIndex = () => {
    setIndexing(true)
    setIndexError('')
    setIndexDone('')
    setProgress({ running: true, phase: 'idle', done: 0, total: 0 })
    // polling каждые 1.5 c (сборка с API-эмбеддером идёт до ~3 мин)
    pollRef.current = setInterval(() => {
      apiKbBuildStatus()
        .then(setProgress)
        .catch(() => { /* сеть: прогресс обновится следующим поллом */ })
    }, 1500)
    void (async () => {
      try {
        const r = await apiKbIndex(strategy, embedder)
        const st = r.stats as { chunks?: number; build_ms?: number }
        const mode = r.mode === 'incremental'
          ? `инкремент, +${r.added} файл.`
          : 'полная сборка'
        setIndexDone(`Готово: ${st.chunks ?? 0} чанков, ` +
          `${((st.build_ms ?? 0) / 1000).toFixed(1)} c (${mode})`)
        await loadStats()
      } catch (err) {
        setIndexError(err instanceof Error ? err.message : String(err))
      } finally {
        if (pollRef.current) clearInterval(pollRef.current)
        pollRef.current = null
        setIndexing(false)
      }
    })()
  }

  // Очистка поиска: сброс запроса, результатов и ошибки
  const clearSearch = () => {
    setQ('')
    setResults(null)
    setSearchError('')
  }

  // Удалить загруженный файл (+ его чанки из индекса, сервер сам)
  const deleteFile = (name: string) => {
    if (!window.confirm(`Удалить «${name}» из базы знаний?`)) return
    setDeleting(true)
    setUploadError('')
    void (async () => {
      try {
        await apiKbDeleteUpload(name)
        await loadStats()
        await loadUploads()
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err))
      } finally {
        setDeleting(false)
      }
    })()
  }

  // Полная очист базы: все загрузки + индекс (settings сохраняются)
  const doWipe = () => {
    if (!window.confirm(
      'Очистить базу знаний? Будут удалены все загруженные файлы и индекс. ' +
      'Настройки (тумблеры, стратегия) сохранятся.',
    )) return
    setUploadError('')
    setIndexDone('')
    setDeleting(true)
    void (async () => {
      try {
        await apiKbWipe()
        setStats({ exists: false })
        setUploads([])
        await loadStats()
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err))
      } finally {
        setDeleting(false)
      }
    })()
  }

  // Загрузить выбранные файлы: каждый — POST /api/kb/upload (multipart)
  const onFiles = (list: FileList | null) => {
    if (!list || list.length === 0) return
    const files = Array.from(list)
    setUploading(true)
    setUploadError('')
    setUploadOk('')
    void (async () => {
      try {
        for (const f of files) {
          await apiKbUpload(f)
        }
        // Подтверждение: файл сохранён в uploads, в индекс попадёт
        // после пересборки. Список загрузок перечитываем
        // (GET /api/kb/uploads), чтобы новый файл был виден сразу
        setUploadOk(files.map((f) => f.name).join(', '))
        await loadStats()
        await loadUploads()
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : String(err))
      } finally {
        setUploading(false)
        if (fileRef.current) fileRef.current.value = ''
      }
    })()
  }

  // Поиск по базе (top-k фиксирован = 5)
  const doSearch = () => {
    const query = q.trim()
    if (!query || searching) return
    setSearching(true)
    setSearchError('')
    void (async () => {
      try {
        const r = await apiKbSearch(query, 5)
        setResults(r.results)
      } catch (err) {
        setResults(null)
        setSearchError(err instanceof Error ? err.message : String(err))
      } finally {
        setSearching(false)
      }
    })()
  }

  const s = hasIndex ? (stats as KbStats) : null

  return (
    <div className="ctx-sections">
      {/* ── Включить ── */}
      <section className="ctx-card" title="Включение RAG">
        <h3>Включить</h3>
        <KbSwitch
          label="RAG в диалоге"
          titleOn="RAG включён"
          titleOff="RAG выключен"
          on={settings?.rag ?? false}
          onChange={(v) => patchSettings({ rag: v })}
        />
        <KbSwitch
          label="Цикл агента (MCP-инструменты)"
          titleOn="Цикл агента включён"
          titleOff="Цикл агента выключен"
          on={settings?.agent_loop ?? false}
          onChange={(v) => patchSettings({ agent_loop: v })}
        />
        <label className="kb-row">
          <span>Реранкер</span>
          <select
            className="kb-select"
            aria-label="Реранкер"
            value={settings?.reranker ?? 'off'}
            onChange={(e) => patchSettings({ reranker: e.target.value as KbReranker })}
          >
            <option value="off">Выключен (только гибридный поиск)</option>
            <option value="api">API qwen3-reranker-4b</option>
          </select>
        </label>
        {settings?.reranker === 'api' && s && s.reranker_key_configured === false && (
          <p className="kb-error">Ключ реранкера не настроен (GPUSTACK_KEY_RERANK) — будет использоваться только гибридный поиск</p>
        )}
        <label className="kb-row">
          <span>Этап 1: кандидатов (recall)</span>
          <input
            type="number"
            className="input kb-topk-input"
            aria-label="Этап 1: кандидатов (recall)"
            min={1}
            max={200}
            value={settings?.rag_recall ?? 50}
            onChange={(e) => {
              const n = Math.max(1, Math.min(200, Number(e.target.value) || 1))
              patchSettings({ rag_recall: n })
            }}
          />
        </label>
        <label className="kb-row">
          <span>Этап 2: Топ-K в диалоге</span>
          <input
            type="number"
            className="input kb-topk-input"
            aria-label="Этап 2: Топ-K в диалоге"
            min={1}
            max={10}
            value={settings?.rag_top_k ?? 5}
            onChange={(e) => {
              const n = Math.max(1, Math.min(10, Number(e.target.value) || 1))
              patchSettings({ rag_top_k: n })
            }}
          />
        </label>
      </section>

      {/* ── Индексация ── */}
      <section className="ctx-card" title="Индексация документов">
        <h3>Индексация</h3>
        <label className="kb-field">
          <span className="kb-field-label">Стратегия</span>
          <select
            className="kb-select"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value as KbStrategy)}
          >
            <option value="fixed">Фиксированный размер</option>
            <option value="structural">По структуре (заголовки)</option>
          </select>
        </label>
        <label className="kb-field">
          <span className="kb-field-label">Эмбеддер</span>
          <select
            className="kb-select"
            value={embedder}
            onChange={(e) => setEmbedder(e.target.value as KbEmbedder)}
          >
            <option value="hash">Локальный (быстрый, офлайн)</option>
            <option value="api">API qwen3-vl-embedding-8b</option>
          </select>
        </label>
        <button type="button" className="btn" disabled={indexing} onClick={doIndex}>
          {indexing ? 'Индексация…' : 'Индексировать'}
        </button>
        <p className="kb-hint">новые файлы — только они (инкремент); полная пересборка — смена стратегии/эмбеддера. API-эмбеддер — до ~3 мин</p>
        {indexing && progress && (
          <>
            <div
              className="kb-progress"
              role="progressbar"
              aria-label="Прогресс индексации"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progressPct(progress)}
            >
              <div
                className="kb-progress-fill"
                style={{ width: `${progressPct(progress)}%` }}
              />
            </div>
            <p className="kb-hint">{phaseLabel(progress)} — {progressPct(progress)}%</p>
          </>
        )}
        {indexDone && <p className="kb-done">{indexDone}</p>}
        {indexError && <p className="kb-error">{indexError}</p>}
      </section>

      {/* ── Файлы ── */}
      <section className="ctx-card" title="Файлы базы">
        <h3>Файлы</h3>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="kb-file-input"
          aria-label="Файл"
          accept=".txt,.md,.py,.js,.ts,.tsx,.jsx,.json,.csv,.html,.css,.yaml,.yml"
          onChange={(e) => onFiles(e.target.files)}
        />
        <div className="kb-file-actions">
          <button
            type="button"
            className="btn"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? 'Загрузка…' : '+ Добавить файл'}
          </button>
          <button
            type="button"
            className="btn btn-danger kb-wipe"
            disabled={deleting || !uploads || uploads.length === 0}
            onClick={doWipe}
            title="Удалить все загруженные файлы и индекс (настройки сохраняются)"
          >
            {deleting ? 'Очистка…' : 'Очистить базу'}
          </button>
        </div>
        {/* Загрузки пользователя: несёт отдельный GET /api/kb/uploads —
            видны сразу после upload, даже ДО сборки индекса (stats 404).
            Чип — «в индексе» (попал после «Индексировать») / «не в индексе»
            (без индекса — всегда «не в индексе») */}
        {uploads && uploads.length > 0 && (
          <>
            <p className="kb-hint">Загрузки:</p>
            <ul className="kb-files">
              {uploads.map((u) => {
                const indexed = s ? s.files.includes(`uploads/${u.name}`) : false
                return (
                  <li key={u.name} className="kb-file">
                    <span className="kb-file-name">{u.name}</span>
                    <span className="kb-hint"> · {humanSize(u.size)}</span>
                    <span
                      className={indexed ? 'kb-chip kb-chip-ok' : 'kb-chip'}
                      title={indexed
                        ? 'Файл в индексе'
                        : 'Файл сохранён, но ещё не в индексе — нажмите «Индексировать»'}
                    >
                      {indexed ? 'в индексе' : 'не в индексе'}
                    </span>
                    <button
                      type="button"
                      className="btn btn-icon kb-file-del"
                      aria-label={`Удалить ${u.name}`}
                      title="Удалить файл"
                      disabled={deleting}
                      onClick={() => deleteFile(u.name)}
                    >
                      ×
                    </button>
                  </li>
                )
              })}
            </ul>
          </>
        )}
        {/* Корпус, который ещё не попал в индекс (новые файлы).
            Блока «Документы репозитория в индексе» больше нет: корпус —
            только загрузки (uploads), доки репозитория в индекс не входят. */}
        {s && s.pending_files && s.pending_files.length > 0 && (
          <p className="kb-hint">Ещё не в индексе: {s.pending_files.length} — нажмите «Индексировать» (индексировать будут только новые)</p>
        )}
        {uploadOk && (
          <p className="kb-hint">Добавлено: {uploadOk}. Нажмите «Индексировать», чтобы файл попал в базу.</p>
        )}
        {uploadError && <p className="kb-error">{uploadError}</p>}
      </section>

      {/* ── Статистика ── */}
      {s && (
        <section className="ctx-card" title="Статистика индекса">
          <h3>Статистика</h3>
          <div className="tok-row"><span className="tok-label">Документов</span><span className="tok-value">{s.stats.docs}</span></div>
          <div className="tok-row"><span className="tok-label">Чанков</span><span className="tok-value">{s.stats.chunks}</span></div>
          <div className="tok-row"><span className="tok-label">Слов корпуса</span><span className="tok-value">{s.stats.corpus_words}</span></div>
          <div className="tok-row"><span className="tok-label">Dim</span><span className="tok-value">{s.dim}</span></div>
          <div className="tok-row"><span className="tok-label">Время сборки</span><span className="tok-value">{(s.stats.build_ms / 1000).toFixed(1)} c</span></div>
          <div className="tok-row"><span className="tok-label">Создан</span><span className="tok-value">{new Date(s.built_at).toLocaleString('ru-RU')}</span></div>
          <div className="tok-row"><span className="tok-label">Эмбеддер</span><span className="tok-value">{EMBEDDER_LABEL[s.embedder]}</span></div>
          <div className="tok-row"><span className="tok-label">Стратегия</span><span className="tok-value">{STRATEGY_LABEL[s.strategy]}</span></div>
        </section>
      )}

      {/* ── Сравнение стратегий ── */}
      {s && (
        <section className="ctx-card" title="Сравнение стратегий">
          <h3>Сравнение стратегий</h3>
          <table className="kb-table">
            <thead>
              <tr>
                <th></th>
                <th className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>fixed</th>
                <th className={s.strategy === 'structural' ? 'kb-col-active' : ''}>structural</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>чанков</td>
                <td className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>{len0(s.comparison.fixed.chunks)}</td>
                <td className={s.strategy === 'structural' ? 'kb-col-active' : ''}>{len0(s.comparison.structural.chunks)}</td>
              </tr>
              <tr>
                <td>сред. длина</td>
                <td className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>{len0(s.comparison.fixed.avg_chars)}</td>
                <td className={s.strategy === 'structural' ? 'kb-col-active' : ''}>{len0(s.comparison.structural.avg_chars)}</td>
              </tr>
              <tr>
                <td>макс. длина</td>
                <td className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>{len0(s.comparison.fixed.max_chars)}</td>
                <td className={s.strategy === 'structural' ? 'kb-col-active' : ''}>{len0(s.comparison.structural.max_chars)}</td>
              </tr>
              <tr>
                <td>hit@3</td>
                <td className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>{metric3(s.comparison.fixed.hit_at_3)}</td>
                <td className={s.strategy === 'structural' ? 'kb-col-active' : ''}>{metric3(s.comparison.structural.hit_at_3)}</td>
              </tr>
              <tr>
                <td>precision@3</td>
                <td className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>{metric3(s.comparison.fixed.precision_at_3)}</td>
                <td className={s.strategy === 'structural' ? 'kb-col-active' : ''}>{metric3(s.comparison.structural.precision_at_3)}</td>
              </tr>
              <tr>
                <td>MRR</td>
                <td className={s.strategy === 'fixed' ? 'kb-col-active' : ''}>{metric3(s.comparison.fixed.mrr)}</td>
                <td className={s.strategy === 'structural' ? 'kb-col-active' : ''}>{metric3(s.comparison.structural.mrr)}</td>
              </tr>
            </tbody>
          </table>
        </section>
      )}

      {/* ── Поиск по базе ── */}
      <section className="ctx-card" title="Поиск по базе">
        <h3>Поиск по базе</h3>
        {!s && stats !== null && <p className="kv-empty">Сначала создайте индекс</p>}
        {stats === null && <p className="kv-empty">Загрузка…</p>}
        {s && (
          <>
            <div className="kb-search">
              <input
                className="input"
                aria-label="Запрос"
                placeholder="Запрос"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') doSearch()
                }}
              />
              <button
                type="button"
                className="btn"
                disabled={searching || !q.trim()}
                onClick={doSearch}
              >
                Найти
              </button>
              {/* Очистить: сбросить запрос, результаты и ошибку */}
              <button
                type="button"
                className="btn btn-icon kb-clear"
                aria-label="Очистить поиск"
                title="Очистить запрос и результаты"
                disabled={!q && results === null && !searchError}
                onClick={clearSearch}
              >
                ×
              </button>
            </div>
            {searchError && <p className="kb-error">{searchError}</p>}
            {results !== null && results.length === 0 && (
              <p className="kv-empty">Ничего не найдено</p>
            )}
            {results?.map((r) => (
              <div key={r.chunk_id} className="kb-result">
                <div className="kb-result-head">
                  <span className="kb-score">
                    {r.rerank_score != null ? metric3(r.rerank_score) : metric3(r.score)}
                  </span>
                  {r.rerank_score != null && (
                    <span
                      className="kb-chip"
                      title={`Реранкер поднял с позиции этапа 1 #${r.stage1_rank}`}
                    >
                      из #{r.stage1_rank}
                    </span>
                  )}
                  <span className="kb-result-src">{r.file}{r.section ? ` · ${r.section}` : ''}</span>
                </div>
                <p className="kb-excerpt">
                  {r.text.slice(0, 300)}{r.text.length > 300 ? '…' : ''}
                </p>
              </div>
            ))}
          </>
        )}
      </section>
    </div>
  )
}
