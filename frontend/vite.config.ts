/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { mockApi } from './mock-plugin.ts'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Adres backendu z środowiska serwera Vite; domyślnie lokalny port 8000.
  const backend = env.BACKEND_URL || process.env.BACKEND_URL || 'http://localhost:8000'
  const useMock = (env.MOCK_API || process.env.MOCK_API) === '1'
  const proxy = { '/api': backend, '/static': backend }
  return {
    plugins: [react(), ...(useMock ? [mockApi()] : [])],
    server: { port: 5173, strictPort: true, proxy: useMock ? undefined : proxy },
    preview: { port: 5173, proxy },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
