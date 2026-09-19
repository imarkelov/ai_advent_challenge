// Modal — базовые механики: закрытие (× / ESC), копирование copyText в буфер.
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import Modal from '../src/components/Modal'

describe('Modal — закрытие и копирование', () => {
  it('клик по «×» вызывает onClose', () => {
    const onClose = vi.fn()
    render(
      <Modal title="Тест" onClose={onClose}>
        <p>контент</p>
      </Modal>,
    )
    fireEvent.click(screen.getByTitle('Закрыть'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ESC вызывает onClose', () => {
    const onClose = vi.fn()
    render(
      <Modal title="Тест" onClose={onClose}>
        <p>контент</p>
      </Modal>,
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('клик по оверлею вызывает onClose', () => {
    const onClose = vi.fn()
    render(
      <Modal title="Тест" onClose={onClose}>
        <p>контент</p>
      </Modal>,
    )
    fireEvent.click(screen.getByText('Тест').closest('.modal-overlay')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('клик по «Скопировать» кладёт copyText в clipboard', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(
      <Modal title="Тест" onClose={vi.fn()} copyText="копируй меня">
        <p>контент</p>
      </Modal>,
    )
    fireEvent.click(screen.getByText('Скопировать'))
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('копируй меня'))
    // после успеха — «Скопировано ✓»
    expect(await screen.findByText('Скопировано ✓')).toBeInTheDocument()
  })
})
