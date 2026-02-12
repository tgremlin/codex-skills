import { test, expect } from '@playwright/test'
import fs from 'fs'
import path from 'path'

const swDisableEnv = process.env.UI_SMOKE_DISABLE_SW ?? process.env.UI_DISABLE_SW
const disableServiceWorkers = swDisableEnv ? swDisableEnv !== '0' : true
const swDisableSource =
  process.env.UI_SMOKE_DISABLE_SW !== undefined
    ? `UI_SMOKE_DISABLE_SW=${process.env.UI_SMOKE_DISABLE_SW}`
    : process.env.UI_DISABLE_SW !== undefined
      ? `UI_DISABLE_SW=${process.env.UI_DISABLE_SW}`
      : 'default'

test.use({
  serviceWorkers: disableServiceWorkers ? 'block' : 'allow',
})

type ConsoleEntry = {
  type: string
  text: string
  location?: {
    url: string
    lineNumber: number
    columnNumber: number
  }
  timestamp: string
}

type PageErrorEntry = {
  message: string
  stack?: string
  timestamp: string
}

type NetworkEntry = {
  url: string
  method: string
  status: number
  statusText: string
  timestamp: string
}

type ChunkErrorEntry = {
  source: 'console' | 'pageerror'
  message: string
  stack?: string
  timestamp: string
}

type AssetFailureEntry = {
  url: string
  method?: string
  status?: number
  statusText?: string
  errorText?: string
  timestamp: string
}

const baseUrl = process.env.BASE_URL ?? 'http://localhost:3000'
const targetPath = process.env.UI_SMOKE_PATH ?? '/routes'
const runId = process.env.UI_SMOKE_RUN_ID ?? new Date().toISOString().replace(/[:.]/g, '-')
const artifactsDir = path.join(process.cwd(), 'artifacts', 'ui-smoke', runId)

const steps = [
  `Visit ${baseUrl}`,
  `Navigate to ${targetPath}`,
  'Assert primary UI anchor',
]

const primaryTestId = process.env.UI_SMOKE_TESTID ?? 'board-root'
const chunkErrorPatterns = [
  /ChunkLoadError/i,
  /Loading chunk [\\w-]+ failed/i,
  /CSS chunk/i,
  /Failed to fetch dynamically imported module/i,
]
const swBlockedErrorPatterns = [
  /Serwist\\.register/i,
  /@serwist\\+window/i,
  /service worker/i,
  /reading 'waiting'/i,
]

const isChunkAsset = (url: string) => {
  const lower = url.toLowerCase()
  if (!lower.includes('/_next/') && !lower.includes('chunk')) return false
  return lower.includes('.js') || lower.includes('.css')
}

test('ui smoke', async ({ page, context }) => {
  fs.mkdirSync(artifactsDir, { recursive: true })

  const consoleLogs: ConsoleEntry[] = []
  const pageErrors: PageErrorEntry[] = []
  const ignoredPageErrors: PageErrorEntry[] = []
  const networkFailures: NetworkEntry[] = []
  const chunkErrors: ChunkErrorEntry[] = []
  const assetFailures: AssetFailureEntry[] = []
  const now = () => new Date().toISOString()
  let failure: unknown
  let actual = 'All checks passed.'

  page.on('console', (msg) => {
    consoleLogs.push({
      type: msg.type(),
      text: msg.text(),
      location: msg.location(),
      timestamp: now(),
    })

    if (msg.type() === 'error' && chunkErrorPatterns.some((pattern) => pattern.test(msg.text()))) {
      chunkErrors.push({
        source: 'console',
        message: msg.text(),
        timestamp: now(),
      })
    }
  })

  page.on('pageerror', (error) => {
    const entry = {
      message: error.message,
      stack: error.stack,
      timestamp: now(),
    }

    if (
      disableServiceWorkers &&
      swBlockedErrorPatterns.some((pattern) => pattern.test(entry.stack || entry.message))
    ) {
      ignoredPageErrors.push(entry)
      return
    }

    pageErrors.push(entry)

    if (chunkErrorPatterns.some((pattern) => pattern.test(error.message))) {
      chunkErrors.push({
        source: 'pageerror',
        message: error.message,
        stack: error.stack,
        timestamp: now(),
      })
    }
  })

  page.on('response', (response) => {
    const status = response.status()
    if (status >= 400) {
      networkFailures.push({
        url: response.url(),
        method: response.request().method(),
        status,
        statusText: response.statusText(),
        timestamp: now(),
      })
    }

    if (status >= 400 && isChunkAsset(response.url())) {
      assetFailures.push({
        url: response.url(),
        method: response.request().method(),
        status,
        statusText: response.statusText(),
        timestamp: now(),
      })
    }
  })

  page.on('requestfailed', (request) => {
    if (!isChunkAsset(request.url())) return
    assetFailures.push({
      url: request.url(),
      method: request.method(),
      errorText: request.failure()?.errorText,
      timestamp: now(),
    })
  })

  // Dev-only auth bypass hook: supply JSON in UI_SMOKE_DEV_AUTH if needed.
  // Example: {"localStorage": {"authToken": "dev"}, "cookies": [{"name": "session", "value": "dev", "url": "http://localhost:3000"}]}
  const devAuth = process.env.UI_SMOKE_DEV_AUTH
  if (devAuth) {
    let parsed: {
      localStorage?: Record<string, string>
      cookies?: Parameters<typeof context.addCookies>[0]
    } | undefined

    try {
      parsed = JSON.parse(devAuth)
    } catch (error) {
      consoleLogs.push({
        type: 'error',
        text: `UI_SMOKE_DEV_AUTH JSON parse failed: ${error instanceof Error ? error.message : String(error)}`,
        timestamp: now(),
      })
    }

    if (parsed?.localStorage) {
      await page.addInitScript((items) => {
        Object.entries(items).forEach(([key, value]) => {
          window.localStorage.setItem(key, String(value))
        })
      }, parsed.localStorage)
    }

    if (parsed?.cookies?.length) {
      await context.addCookies(parsed.cookies)
    }
  }

  const serviceWorkerStatus = {
    supported: false,
    registrationCount: 0,
    registrations: [] as Array<{
      scope: string
      activeScript?: string | null
      waitingScript?: string | null
      installingScript?: string | null
    }>,
  }

  try {
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true })

    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' })

    const targetUrl = new URL(targetPath, baseUrl).toString()
    if (page.url() !== targetUrl) {
      await page.goto(targetUrl, { waitUntil: 'domcontentloaded' })
    }

    try {
      const swInfo = await page.evaluate(async () => {
        if (!('serviceWorker' in navigator)) {
          return { supported: false, registrations: [] as Array<any> }
        }
        const regs = await navigator.serviceWorker.getRegistrations()
        return {
          supported: true,
          registrations: regs.map((reg) => ({
            scope: reg.scope,
            activeScript: reg.active?.scriptURL ?? null,
            waitingScript: reg.waiting?.scriptURL ?? null,
            installingScript: reg.installing?.scriptURL ?? null,
          })),
        }
      })
      serviceWorkerStatus.supported = swInfo.supported
      serviceWorkerStatus.registrations = swInfo.registrations
      serviceWorkerStatus.registrationCount = swInfo.registrations.length
    } catch (swError) {
      consoleLogs.push({
        type: 'error',
        text: `Service worker inspection failed: ${swError instanceof Error ? swError.message : String(swError)}`,
        timestamp: now(),
      })
    }

    await expect(page.getByTestId(primaryTestId)).toBeVisible()
  } catch (error) {
    failure = error
    actual = `Failure: ${error instanceof Error ? error.message : String(error)}`
    const screenshotPath = path.join(artifactsDir, 'screenshot.png')
    try {
      await page.screenshot({ path: screenshotPath, fullPage: true })
    } catch (screenshotError) {
      consoleLogs.push({
        type: 'error',
        text: `Screenshot failed: ${screenshotError instanceof Error ? screenshotError.message : String(screenshotError)}`,
        timestamp: now(),
      })
    }
  } finally {
    const tracePath = path.join(artifactsDir, 'trace.zip')
    try {
      await context.tracing.stop({ path: tracePath })
    } catch (traceError) {
      consoleLogs.push({
        type: 'error',
        text: `Tracing stop failed: ${traceError instanceof Error ? traceError.message : String(traceError)}`,
        timestamp: now(),
      })
    }

    const consolePath = path.join(artifactsDir, 'console.json')
    const pageErrorsPath = path.join(artifactsDir, 'pageerrors.json')
    const networkPath = path.join(artifactsDir, 'network.json')
    const pwaPath = path.join(artifactsDir, 'pwa.json')
    fs.writeFileSync(consolePath, JSON.stringify(consoleLogs, null, 2), 'utf8')
    fs.writeFileSync(pageErrorsPath, JSON.stringify(pageErrors, null, 2), 'utf8')
    fs.writeFileSync(networkPath, JSON.stringify(networkFailures, null, 2), 'utf8')
    fs.writeFileSync(
      pwaPath,
      JSON.stringify(
        {
          disableServiceWorkers,
          serviceWorker: serviceWorkerStatus,
          chunkErrors,
          assetFailures,
          ignoredPageErrors,
        },
        null,
        2,
      ),
      'utf8',
    )

    const consoleErrorCount = consoleLogs.filter((entry) => entry.type === 'error').length
    const screenshotPath = path.join(artifactsDir, 'screenshot.png')
    const hasScreenshot = fs.existsSync(screenshotPath)
    const chunkErrorCount = chunkErrors.length
    const assetFailureCount = assetFailures.length
    const ignoredPageErrorCount = ignoredPageErrors.length
    const swStatus = disableServiceWorkers
      ? `blocked (${swDisableSource})`
      : serviceWorkerStatus.supported
        ? `registered: ${serviceWorkerStatus.registrationCount}`
        : 'not supported'

    const reportLines = [
      '# UI Smoke Report',
      '',
      `Run ID: ${runId}`,
      `Base URL: ${baseUrl}`,
      `Target path: ${targetPath}`,
      '',
      '## Steps attempted',
      ...steps.map((step, index) => `${index + 1}. ${step}`),
      '',
      '## Expected vs actual',
      'Expected: App loads and primary screen renders the anchor element.',
      `Actual: ${actual}`,
      '',
      '## Summary',
      `Console errors: ${consoleErrorCount}`,
      `Page errors: ${pageErrors.length}`,
      `Failed requests: ${networkFailures.length}`,
      '',
      '## PWA/Cache diagnostics',
      `Service worker status: ${swStatus}`,
      `ChunkLoadError occurrences: ${chunkErrorCount}`,
      `JS/CSS chunk failures: ${assetFailureCount}`,
      `Ignored SW-blocked page errors: ${ignoredPageErrorCount}`,
      chunkErrorCount > 0
        ? `Chunk errors: ${chunkErrors.slice(0, 3).map((entry) => entry.message).join(' | ')}`
        : 'Chunk errors: none',
      assetFailureCount > 0
        ? `Chunk failures: ${assetFailures
            .slice(0, 3)
            .map((entry) => `${entry.status ?? 'failed'} ${entry.url}`)
            .join(' | ')}`
        : 'Chunk failures: none',
      'Disable SW (dev/test): UI_SMOKE_DISABLE_SW=1 or UI_DISABLE_SW=1 (set to 0 to allow)',
      '',
      '## Artifacts',
      'console.json',
      'pageerrors.json',
      'network.json',
      'pwa.json',
      hasScreenshot ? 'screenshot.png' : 'screenshot.png (not captured)',
      'trace.zip',
      '',
      '## Notes',
      `Primary test anchor: ${primaryTestId}`,
      '',
    ]

    const reportPath = path.join(artifactsDir, 'report.md')
    fs.writeFileSync(reportPath, reportLines.join('\n'), 'utf8')
  }

  if (failure) {
    throw failure
  }
})
