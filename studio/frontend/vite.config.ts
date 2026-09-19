import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Конфиг Vite: dev-сервер на :5173 с прокси /api → бэкенд :8000; тесты — vitest + jsdom
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
  },
})
