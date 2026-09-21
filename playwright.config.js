import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';

const python = existsSync('backend/.venv/bin/python') ? 'backend/.venv/bin/python' : 'python3';
export default defineConfig({
  testDir: './tests/ui',
  timeout: 45_000,
  expect: { timeout: 8_000 },
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5174',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: [
    { command: `${python} -m tests.ui.server`, url: 'http://127.0.0.1:8011/health', reuseExistingServer: false },
    { command: 'npm run dev:frontend -- --host 127.0.0.1 --port 5174 --strictPort', url: 'http://127.0.0.1:5174', env: { VITE_API_BASE: 'http://127.0.0.1:8011' }, reuseExistingServer: false },
  ],
});
