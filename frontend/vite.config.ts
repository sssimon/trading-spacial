import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
//
// Env vars (read from frontend/.env, .env.local, etc. via loadEnv with empty
// prefix so non-VITE_ names also surface):
//
//   VITE_DEV_PORT             default 5173 — port for the Vite dev server
//   VITE_API_PROXY_TARGET     default http://localhost:8000
//                             where /api/* gets proxied in dev mode.
//                             Set to http://localhost:8001 if port 8000 is
//                             occupied by something else.
//
// In production the React bundle is served by nginx; this proxy block is
// dev-only. nginx config still hard-routes /api/ → btc_api on the prod box.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const devPort = Number(env.VITE_DEV_PORT || 5173)
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:8000'
  return {
    plugins: [react()],
    server: {
      port: devPort,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
  }
})
