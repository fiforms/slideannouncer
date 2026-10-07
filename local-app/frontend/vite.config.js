import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

// Which product's frontend (src/products/<name>/index.js) is built in —
// package.sh and dev-deploy.sh set KIOSK_PRODUCT; see docs/PRODUCTS.md.
const product = process.env.KIOSK_PRODUCT || 'slideannouncer'
const src = fileURLToPath(new URL('./src', import.meta.url))

// Production build output (dist/) is what image-builder/build.sh stages
// onto the device; nginx serves it as static files with an SPA fallback
// (see system/nginx-slide-announcer.conf). The dev proxy below is only for
// `npm run dev` against a backend running locally at 127.0.0.1:8000 — real
// devices always serve both through the same nginx origin.
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // `@core/...` reaches the product-neutral shell from product code.
      '@core': src,
      '@product': `${src}/products/${product}/index.js`,
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
