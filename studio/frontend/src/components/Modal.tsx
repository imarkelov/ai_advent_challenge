// Универсальная модалка «Студии»: оверлей (клик мимо — закрыть),
// шапка с «×», ESC, опциональный resize-grip в правом нижнем углу
// (pointer-capture, лимиты 320×200 … окно−32) и кнопка «Скопировать»
// (clipboard API + fallback через textarea/execCommand, «Скопировано ✓» 2 сек).
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'

interface ModalProps {
  title: string
  onClose: () => void
  children: ReactNode
  resizable?: boolean
  copyText?: string
}

export default function Modal({ title, onClose, children, resizable, copyText }: ModalProps) {
  const [copied, setCopied] = useState(false)
  const [size, setSize] = useState({ w: 560, h: 420 })
  const copyTimer = useRef<number | undefined>(undefined)
  // Точка старта перетаскивания grip: startXY (курсор) + startW/startH (панель)
  const drag = useRef<{ x: number; y: number; w: number; h: number } | null>(null)

  // ESC — закрыть
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // Resize: window-слушатели pointermove/pointerup активны, пока модалка resizable
  useEffect(() => {
    if (!resizable) return
    const onMove = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      setSize({
        w: Math.min(Math.max(320, d.w + e.clientX - d.x), window.innerWidth - 32),
        h: Math.min(Math.max(200, d.h + e.clientY - d.y), window.innerHeight - 32),
      })
    }
    const onUp = () => {
      drag.current = null
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [resizable])

  // Таймер «Скопировано ✓» не должен дёрнуть компонент после размонтирования
  useEffect(() => () => window.clearTimeout(copyTimer.current), [])

  // Копирование: clipboard API, фолбэк — временный textarea + execCommand('copy')
  const copy = async () => {
    if (copyText === undefined) return
    try {
      await navigator.clipboard.writeText(copyText)
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = copyText
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
    copyTimer.current = window.setTimeout(() => setCopied(false), 2000)
  }

  const onGripDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!resizable) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, w: size.w, h: size.h }
  }

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        // только клик ПО ОВЕРЛЕЮ, не по панели
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className="modal"
        style={resizable ? { width: size.w, height: size.h } : undefined}
      >
        <header className="modal-head">
          <span className="modal-title">{title}</span>
          <button type="button" className="btn-icon" title="Закрыть" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {copyText !== undefined && (
          <footer className="modal-foot">
            <button type="button" className="btn" onClick={() => void copy()}>
              {copied ? 'Скопировано ✓' : 'Скопировать'}
            </button>
          </footer>
        )}
        {resizable && <div className="modal-grip" onPointerDown={onGripDown} />}
      </div>
    </div>
  )
}
