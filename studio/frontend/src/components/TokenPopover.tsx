// Токен-поповер: всплывающее окно рядом с кольцом токенов (TokenGauge,
// hover). Показывает расход и остаток ОКНА диалога: окно модели (реальный
// num_ctx), занято (промпт последнего запроса + резерв на ответ),
// сколько осталось для диалога и сколько ещё полных ходов влезает; ниже —
// последний usage (prompt/completion/total), сессионные токены, модель.
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
  const limit = t?.context_window ?? t?.context_limit ?? 0
  const last = t?.last
  const lastTotal = last?.total ?? null
  const outputBudget = t?.max_output_tokens ?? 0
  // Занято окна: промпт последнего запроса + резерв под ответ (ответ в
  // окно не входит — он уже написан, но место под следующий зарезервировано)
  const occupied =
    t?.occupied_tokens ?? (last != null ? last.prompt + outputBudget : null)
  const remaining =
    t?.remaining_for_dialog ?? (limit > 0 && occupied != null ? Math.max(0, limit - occupied) : null)
  const turns = t?.full_in_turns ?? (remaining != null && last && last.prompt > 0
    ? Math.floor(remaining / last.prompt)
    : null)
  // Заполнение progress-бара: тот же расчёт, что у кольца гейджа
  const pct = limit > 0 && occupied != null ? Math.min(100, (occupied / limit) * 100) : 0
  const warn = pct > 90
  const fmt = (n: number | null | undefined) =>
    n == null ? '—' : n.toLocaleString('ru-RU')

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
        <div className="pop-title">Окно диалога</div>
        <div
          className="pop-progress"
          role="progressbar"
          aria-label="Заполнение окна контекста"
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
          <span>занято</span>
          <b data-testid="tokens-occupied">
            {limit > 0 && occupied != null ? `${fmt(occupied)} / ${fmt(limit)}` : '—'}
          </b>
        </div>
        <div className="pop-row">
          <span>осталось для диалога</span>
          <b data-testid="tokens-remaining">{fmt(remaining)}</b>
        </div>
        <div className="pop-row">
          <span>резерв на ответ</span>
          <b>{outputBudget ? fmt(outputBudget) : '—'}</b>
        </div>
        <div className="pop-row">
          <span>ещё влезет</span>
          <b data-testid="tokens-turns">{turns != null ? `${turns} ход(ов)` : '—'}</b>
        </div>
        <div className="pop-title">Последний запрос</div>
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
          <b>
            {state.config?.model ?? '—'}
            {t?.context_window_method === 'static' ? ' (окно: справочник)' : ''}
          </b>
        </div>
      </div>
      <div className="token-pop-grip" title="Растянуть" onPointerDown={onGripDown} />
    </div>
  )
}
