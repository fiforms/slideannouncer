import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Production build output (dist/) is what image-builder/build.sh stages
// onto the device; nginx serves it as static files with an SPA fallback
// (see system/nginx-slide-announcer.conf). The dev proxy below is only for
// `npm run dev` against a backend running locally at 127.0.0.1:8000 — real
// devices always serve both through the same nginx origin.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
