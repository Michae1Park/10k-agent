import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// `tenk serve` runs the API on :8000; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
