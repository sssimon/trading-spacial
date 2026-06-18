import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Solo tests unitarios bajo src/. Los specs de Playwright (frontend/e2e/*.spec.ts)
    // los corre `playwright test`, NO vitest — si vitest los recoge, fallan al
    // colectar (usan el test() de Playwright) y revientan el job de CI.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});
