// Токен-гейдж в шапке чата: SVG-кольцо заполнения лимита контекста
// (total последнего запроса / context_limit — та же семантика, что
// progress-бар) + компактное число.
// По hover — TokenPopover (лимит с progress-баром, последний usage,
// сессионные токены, модель; растягиваемый, размер в localStorage).
// Закрытие поповера: mouseleave с grace-периодом 300ms (успеть заехать
// внутрь окна) или клик вне. Данные — только из state useStudio, без
// новых API-вызовов.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useStudio } from '../state'
import TokenPopover from './TokenPopover'

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

// Grace-период закрытия по mouseleave: курсору хватает времени пересечь
// зазор между кольцом и поповером
const CLOSE_GRACE_MS = 300

export default function TokenGauge() {
  const { state } = useStudio()
  const t = state.tokens
  const limit = t?.context_limit ?? 0
  const lastTotal = t?.last?.total ?? null
  // Заполнение кольца: total последнего запроса / лимит контекста
  const pct = limit > 0 && lastTotal != null ? Math.min(1, lastTotal / limit) : 0
  const warn = pct > 0.9

  const [popOpen, setPopOpen] = useState(false)
  const graceTimer = useRef<number | undefined>(undefined)

  const cancelClose = useCallback(() => {
    window.clearTimeout(graceTimer.current)
  }, [])
  const scheduleClose = useCallback(() => {
    window.clearTimeout(graceTimer.current)
    graceTimer.current = window.setTimeout(() => setPopOpen(false), CLOSE_GRACE_MS)
  }, [])
  const close = useCallback(() => {
    window.clearTimeout(graceTimer.current)
    setPopOpen(false)
  }, [])

  // Таймер grace не должен дёрнуть компонент после размонтирования
  useEffect(() => () => window.clearTimeout(graceTimer.current), [])

  return (
    <span
      className={`token-gauge${warn ? ' warn' : ''}`}
      role="progressbar"
      aria-label="Лимит контекста"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct * 100)}
      onMouseEnter={() => {
        // вход в зону (кольцо или поповер-потомок) — открыть и отменить
        // возможный закрывающий таймер
        cancelClose()
        setPopOpen(true)
      }}
      onMouseLeave={scheduleClose}
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
      {popOpen && <TokenPopover onHover={cancelClose} onClose={close} />}
    </span>
  )
}
