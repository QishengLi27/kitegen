import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward API requests to the backend uvicorn during local development
      '/chat': 'http://localhost:8000',
      '/reset': 'http://localhost:8000',
      '/portfolio': 'http://localhost:8000',
      '/alerts': 'http://localhost:8000',
      '/usage': 'http://localhost:8000',
      '/briefing': 'http://localhost:8000',
      '/paper': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
