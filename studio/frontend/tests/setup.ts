// Настройка vitest: matchers из jest-dom + очистка DOM после каждого теста
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

afterEach(() => {
  cleanup()
})
