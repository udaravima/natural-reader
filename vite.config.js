import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { createReadStream, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// What the speech window's VAD loads by name at run time, so it can't be bundled: served at /vad/ from this app.
const VAD_FILES = {
  'vad.worklet.bundle.min.js': '@ricky0123/vad-web/dist/vad.worklet.bundle.min.js',
  'silero_vad_v5.onnx': '@ricky0123/vad-web/dist/silero_vad_v5.onnx',
  'ort-wasm-simd-threaded.wasm': 'onnxruntime-web/dist/ort-wasm-simd-threaded.wasm',
  'ort-wasm-simd-threaded.mjs': 'onnxruntime-web/dist/ort-wasm-simd-threaded.mjs',
}
const VAD_TYPES = { js: 'text/javascript', mjs: 'text/javascript', wasm: 'application/wasm', onnx: 'application/octet-stream' }
const vadFile = (name) => fileURLToPath(new URL(`node_modules/${VAD_FILES[name]}`, import.meta.url))

function vadFiles() {
  return {
    name: 'vad-files',
    configureServer(server) {
      server.middlewares.use('/vad', (req, res, next) => {
        const name = req.url.split('?')[0].slice(1)
        if (!VAD_FILES[name]) return next()
        res.setHeader('Content-Type', VAD_TYPES[name.split('.').pop()])
        createReadStream(vadFile(name)).pipe(res)
      })
    },
    generateBundle() {
      for (const name of Object.keys(VAD_FILES)) {
        this.emitFile({ type: 'asset', fileName: `vad/${name}`, source: readFileSync(vadFile(name)) })
      }
    },
  }
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    vadFiles(),
  ],
  // Dev-only reverse proxy so `npm run dev` (:5173) is same-origin with the
  // backend — the OIDC session cookie only rides same-origin fetches. In a real
  // deployment the system nginx does this (see docs/sample_configs/).
  server: {
    watch: {
      // Avoid watching the large pdf.worker.min.js file from pdfjs-dist
      ignored: ['**/.venv/**', '**/node_modules/**', '**/data/**', '**/logs/**'],
    },
    proxy: {
      '/v1': 'http://localhost:8000',       // FastAPI backend
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