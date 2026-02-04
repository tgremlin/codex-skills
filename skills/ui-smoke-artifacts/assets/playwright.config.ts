import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:3000',
    headless: true,
    chromiumSandbox: false,
    launchOptions: {
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
    trace: 'off',
  },
  reporter: [['list']],
})
