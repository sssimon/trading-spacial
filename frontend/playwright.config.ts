import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://localhost:5174',
    headless: true,
    viewport: { width: 1440, height: 900 },
    ignoreHTTPSErrors: true,
    screenshot: 'on',
    video: 'off',
    // cookies are scoped to 127.0.0.1 but frontend runs on localhost;
    // keep them separate from any live session on :5173/:8000.
  },
});
