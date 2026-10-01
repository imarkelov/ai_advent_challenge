// Токен-поповер: всплывающее окно рядом с кольцом токенов (TokenGauge,
// hover). Содержимое — те же данные, что были в блоке «Токены» сайдбара:
// лимит контекста с progress-баром, последний usage (prompt/completion/
// total), сессионные токены, модель.
// Растягивается за drag-grip в правом нижнем углу (pointer events,
// min 320×200, max — вьюпорт минус отступы; паттерн модалки JSON дня 8);
// размер сохраняется в localStorage (token-pop-size) и применяется при
// следующем открытии.
import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { useStudio } from '../state'

const SIZE_KEY = 'token-pop-size'
const MIN_W = 320
const MIN_H = 200
const MARGIN = 32
const DEFAULT_SIZE = { w: 340, h: 240 }

const clamp = (n: number, min: number, max: number) => Math.min(Math.max(min, n), max)

const maxW = () => Math.max(MIN_W, window.innerWidth - MARGIN)
const maxH = () => Math.max(MIN_H, window.innerHeight - MARGIN)

// Сохранённый размер (JSON {w,h}); битая запись — дефолты
function loadSize(): { w: number; h: number } {
  try {
    const raw = localStorage.getItem(SIZE_KEY)
    if (raw) {
      const p = JSON.parse(raw) as { w?: unknown; h?: unknown }
      if (
        typeof p.w === 'number' &&
        typeof p.h === 'number' &&
        Number.isFinite(p.w) &&
        Number.isFinite(p.h)
      ) {
        return { w: p.w, h: p.h }
      }
    }
  } catch {
    // битая JSON-запись — молча дефолты
  }
  return DEFAULT_SIZE
}

interface TokenPopoverProps {
  // Курсор вернулся в зону (на поповер) — отменить закрывающий grace-таймер
  onHover: () => void
  onClose: () => void
}

export default function TokenPopover({ onHover, onClose }: TokenPopoverProps) {
  const { state } = useStudio()
  const t = state.tokens
  const limit = t?.context_limit ?? 0
  const last = t?.last
  const lastTotal = last?.total ?? null
  // Заполнение progress-бара: тот же расчёт, что у кольца гейджа
  const pct = limit > 0 && lastTotal != null ? Math.min(100, (lastTotal / limit) * 100) : 0
  const warn = pct > 90

  const [size, setSize] = useState(() => {
    const s = loadSize()
    return { w: clamp(s.w, MIN_W, maxW()), h: clamp(s.h, MIN_H, maxH()) }
  })
  // Текущий размер для записи в localStorage на pointerup (без пересоздания
  // window-слушателей при каждом move)
  const sizeRef = useRef(size)
  useEffect(() => {
    sizeRef.current = size
  }, [size])
  const drag = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  const popRef = useRef<HTMLDivElement>(null)

  // Клик вне поповера — закрыть
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('click', onClick)
    return () => document.removeEventListener('click', onClick)
  }, [onClose])

  // Resize: window-слушатели pointermove/pointerup; размер фиксируем на
  // pointerup (в localStorage) — drag-grip паттерн из модалки JSON
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      setSize({
        w: clamp(d.w + e.clientX - d.x, MIN_W, maxW()),
        h: clamp(d.h + e.clientY - d.y, MIN_H, maxH()),
      })
    }
    const onUp = () => {
      if (!drag.current) return
      drag.current = null
      localStorage.setItem(SIZE_KEY, JSON.stringify(sizeRef.current))
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  const onGripDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    // jsdom не реализует Pointer Capture — guard только для тестов
    if (typeof e.currentTarget.setPointerCapture === 'function') {
      e.currentTarget.setPointerCapture(e.pointerId)
    }
    drag.current = { x: e.clientX, y: e.clientY, w: size.w, h: size.h }
  }

  return (
    <div
      ref={popRef}
      className="token-pop"
      style={{ width: size.w, height: size.h }}
      aria-label="Токены"
      onMouseEnter={onHover}
    >
      <div className="token-pop-body">
        <div className="pop-title">Лимит контекста</div>
        <div
          className="pop-progress"
          role="progressbar"
          aria-label="Заполнение лимита контекста"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
        >
          <div
            className={warn ? 'pop-fill warn' : 'pop-fill'}
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="pop-row">
          <span>последний запрос</span>
          <b>{limit > 0 && lastTotal != null ? `${lastTotal} / ${limit}` : '—'}</b>
        </div>
        <div className="pop-row">
          <span>prompt</span>
          <b>{last?.prompt ?? '—'}</b>
        </div>
        <div className="pop-row">
          <span>completion</span>
          <b>{last?.completion ?? '—'}</b>
        </div>
        <div className="pop-row">
          <span>total</span>
          <b>{lastTotal ?? '—'}</b>
        </div>
        <div className="pop-row">
          <span>за сессию</span>
          <b>{t ? t.session.total : '—'}</b>
        </div>
        <div className="pop-row">
          <span>модель</span>
          <b>{state.config?.model ?? '—'}</b>
        </div>
      </div>
      <div className="token-pop-grip" title="Растянуть" onPointerDown={onGripDown} />
    </div>
  )
}
