#!/usr/bin/env node
/* eslint-disable no-console */
const fs = require('fs')
const path = require('path')
const { chromium } = require('@playwright/test')
const pixelmatch = require('pixelmatch')
const { PNG } = require('pngjs')

const BASE_URL = process.env.BASE_URL || 'http://localhost:3000'
const TARGET_PATH = process.env.UI_SNAPSHOT_PATH || '/routes'
const UPDATE_BASELINE = process.env.UI_SNAPSHOT_UPDATE === '1'
const DIFF_THRESHOLD = Number(process.env.UI_SNAPSHOT_THRESHOLD || 0.1)
const MAX_DIFF_PIXELS = Number(process.env.UI_SNAPSHOT_MAX_DIFF_PIXELS || 25)
const SAVE_ACTUAL = process.env.UI_SNAPSHOT_SAVE_ACTUAL === '1'
const DISABLE_SERVICE_WORKERS =
  process.env.UI_DISABLE_SW === '1' || process.env.UI_SMOKE_DISABLE_SW === '1'

const mobileViewport = (process.env.UI_MOBILE_VIEWPORT || '390x844')
  .split('x')
  .map((value) => Number(value))
const mobileWidth = mobileViewport[0] || 390
const mobileHeight = mobileViewport[1] || 844

const VIEWPORTS = [
  { name: 'mobile', width: mobileWidth, height: mobileHeight },
  { name: 'desktop', width: 1280, height: 720 },
]

const BASELINE_ROOT = path.join(process.cwd(), 'tests', 'visual', 'baseline')
const DIFF_ROOT = path.join(process.cwd(), 'tests', 'visual', 'diffs')

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true })
}

function removeIfExists(filePath) {
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath)
  }
}

function compareImages(baselineBuffer, actualBuffer) {
  const baseline = PNG.sync.read(baselineBuffer)
  const actual = PNG.sync.read(actualBuffer)

  if (baseline.width !== actual.width || baseline.height !== actual.height) {
    return {
      diffPixels: Math.max(baseline.width, actual.width) * Math.max(baseline.height, actual.height),
      diffImage: null,
      sizeMismatch: true,
      width: { baseline: baseline.width, actual: actual.width },
      height: { baseline: baseline.height, actual: actual.height },
    }
  }

  const diff = new PNG({ width: baseline.width, height: baseline.height })
  const diffPixels = pixelmatch(baseline.data, actual.data, diff.data, baseline.width, baseline.height, {
    threshold: DIFF_THRESHOLD,
  })

  return {
    diffPixels,
    diffImage: diff,
    sizeMismatch: false,
    width: { baseline: baseline.width, actual: actual.width },
    height: { baseline: baseline.height, actual: actual.height },
  }
}

async function captureStateScreenshot(page, state) {
  if (state === 'board') {
    await page.getByTestId('board-root').waitFor({ state: 'visible' })
  }

  if (state === 'drawer') {
    const jobCard = page.getByTestId('job-card').first()
    await jobCard.waitFor({ state: 'visible' })
    await jobCard.click()
    await page.getByTestId('details-drawer').waitFor({ state: 'visible' })
    await page.getByTestId('drawer-close').waitFor({ state: 'visible' })
  }

  return page.screenshot({ fullPage: true })
}

async function runViewport(viewport) {
  const baselineDir = path.join(BASELINE_ROOT, viewport.name)
  const diffDir = path.join(DIFF_ROOT, viewport.name)
  ensureDir(baselineDir)
  ensureDir(diffDir)

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  })

  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: 1,
    serviceWorkers: DISABLE_SERVICE_WORKERS ? 'block' : 'allow',
  })

  const page = await context.newPage()
  await page.emulateMedia({ reducedMotion: 'reduce' })

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' })
  const targetUrl = new URL(TARGET_PATH, BASE_URL).toString()
  if (page.url() !== targetUrl) {
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded' })
  }

  await page.addStyleTag({
    content: '*{transition-duration:0s !important;animation-duration:0s !important;animation-delay:0s !important;}',
  })

  await page.evaluate(async () => {
    if ('fonts' in document && document.fonts?.ready) {
      await document.fonts.ready
    }
    await new Promise(requestAnimationFrame)
    await new Promise(requestAnimationFrame)
  })

  const results = []
  for (const state of ['board', 'drawer']) {
    const baselinePath = path.join(baselineDir, `${state}.png`)
    const diffPath = path.join(diffDir, `${state}-diff.png`)

    const actualBuffer = await captureStateScreenshot(page, state)

    if (SAVE_ACTUAL) {
      const actualPath = path.join(diffDir, `${state}-actual.png`)
      fs.writeFileSync(actualPath, actualBuffer)
    }

    if (UPDATE_BASELINE) {
      fs.writeFileSync(baselinePath, actualBuffer)
      removeIfExists(diffPath)
      results.push({ state, status: 'baseline-updated' })
      continue
    }

    if (!fs.existsSync(baselinePath)) {
      results.push({
        state,
        status: 'missing-baseline',
        message: `Baseline missing at ${baselinePath}. Run with UI_SNAPSHOT_UPDATE=1 to create it.`,
      })
      continue
    }

    const baselineBuffer = fs.readFileSync(baselinePath)
    const comparison = compareImages(baselineBuffer, actualBuffer)

    if (comparison.sizeMismatch) {
      fs.writeFileSync(diffPath, actualBuffer)
      results.push({
        state,
        status: 'size-mismatch',
        message: `Baseline ${comparison.width.baseline}x${comparison.height.baseline} vs actual ${comparison.width.actual}x${comparison.height.actual}`,
      })
      continue
    }

    if (comparison.diffPixels > 0) {
      if (MAX_DIFF_PIXELS > 0 && comparison.diffPixels <= MAX_DIFF_PIXELS) {
        removeIfExists(diffPath)
        results.push({ state, status: 'tolerated', diffPixels: comparison.diffPixels })
        continue
      }
      fs.writeFileSync(diffPath, PNG.sync.write(comparison.diffImage))
      results.push({
        state,
        status: 'diff',
        diffPixels: comparison.diffPixels,
        diffPath,
      })
    } else {
      removeIfExists(diffPath)
      results.push({ state, status: 'match' })
    }
  }

  await browser.close()

  return { viewport: viewport.name, results }
}

async function main() {
  ensureDir(BASELINE_ROOT)
  ensureDir(DIFF_ROOT)

  const failures = []
  const summaries = []

  for (const viewport of VIEWPORTS) {
    const { viewport: name, results } = await runViewport(viewport)
    summaries.push({ viewport: name, results })

    results.forEach((result) => {
      if (
        result.status !== 'match' &&
        result.status !== 'baseline-updated' &&
        result.status !== 'tolerated'
      ) {
        failures.push({ viewport: name, ...result })
      }
    })
  }

  summaries.forEach((summary) => {
    console.log(`Viewport: ${summary.viewport}`)
    summary.results.forEach((result) => {
      const suffix = result.message ? ` (${result.message})` : ''
      const diffNote =
        result.status === 'tolerated'
          ? ` (diffPixels=${result.diffPixels}, max=${MAX_DIFF_PIXELS})`
          : ''
      console.log(`- ${result.state}: ${result.status}${suffix}${diffNote}`)
    })
  })

  if (failures.length > 0) {
    console.error('\nSnapshot mismatches detected:')
    failures.forEach((failure) => {
      console.error(`- ${failure.viewport}/${failure.state}: ${failure.status}${failure.message ? ` (${failure.message})` : ''}`)
    })
    process.exit(1)
  }

  if (UPDATE_BASELINE) {
    console.log('\nBaselines updated.')
  } else {
    console.log('\nAll snapshots matched.')
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
