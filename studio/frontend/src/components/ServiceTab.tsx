// Вкладка «Сервис» (день 30): локальная LLM как приватный сетевой сервис.
// Показывает адрес для подключения с других устройств (LAN) и готовый
// пример, таблицу клиентов с ключами и лимитами (RPM/TPM/max context),
// живые счётчики использования и лог отклонённых запросов.
import { useCallback, useEffect, useState } from 'react'
import {
  apiGatewayClientDelete,
  apiGatewayClients,
  apiGatewayClientUpsert,
  apiGatewayRejections,
  apiGatewayReset,
  apiGatewayStatus,
  apiGatewayUsage,
  type GatewayClient,
  type GatewayRejection,
  type GatewayStatus,
  type GatewayUsage,
} from '../api'

// Пустая форма нового клиента: лимиты — разумные дефолты.
interface FormState {
  name: string
  rpm: string
  tpm: string
  max_context: string
  max_output_tokens: string
  allow_models: string
}

const EMPTY_FORM: FormState = {
  name: '',
  rpm: '60',
  tpm: '20000',
  max_context: '8192',
  max_output_tokens: '2048',
  allow_models: '',
}

function num(v: string): number | undefined {
  const t = v.trim()
  if (!t) return undefined
  const n = Number(t)
  return Number.isFinite(n) ? n : undefined
}

function fmtTime(ts?: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString('ru-RU')
}

export default function ServiceTab() {
  const [status, setStatus] = useState<GatewayStatus | null>(null)
  const [clients, setClients] = useState<GatewayClient[]>([])
  const [usage, setUsage] = useState<GatewayUsage | null>(null)
  const [rejections, setRejections] = useState<GatewayRejection[]>([])
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [newKey, setNewKey] = useState<{ name: string; key: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const [s, c, u, r] = await Promise.all([
      apiGatewayStatus().catch(() => null),
      apiGatewayClients().catch(() => ({ clients: [] })),
      apiGatewayUsage().catch(() => null),
      apiGatewayRejections(30).catch(() => ({ rejections: [] })),
    ])
    setStatus(s)
    setClients(c.clients)
    setUsage(u)
    setRejections(r.rejections)
  }, [])

  useEffect(() => {
    void refresh()
    // Живые счётчики: обновляем раз в 5 с, пока вкладка открыта.
    const t = setInterval(() => void refresh(), 5000)
    return () => clearInterval(t)
  }, [refresh])

  async function copy(text: string, tag: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(tag)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      setErr('Не удалось скопировать в буфер обмена')
    }
  }

  async function create() {
    if (busy) return
    const name = form.name.trim()
    if (!name) {
      setErr('Укажите имя клиента')
      return
    }
    setBusy(true)
    try {
      const models = form.allow_models
        .split(',')
        .map((m) => m.trim())
        .filter(Boolean)
      const created = await apiGatewayClientUpsert({
        name,
        rpm: num(form.rpm),
        tpm: num(form.tpm),
        max_context: num(form.max_context),
        max_output_tokens: num(form.max_output_tokens),
        allow_models: models,
      })
      // Ключ показывается один раз: сохранить его больше негде.
      if (created.key) setNewKey({ name: created.name, key: created.key })
      setForm(EMPTY_FORM)
      setErr(null)
      await refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось создать клиента')
    } finally {
      setBusy(false)
    }
  }

  async function toggle(c: GatewayClient) {
    try {
      await apiGatewayClientUpsert({ name: c.name, enabled: !c.enabled })
      await refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось переключить клиента')
    }
  }

  async function rotate(c: GatewayClient) {
    try {
      const r = await apiGatewayClientUpsert({ name: c.name, rotate_key: true })
      if (r.key) setNewKey({ name: r.name, key: r.key })
      await refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось обновить ключ')
    }
  }

  async function remove(c: GatewayClient) {
    if (!confirm(`Удалить клиента «${c.name}»? Его ключ перестанет работать.`)) return
    try {
      await apiGatewayClientDelete(c.name)
      if (newKey?.name === c.name) setNewKey(null)
      await refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось удалить клиента')
    }
  }

  async function resetUsage(c: GatewayClient) {
    try {
      await apiGatewayReset(c.name)
      await refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Не удалось сбросить счётчики')
    }
  }

  const curlExample = status
    ? `curl ${status.chat_url} \\
  -H "Authorization: Bearer <КЛЮЧ>" \\
  -H "Content-Type: application/json" \\
  -d '{"model": "${status.models[0] ?? 'gemma3:27b'}", "messages": [{"role": "user", "content": "Привет!"}]}'`
    : ''

  const pyExample = status
    ? `from openai import OpenAI

client = OpenAI(base_url="${status.lan_url}", api_key="<КЛЮЧ>")
r = client.chat.completions.create(
    model="${status.models[0] ?? 'gemma3:27b'}",
    messages=[{"role": "user", "content": "Привет!"}],
)
print(r.choices[0].message.content)`
    : ''

  return (
    <div className="series-tab">
      {err && <div className="series-error">{err}</div>}

      {/* Адрес для подключения */}
      <div className="series-block">
        <span className="series-label">Адрес сервиса в локальной сети</span>
        {status ? (
          <>
            <div className="series-row">
              <input
                className="series-input"
                readOnly
                value={status.chat_url}
                aria-label="URL сервиса"
              />
              <button
                type="button"
                className="btn"
                onClick={() => copy(status.chat_url, 'url')}
              >
                {copied === 'url' ? '✓' : 'Копировать'}
              </button>
            </div>
            <div className="series-params">
              <span className="series-chip">
                моделей <b>{status.models.length}</b>
              </span>
              <span className="series-chip">
                клиентов <b>{status.clients}</b>
              </span>
              <span className="series-chip">
                TLS <b>{status.tls ? 'вкл' : 'выкл'}</b>
              </span>
            </div>
            <p className="series-warn">{status.note}</p>
          </>
        ) : (
          <p className="series-empty">Сервис недоступен…</p>
        )}
      </div>

      {/* Примеры подключения */}
      {status && (
        <div className="series-block">
          <span className="series-label">Как подключиться с другого устройства</span>
          <div className="series-row">
            <button
              type="button"
              className="btn"
              onClick={() => copy(curlExample, 'curl')}
            >
              {copied === 'curl' ? '✓ Скопировано' : 'Копировать curl'}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => copy(pyExample, 'py')}
            >
              {copied === 'py' ? '✓ Скопировано' : 'Копировать Python'}
            </button>
          </div>
          <pre className="series-prompt">{curlExample}</pre>
        </div>
      )}

      {/* Новый клиент */}
      <div className="series-block">
        <span className="series-label">Новый клиент</span>
        <div className="series-row">
          <input
            className="series-input"
            placeholder="Имя (например: телефон)"
            value={form.name}
            aria-label="Имя клиента"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="series-row">
          <input
            className="series-input"
            placeholder="RPM"
            title="Запросов в минуту (0 — без лимита)"
            aria-label="RPM"
            value={form.rpm}
            onChange={(e) => setForm({ ...form, rpm: e.target.value })}
          />
          <input
            className="series-input"
            placeholder="TPM"
            title="Токенов в минуту (0 — без лимита)"
            aria-label="TPM"
            value={form.tpm}
            onChange={(e) => setForm({ ...form, tpm: e.target.value })}
          />
        </div>
        <div className="series-row">
          <input
            className="series-input"
            placeholder="Max context"
            title="Лимит входных токенов; при превышении контекст сжимается сводкой"
            aria-label="Max context"
            value={form.max_context}
            onChange={(e) => setForm({ ...form, max_context: e.target.value })}
          />
          <input
            className="series-input"
            placeholder="Max output"
            title="Потолок токенов ответа"
            aria-label="Max output tokens"
            value={form.max_output_tokens}
            onChange={(e) => setForm({ ...form, max_output_tokens: e.target.value })}
          />
        </div>
        <input
          className="series-input"
          placeholder="Модели через запятую (пусто — все; gemma3:* — маска)"
          aria-label="Разрешённые модели"
          value={form.allow_models}
          onChange={(e) => setForm({ ...form, allow_models: e.target.value })}
        />
        <button type="button" className="btn gw-primary" disabled={busy} onClick={create}>
          {busy ? '…' : 'Создать клиента'}
        </button>
      </div>

      {/* Новый ключ — показывается один раз */}
      {newKey && (
        <div className="series-block">
          <span className="series-label">Ключ клиента «{newKey.name}»</span>
          <p className="series-warn">
            Скопируйте ключ сейчас — он хранится только в виде хэша и больше
            показан не будет.
          </p>
          <div className="series-row">
            <input
              className="series-input"
              readOnly
              value={newKey.key}
              aria-label="Ключ клиента"
            />
            <button
              type="button"
              className="btn"
              onClick={() => copy(newKey.key, 'key')}
            >
              {copied === 'key' ? '✓' : 'Копировать'}
            </button>
          </div>
          <button type="button" className="series-link" onClick={() => setNewKey(null)}>
            Скрыть
          </button>
        </div>
      )}

      {/* Клиенты */}
      <div className="series-block">
        <span className="series-label">Клиенты</span>
        {clients.length === 0 && <p className="series-empty">Пока ни одного клиента</p>}
        {clients.map((c) => {
          const u = usage?.usage[c.name]
          return (
            <div key={c.name} className="gw-client">
              <div className="series-card-head">
                <b>{c.name}</b>
                <span className={c.enabled ? 'gw-on' : 'gw-off'}>
                  {c.enabled ? 'включён' : 'выключен'}
                </span>
              </div>
              <div className="series-params">
                <span className="series-chip">
                  RPM <b>{c.rpm || '∞'}</b>
                </span>
                <span className="series-chip">
                  TPM <b>{c.tpm || '∞'}</b>
                </span>
                <span className="series-chip">
                  context <b>{c.max_context || '∞'}</b>
                </span>
                <span className="series-chip">
                  max out <b>{c.max_output_tokens || '∞'}</b>
                </span>
                {c.allow_models.length > 0 && (
                  <span className="series-chip">
                    модели <b>{c.allow_models.join(', ')}</b>
                  </span>
                )}
              </div>
              <div className="gw-usage">
                запросов <b>{u?.requests ?? 0}</b> · токенов{' '}
                <b>{u?.tokens_total ?? 0}</b> · сжатий{' '}
                <b>{u?.compressed ?? 0}</b> · отказов <b>{u?.rejected ?? 0}</b>
                {u?.window && (
                  <>
                    {' '}
                    · в окне <b>{u.window.requests}</b> запр. /{' '}
                    <b>{u.window.tokens}</b> ток.
                  </>
                )}
                {u?.avg_latency ? ` · ${u.avg_latency.toFixed(2)} с` : ''}
              </div>
              <div className="series-row">
                <button type="button" className="btn" onClick={() => toggle(c)}>
                  {c.enabled ? 'Выключить' : 'Включить'}
                </button>
                <button type="button" className="btn" onClick={() => rotate(c)}>
                  Новый ключ
                </button>
                <button
                  type="button"
                  className="btn"
                  title="Сбросить счётчики использования"
                  onClick={() => resetUsage(c)}
                >
                  Сбросить счётчики
                </button>
                <button type="button" className="btn btn-danger" onClick={() => remove(c)}>
                  Удалить
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Итоги */}
      {usage && (
        <div className="series-block">
          <span className="series-label">Итого</span>
          <div className="series-metrics">
            <span className="series-chip">
              запросов <b>{usage.totals.requests}</b>
            </span>
            <span className="series-chip">
              токенов <b>{usage.totals.tokens}</b>
            </span>
            <span className="series-chip">
              отказов <b>{usage.totals.rejected}</b>
            </span>
          </div>
        </div>
      )}

      {/* Отклонённые запросы */}
      <div className="series-block">
        <span className="series-label">Отклонённые запросы</span>
        {rejections.length === 0 && (
          <p className="series-empty">Отказов не было</p>
        )}
        {rejections
          .slice()
          .reverse()
          .map((r, i) => (
            <div key={`${r.ts}-${i}`} className="gw-reject">
              <span className="gw-reject-code">{r.status || '—'}</span>
              <span className="gw-reject-client">{r.client}</span>
              <span className="gw-reject-msg" title={r.message}>
                {r.message}
              </span>
              <span className="gw-reject-ts">{fmtTime(r.ts)}</span>
            </div>
          ))}
      </div>
    </div>
  )
}
