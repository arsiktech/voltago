import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The Python backend (server.py) exposes the reliable-sources JSON API.
// In dev we proxy /api to it; override with VITE_API_TARGET if it runs elsewhere.
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8799'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5183,
    strictPort: false,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // The service worker consumes this manifest during installation so one normal online
    // visit can precache every hashed JS/CSS/font chunk before a later offline launch.
    manifest: true,
    rollupOptions: {
      output: {
        // Keep the heavy map engine in its own chunk so it loads lazily with MapView.
        manualChunks: (id) => (id.includes('maplibre-gl') ? 'maplibre' : undefined),
      },
    },
  },
})
