// Панель «Источники и цитаты» (день 24, задача 3, TDD): отдельная панель
// под assistant-сообщением — мокап B (B-1: карточки источников — номер,
// file · section, score-pill, «из #N» (stage1_rank) при реранке, цитата в
// «»; B-2: красная карточка «🚫 Не знаю» со слабым контекстом).
// Двухуровневое сворачивание (паттерн дня 19 «🧩 Шаги агента»):
// панель свёрнута по умолчанию (клик по шапке — карточки), каждая карточка
// свёрнута по умолчанию (клик по шапке — цитата).
// Чистый props-компонент — StudioProvider не нужен.
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import SourcesPanel from '../src/components/SourcesPanel'
import type { RagContext } from '../src/api'

// ── Фикстуры (данные мокапа B-1) ────────────────────────────────────────────
const RAG_CTX: RagContext = {
  query: 'Какой телефон был у героя?',
  recall_total: 50,
  reranked: true,
  chunks: [
    {
      rank: 1, chunk_id: 'egg_book-structural-0042',
      file: 'egg_book.txt', section: 'Глава 3', score: 0.94,
      stage1_rank: 2, reranked: true,
      text: 'У него был телефон IPhone 17Promax, купленный на зарплату за одну неделю.',
    },
    {
      rank: 2, chunk_id: 'egg_book-structural-0057',
      file: 'egg_book.txt', section: 'Глава 7', score: 0.61,
      stage1_rank: 9, reranked: true,
      text: '…а вечером он набирал номер на том самом IPhone и ждал ответа.',
    },
  ],
}

// Старое сообщение (до дня 24): без dont_know, без chunk_id в чанках
const LEGACY_CTX: RagContext = {
  recall_total: 50,
  reranked: false,
  chunks: [
    {
      rank: 1, file: 'a.md', section: 'Раздел', score: 0.7,
      stage1_rank: null, reranked: false, text: 'Старый чанк без chunk_id.',
    },
  ],
}

const PANEL_HEAD = { name: /📖 ИСТОЧНИКИ И ЦИТАТЫ/ }

describe('SourcesPanel — панель «Источники и цитаты» (мокап B)', () => {
  it('C-0: панель свёрнута по умолчанию — шапка (title + счётчик + caret ▸) видна, карточек нет в DOM', () => {
    const { container } = render(<SourcesPanel ragContext={RAG_CTX} />)

    // Шапка-кнопка: заголовок + счётчик (+ «reranked»), aria-expanded=false
    const head = screen.getByRole('button', PANEL_HEAD)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('2 источника · reranked')).toBeInTheDocument()
    // Caret свёрнутого состояния — ▸
    expect(container.querySelector('.src-caret')?.textContent).toBe('▸')

    // Тело свёрнуто: ни списка, ни карточек, ни цитат в DOM
    expect(container.querySelector('.src-cards')).toBeNull()
    expect(container.querySelectorAll('.src-card').length).toBe(0)
    expect(container.querySelector('.src-quote')).toBeNull()
  })

  it('C-1: клик по шапке — панель раскрывается (N карточек, caret ▾), повторный клик — сворачивается', () => {
    const { container } = render(<SourcesPanel ragContext={RAG_CTX} />)
    const head = screen.getByRole('button', PANEL_HEAD)

    // Раскрытие
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(container.querySelector('.src-caret')?.textContent).toBe('▾')
    const cards = container.querySelectorAll('.src-card')
    expect(cards.length).toBe(2)

    // Номера 1..N
    const nums = Array.from(container.querySelectorAll('.src-num')).map((n) => n.textContent)
    expect(nums).toEqual(['1', '2'])

    // file · section
    expect(screen.getByText('egg_book.txt · Глава 3')).toBeInTheDocument()
    expect(screen.getByText('egg_book.txt · Глава 7')).toBeInTheDocument()

    // «из #N» (stage1_rank) — реранкено
    expect(screen.getByText('из #2')).toBeInTheDocument()
    expect(screen.getByText('из #9')).toBeInTheDocument()
    // stage1_rank ≤ 20 → без 🚨
    expect(container.textContent).not.toContain('🚨')

    // chunk_id — id-строка видна, даже пока цитата свёрнута
    expect(screen.getByText('egg_book-structural-0042')).toBeInTheDocument()
    expect(screen.getByText('egg_book-structural-0057')).toBeInTheDocument()

    // Повторный клик — сворачивание: карточки снова вне DOM
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(container.querySelectorAll('.src-card').length).toBe(0)
  })

  it('C-2: цитата карточки свёрнута по умолчанию; клик по шапке карточки — цитата этой карточки; карточки независимы', () => {
    const { container } = render(<SourcesPanel ragContext={RAG_CTX} />)
    fireEvent.click(screen.getByRole('button', PANEL_HEAD))

    // Обе карточки свёрнуты: цитат нет в DOM, caret ▸
    expect(container.querySelectorAll('.src-quote').length).toBe(0)
    const card1 = screen.getByRole('button', { name: /egg_book\.txt · Глава 3/ })
    const card2 = screen.getByRole('button', { name: /egg_book\.txt · Глава 7/ })
    expect(card1).toHaveAttribute('aria-expanded', 'false')
    expect(card2).toHaveAttribute('aria-expanded', 'false')

    // Клик по 1-й карточке — её цитата вербатим, 2-я по-прежнему свёрнута
    fireEvent.click(card1)
    expect(card1).toHaveAttribute('aria-expanded', 'true')
    expect(card2).toHaveAttribute('aria-expanded', 'false')
    let quotes = container.querySelectorAll('.src-quote')
    expect(quotes.length).toBe(1)
    expect(quotes[0].textContent).toBe(
      '«У него был телефон IPhone 17Promax, купленный на зарплату за одну неделю.»',
    )

    // Клик по 2-й — обе открыты одновременно (несколько можно раскрыть)
    fireEvent.click(card2)
    quotes = container.querySelectorAll('.src-quote')
    expect(quotes.length).toBe(2)
    expect(quotes[1].textContent).toBe('«…а вечером он набирал номер на том самом IPhone и ждал ответа.»')

    // Повторный клик по 1-й — её цитата снова скрыта (toggle)
    fireEvent.click(card1)
    expect(card1).toHaveAttribute('aria-expanded', 'false')
    expect(container.querySelectorAll('.src-quote').length).toBe(1)
  })

  it('Шкала score: 🟢 ≥0.8 / 🟡 0.5–0.8 / 🔴 <0.5 (паттерн RagContextInspector)', () => {
    const ctx: RagContext = {
      recall_total: 10,
      reranked: false,
      chunks: [
        { rank: 1, chunk_id: 'a', file: 'a.md', section: 'S1', score: 0.8, stage1_rank: null, reranked: false, text: 'green' },
        { rank: 2, chunk_id: 'b', file: 'b.md', section: 'S2', score: 0.6, stage1_rank: null, reranked: false, text: 'yellow' },
        { rank: 3, chunk_id: 'c', file: 'c.md', section: 'S3', score: 0.4, stage1_rank: null, reranked: false, text: 'red' },
      ],
    }
    const { container } = render(<SourcesPanel ragContext={ctx} />)
    fireEvent.click(screen.getByRole('button', PANEL_HEAD))
    const pills = Array.from(container.querySelectorAll('.src-score-pill'))
    expect(pills.length).toBe(3)
    expect(pills[0].textContent).toBe('0.80')
    expect(pills[0].className).toContain('ok')
    expect(pills[1].textContent).toBe('0.60')
    expect(pills[1].className).toContain('warn')
    expect(pills[2].textContent).toBe('0.40')
    expect(pills[2].className).toContain('bad')
  })

  it('B-2: dont_know → красная карточка «🚫 Не знаю» (причина + подсказка); чанк-карточки НЕ рендерятся', () => {
    const dontKnow: RagContext = {
      recall_total: 50,
      reranked: true,
      chunks: [],
      dont_know: true,
    }
    const { container } = render(<SourcesPanel ragContext={dontKnow} minScore={0.5} />)

    // Карточка «не знаю»: заголовок + причина (порог) + подсказка
    expect(screen.getByText(/🚫 Не знаю/i)).toBeInTheDocument()
    expect(screen.getByText('порог ≥ 0.5 — ни один чанк не прошёл')).toBeInTheDocument()
    expect(screen.getByText('Уточните вопрос: о каком документе вы спрашиваете?')).toBeInTheDocument()

    // Чанк-карточки не рендерятся, панели нет
    expect(container.querySelector('.src-card')).toBeNull()
    expect(container.querySelector('.src-panel')).toBeNull()
    expect(container.querySelector('.src-dontknow')).not.toBeNull()
  })

  it('B-2: dont_know без minScore — общая причина («в базе не нашлось…»)', () => {
    const dontKnow: RagContext = { recall_total: 50, reranked: false, chunks: [], dont_know: true }
    render(<SourcesPanel ragContext={dontKnow} />)
    expect(screen.getByText(/🚫 Не знаю/i)).toBeInTheDocument()
    expect(screen.getByText('в базе не нашлось релевантных материалов')).toBeInTheDocument()
    expect(screen.getByText('Уточните вопрос: о каком документе вы спрашиваете?')).toBeInTheDocument()
  })

  it('Старое сообщение (без dont_know, без chunk_id) — рендер без краха, id-строка отсутствует, цитата по клику', () => {
    const { container } = render(<SourcesPanel ragContext={LEGACY_CTX} />)

    // Панель свёрнута; раскрытие
    expect(screen.getByText('📖 ИСТОЧНИКИ И ЦИТАТЫ')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', PANEL_HEAD))
    expect(container.querySelectorAll('.src-card').length).toBe(1)

    // file · section; цитата свёрнута по умолчанию
    expect(screen.getByText('a.md · Раздел')).toBeInTheDocument()
    expect(container.querySelector('.src-quote')).toBeNull()

    // Клик по шапке карточки — цитата; без chunk_id → id-строка не рендерится
    fireEvent.click(screen.getByRole('button', { name: /a\.md · Раздел/ }))
    expect(container.querySelector('.src-quote')?.textContent).toBe('«Старый чанк без chunk_id.»')
    expect(container.querySelector('.src-id')).toBeNull()
  })

  it('F-wave: поле source → строка «source · file · section»; без source → «file · section»', () => {
    const ctx: RagContext = {
      recall_total: 20,
      reranked: false,
      chunks: [
        {
          rank: 1, chunk_id: 'a-structural-0001', source: 'upload',
          file: 'a.md', section: 'Раздел', score: 0.7,
          stage1_rank: null, reranked: false, text: 'Чанк с source.',
        },
        {
          rank: 2, file: 'b.md', section: 'Раздел 2', score: 0.6,
          stage1_rank: null, reranked: false, text: 'Чанк без source.',
        },
      ],
    }
    render(<SourcesPanel ragContext={ctx} />)
    fireEvent.click(screen.getByRole('button', PANEL_HEAD))
    // source есть → «source · file · section»; source нет → «file · section»
    expect(screen.getByText('upload · a.md · Раздел')).toBeInTheDocument()
    expect(screen.getByText('b.md · Раздел 2')).toBeInTheDocument()
  })

  it('chunks пусто и dont_know нет → панель не рендерится (null)', () => {
    const empty: RagContext = { recall_total: 0, reranked: false, chunks: [] }
    const { container } = render(<SourcesPanel ragContext={empty} />)
    expect(container.firstChild).toBeNull()
  })
})
