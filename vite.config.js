import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  // Dev-only reverse proxy so `npm run dev` (:5173) is same-origin with the
  // backend — the OIDC session cookie only rides same-origin fetches. In a real
  // deployment the system nginx does this (see deploy/nginx/natural-reader.conf).
  server: {
    watch: {
      // Avoid watching the large pdf.worker.min.js file from pdfjs-dist
      ignored: ['**/.venv/**', '**/node_modules/**', '**/data/**', '**/logs/**'],
    },
    proxy: {
      '/v1': 'http://localhost:8000',       // FastAPI backend
      '/api': 'http://localhost:11434',     // Ollama (matches the browser default)
    },
  },
  build: {
    // The pdf.worker.min.js from pdfjs-dist is ~1MB and cannot be split
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        // Rolldown requires manualChunks to be a function
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('react-dom') || id.includes('react')) {
              return 'vendor-react';
            }
            if (id.includes('pdfjs-dist')) {
              return 'vendor-pdfjs';
            }
            if (id.includes('lucide-react')) {
              return 'vendor-icons';
            }
          }
        },
      },
    },
  },
})