#!/usr/bin/env node
/* eslint-disable no-console */
const fs = require('fs')
const path = require('path')
const { PNG } = require('pngjs')
const { chromium } = require('@playwright/test')

const BASE_URL = process.env.BASE_URL || 'http://localhost:3000'
const TARGET_PATH = process.env.UI_LAYOUT_PATH || '/routes'
const RUN_ID = process.env.UI_LAYOUT_RUN_ID || new Date().toISOString().replace(/[:.]/g, '-')
const OUTPUT_ROOT = process.env.UI_LAYOUT_OUTPUT || path.join(process.cwd(), 'artifacts', 'ui-layout', RUN_ID)
const SPACING_THRESHOLD = Number(process.env.UI_LAYOUT_SPACING || 8)
const OVERLAP_TOLERANCE = Number(process.env.UI_LAYOUT_OVERLAP_TOLERANCE || 1)
const CLOSE_REMOVE_THRESHOLD = Number(process.env.UI_LAYOUT_CLOSE_REMOVE_SPACING || 16)
const CLOSE_REMOVE_MIN_SIZE = Number(process.env.UI_LAYOUT_CLOSE_REMOVE_MIN_SIZE || 44)
const INCLUDE_TABLET = process.env.UI_LAYOUT_TABLET === '1'
const DISABLE_SERVICE_WORKERS =
  process.env.UI_DISABLE_SW === '1' || process.env.UI_SMOKE_DISABLE_SW === '1'
const IGNORE_DEV_INDICATOR = process.env.UI_LAYOUT_IGNORE_DEV_INDICATOR === '1'

const mobileViewport = (process.env.UI_MOBILE_VIEWPORT || '390x844')
  .split('x')
  .map((value) => Number(value))
const mobileWidth = mobileViewport[0] || 390
const mobileHeight = mobileViewport[1] || 844

const VIEWPORTS = [
  { name: 'mobile', width: mobileWidth, height: mobileHeight },
  { name: 'desktop', width: 1280, height: 720 },
]

const HIGHLIGHT_COLOR = 'rgba(255, 0, 0, 0.25)'

if (INCLUDE_TABLET) {
  VIEWPORTS.splice(1, 0, { name: 'tablet', width: 768, height: 1024 })
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true })
}

function rectIntersection(a, b) {
  const x = Math.max(a.x, b.x)
  const y = Math.max(a.y, b.y)
  const w = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - x)
  const h = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - y)
  return { x, y, width: w, height: h, area: w * h }
}

function rectDistance(a, b) {
  const dx = Math.max(0, Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width)))
  const dy = Math.max(0, Math.max(a.y - (b.y + b.height), b.y - (a.y + a.height)))
  if (dx === 0) return dy
  if (dy === 0) return dx
  return Math.hypot(dx, dy)
}

function isOffscreen(rect, viewport) {
  return (
    rect.x < 0 ||
    rect.y < 0 ||
    rect.x + rect.width > viewport.width ||
    rect.y + rect.height > viewport.height
  )
}

function uniqueIds(items) {
  const seen = new Map()
  return items.map((item) => {
    const count = seen.get(item.id) || 0
    seen.set(item.id, count + 1)
    if (count === 0) return item
    return { ...item, id: `${item.id}-${count + 1}` }
  })
}

async function collectClickables(page) {
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="details-drawer"]')
    if (!root) {
      return []
    }
    const nodes = Array.from(root.querySelectorAll('button, [role="button"], a'))

    const buildSelector = (node) => {
      if (!node || node === root) {
        return '[data-testid="details-drawer"]'
      }
      const parts = []
      let current = node
      while (current && current !== root) {
        if (current.getAttribute) {
          const testId = current.getAttribute('data-testid')
          if (testId) {
            parts.unshift(`[data-testid="${testId}"]`)
            break
          }
        }
        const tag = current.tagName ? current.tagName.toLowerCase() : 'node'
        const parent = current.parentElement
        if (!parent) {
          parts.unshift(tag)
          break
        }
        const siblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName)
        const index = siblings.indexOf(current) + 1
        parts.unshift(`${tag}:nth-of-type(${index})`)
        current = parent
      }
      parts.unshift('[data-testid="details-drawer"]')
      return parts.join(' > ')
    }

    return nodes.map((node, index) => {
      const rect = node.getBoundingClientRect()
      const dataTestId = node.getAttribute('data-testid')
      const ariaLabel = node.getAttribute('aria-label') || ''
      const role = node.getAttribute('role') || ''
      const label = node.textContent || ''
      const href = node.getAttribute('href') || ''
      return {
        id: dataTestId || `clickable-${index + 1}`,
        testId: dataTestId,
        label: label.trim(),
        ariaLabel: ariaLabel.trim(),
        role,
        tagName: node.tagName.toLowerCase(),
        href,
        selector: buildSelector(node),
        bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      }
    })
  })
}

async function collectClipping(page) {
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="details-drawer"]')
    if (!root) return []
    const nodes = Array.from(root.querySelectorAll('[data-testid], h1, h2, h3, button, [role="button"], p, span'))
    return nodes
      .map((node, index) => {
        if (node.classList && node.classList.contains('sr-only')) return null
        const rect = node.getBoundingClientRect()
        const dataTestId = node.getAttribute('data-testid')
        const ariaLabel = node.getAttribute('aria-label') || ''
        const label = (node.textContent || '').trim()
        const scrollWidth = node.scrollWidth
        const clientWidth = node.clientWidth
        const style = window.getComputedStyle(node)
        return {
          id: dataTestId || `${node.tagName.toLowerCase()}-${index + 1}`,
          testId: dataTestId,
          ariaLabel: ariaLabel.trim(),
          label,
          scrollWidth,
          clientWidth,
          overflow: style.overflow,
          textOverflow: style.textOverflow,
          whiteSpace: style.whiteSpace,
          bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          clipped: scrollWidth > clientWidth + 1,
        }
      })
      .filter((item) => item && item.label && item.clipped)
  })
}

async function collectBadgeCandidates(page) {
  return page.evaluate(() => {
    const indicatorHints = ['Try Turbopack', 'Route Static', 'Route Dynamic', 'Route Partial']
    const nodes = Array.from(document.querySelectorAll('span, div, button, a, svg, img'))
    const drawer = document.querySelector('[data-testid="details-drawer"]')
    return nodes
      .map((node, index) => {
        const rawText = (node.textContent || '').trim()
        const isIndicator = indicatorHints.some((hint) => rawText.includes(hint))
        const target = isIndicator ? (node.closest('div') ?? node) : node
        const text = (target.textContent || rawText).trim()
        const testId = target.getAttribute('data-testid') || ''
        const ariaLabel = target.getAttribute('aria-label') || ''
        const title = target.getAttribute('title') || ''
        const role = target.getAttribute('role') || ''
        const className =
          typeof target.className === 'string'
            ? target.className
            : target.className?.baseVal || ''
        const rect = target.getBoundingClientRect()
        const style = window.getComputedStyle(target)
        const borderRadius = Number.parseFloat(style.borderRadius || '0')
        const minSide = Math.min(rect.width, rect.height)
        const isRounded =
          className.includes('rounded') || (Number.isFinite(borderRadius) && borderRadius >= minSide / 2 - 1)
        const isFixed = style.position === 'fixed'
        const zIndex = Number.parseFloat(style.zIndex || '0') || 0
        const hasGraphic = !!target.querySelector('svg, img')
        const strongHint =
          /badge|avatar|profile/i.test(testId) ||
          /profile|avatar/i.test(ariaLabel) ||
          /profile|avatar/i.test(title) ||
          (role === 'img' && /avatar|profile/i.test(text)) ||
          isIndicator
        const smallLetterBadge =
          text && text.length <= 2 && /[A-Za-z]/.test(text) && isRounded
        const fixedLetterBadge =
          text &&
          text.length <= 2 &&
          /[A-Za-z]/.test(text) &&
          isFixed &&
          rect.width <= 64 &&
          rect.height <= 64
        const fixedGraphicBadge =
          isFixed &&
          zIndex >= 1000 &&
          rect.width <= 80 &&
          rect.height <= 80 &&
          (hasGraphic || text.length <= 2 || ariaLabel || title)
        const fixedOverlayBadge =
          isFixed &&
          zIndex >= 1000 &&
          rect.width <= 100 &&
          rect.height <= 100
        if (!strongHint && !smallLetterBadge && !fixedLetterBadge && !fixedGraphicBadge && !fixedOverlayBadge) {
          return null
        }
        const centerX = rect.x + rect.width / 2
        const centerY = rect.y + rect.height / 2
        const topNode = document.elementFromPoint(centerX, centerY)
        const onTop = !!topNode && (topNode === target || target.contains(topNode))
        const insideDrawer = drawer ? drawer.contains(target) : false
        return {
          id: testId || `badge-${index + 1}`,
          label: text,
          testId: testId || null,
          ariaLabel: ariaLabel || null,
          title: title || null,
          role: role || null,
          className,
          tagName: target.tagName ? target.tagName.toLowerCase() : null,
          bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          insideDrawer,
          onTop,
          zIndex,
          isFixed,
        }
      })
      .filter(Boolean)
  })
}

async function collectIndicatorCandidates(page, viewport) {
  const results = []
  const hints = ['Try Turbopack', 'Route Static', 'Route Dynamic', 'Route Partial']
  const frames = page.frames()

  for (const frame of frames) {
    for (const hint of hints) {
      const locator = frame.getByText(hint, { exact: false })
      const count = await locator.count()
      for (let i = 0; i < count; i += 1) {
        const handle = locator.nth(i)
        const box = await handle.boundingBox()
        if (!box) continue
        results.push({
          id: `indicator-${hint}-${i + 1}`,
          label: hint,
          bbox: box,
          insideDrawer: false,
          onTop: true,
          source: 'playwright',
        })
      }
    }

    const nLocator = frame.getByText(/^N$/)
    const nCount = await nLocator.count()
    for (let i = 0; i < nCount; i += 1) {
      const handle = nLocator.nth(i)
      const box = await handle.boundingBox()
      if (!box) continue
      const isSmall = box.width <= 100 && box.height <= 100
      const isBottomLeft =
        box.x < viewport.width / 2 && box.y > viewport.height - 200
      if (!isSmall || !isBottomLeft) continue
      results.push({
        id: `indicator-n-${i + 1}`,
        label: 'N',
        bbox: box,
        insideDrawer: false,
        onTop: true,
        source: 'playwright',
      })
    }
  }

  return results
}

async function captureHighlight(page, target, outputPath) {
  if (!target || !target.bbox) return
  await page.evaluate(({ bbox, color }) => {
    const overlay = document.createElement('div')
    overlay.setAttribute('data-audit-overlay', 'true')
    overlay.style.position = 'fixed'
    overlay.style.left = `${bbox.x}px`
    overlay.style.top = `${bbox.y}px`
    overlay.style.width = `${bbox.width}px`
    overlay.style.height = `${bbox.height}px`
    overlay.style.border = '2px solid red'
    overlay.style.background = color
    overlay.style.zIndex = '2147483647'
    overlay.style.pointerEvents = 'none'
    document.body.appendChild(overlay)
  }, { bbox: target.bbox, color: HIGHLIGHT_COLOR })
  await page.screenshot({ path: outputPath, fullPage: true })
  await page.evaluate(() => {
    document.querySelectorAll('[data-audit-overlay="true"]').forEach((el) => el.remove())
  })
}

function detectOverlayFromScreenshot(imagePath, viewport) {
  if (!fs.existsSync(imagePath)) return null
  const buffer = fs.readFileSync(imagePath)
  const png = PNG.sync.read(buffer)
  const width = png.width
  const height = png.height
  const region = {
    x0: 0,
    x1: Math.min(140, width),
    y0: Math.max(height - 220, 0),
    y1: height,
  }
  const isDark = (x, y) => {
    const idx = (y * width + x) * 4
    const r = png.data[idx]
    const g = png.data[idx + 1]
    const b = png.data[idx + 2]
    const lum = (r + g + b) / 3
    return lum < 70
  }
  const visited = new Uint8Array(width * height)
  let best = null

  for (let y = region.y0; y < region.y1; y += 1) {
    for (let x = region.x0; x < region.x1; x += 1) {
      const idx = y * width + x
      if (visited[idx]) continue
      if (!isDark(x, y)) continue
      const stack = [[x, y]]
      visited[idx] = 1
      let count = 0
      let minX = x
      let maxX = x
      let minY = y
      let maxY = y
      while (stack.length) {
        const [cx, cy] = stack.pop()
        count += 1
        if (cx < minX) minX = cx
        if (cx > maxX) maxX = cx
        if (cy < minY) minY = cy
        if (cy > maxY) maxY = cy
        for (let dy = -1; dy <= 1; dy += 1) {
          for (let dx = -1; dx <= 1; dx += 1) {
            if (dx === 0 && dy === 0) continue
            const nx = cx + dx
            const ny = cy + dy
            if (nx < region.x0 || nx >= region.x1 || ny < region.y0 || ny >= region.y1) {
              continue
            }
            const nidx = ny * width + nx
            if (visited[nidx]) continue
            if (!isDark(nx, ny)) continue
            visited[nidx] = 1
            stack.push([nx, ny])
          }
        }
      }
      const boxWidth = maxX - minX + 1
      const boxHeight = maxY - minY + 1
      const isSmall = boxWidth <= 120 && boxHeight <= 120
      if (!isSmall || count < 200) continue
      if (!best || count > best.count) {
        best = { minX, minY, maxX, maxY, count }
      }
    }
  }

  if (!best) return null
  const bbox = {
    x: best.minX,
    y: best.minY,
    width: best.maxX - best.minX + 1,
    height: best.maxY - best.minY + 1,
  }
  const fitsViewport = bbox.x >= 0 && bbox.y >= 0 && bbox.x + bbox.width <= viewport.width + 2
  if (!fitsViewport) return null
  return bbox
}

async function runViewport(viewport) {
  const viewportDir = path.join(OUTPUT_ROOT, viewport.name)
  const screenshotsDir = path.join(viewportDir, 'screenshots')
  ensureDir(screenshotsDir)

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

  await page.getByTestId('board-root').waitFor({ state: 'visible' })
  const boardViewportPath = path.join(screenshotsDir, 'board-viewport.png')
  await page.screenshot({ path: boardViewportPath, fullPage: false })
  await page.screenshot({ path: path.join(screenshotsDir, 'board.png'), fullPage: true })

  const jobCard = page.getByTestId('job-card').first()
  await jobCard.waitFor({ state: 'visible' })
  await jobCard.click()
  const drawer = page.getByTestId('details-drawer')
  await drawer.waitFor({ state: 'visible' })

  await page.screenshot({ path: path.join(screenshotsDir, 'drawer.png'), fullPage: true })

  const drawerBox = await drawer.boundingBox()
  const closeBox = await page.getByTestId('drawer-close').boundingBox()
  const removeLocator = page.getByTestId('drawer-remove')
  const removeBox = (await removeLocator.count()) > 0 ? await removeLocator.boundingBox() : null

  const clickablesRaw = await collectClickables(page)
  const clickables = uniqueIds(clickablesRaw).filter((item) => item.bbox.width && item.bbox.height)
  const clickableMap = new Map(clickables.map((item) => [item.id, item]))
  const badgeCandidatesRaw = await collectBadgeCandidates(page)
  const indicatorCandidates = await collectIndicatorCandidates(page, viewport)
  const screenshotOverlay = detectOverlayFromScreenshot(boardViewportPath, viewport)
  const screenshotCandidates = screenshotOverlay
    ? [
        {
          id: 'overlay-bottom-left',
          label: 'overlay',
          bbox: screenshotOverlay,
          insideDrawer: false,
          onTop: true,
          source: 'screenshot',
        },
      ]
    : []
  const badgeCandidates = uniqueIds([
    ...badgeCandidatesRaw,
    ...indicatorCandidates,
    ...screenshotCandidates,
  ]).filter(
    (item) => item.bbox.width && item.bbox.height,
  )

  const makeIdentifier = (item) => {
    if (!item) return ''
    if (item.testId) return `data-testid=${item.testId}`
    if (item.ariaLabel) return `aria-label=${item.ariaLabel}`
    if (item.label) return `text=${item.label}`
    return item.selector || item.id
  }

  const buildMeta = (item) => ({
    id: item.id,
    identifier: makeIdentifier(item),
    testId: item.testId || null,
    ariaLabel: item.ariaLabel || null,
    label: item.label || null,
    role: item.role || null,
    tagName: item.tagName || null,
    href: item.href || null,
    selector: item.selector || null,
    bbox: item.bbox,
  })

  const buildBadgeMeta = (badge) => ({
    id: badge.id,
    label: badge.label,
    testId: badge.testId || null,
    ariaLabel: badge.ariaLabel || null,
    title: badge.title || null,
    role: badge.role || null,
    className: badge.className,
    tagName: badge.tagName || null,
    bbox: badge.bbox,
    insideDrawer: badge.insideDrawer,
    onTop: badge.onTop,
    zIndex: badge.zIndex ?? null,
    isFixed: badge.isFixed ?? null,
    source: badge.source || 'dom',
  })

  const overlaps = []
  const spacing = []
  const badgeIssues = []
  const ignoredBadgeIssues = []
  const sizeViolations = []
  const closeRemoveStatus = {
    distance: null,
    threshold: CLOSE_REMOVE_THRESHOLD,
    overlap: false,
  }
  for (let i = 0; i < clickables.length; i += 1) {
    for (let j = i + 1; j < clickables.length; j += 1) {
      const a = clickables[i]
      const b = clickables[j]
      const intersection = rectIntersection(a.bbox, b.bbox)
      if (intersection.width > OVERLAP_TOLERANCE && intersection.height > OVERLAP_TOLERANCE) {
        overlaps.push({
          a: a.id,
          b: b.id,
          intersection,
          aMeta: buildMeta(a),
          bMeta: buildMeta(b),
          rule: 'overlap',
        })
      }

      const distance = rectDistance(a.bbox, b.bbox)
      if (distance < SPACING_THRESHOLD) {
        spacing.push({
          a: a.id,
          b: b.id,
          distance: Number(distance.toFixed(2)),
          threshold: SPACING_THRESHOLD,
          aMeta: buildMeta(a),
          bMeta: buildMeta(b),
          rule: 'spacing',
        })
      }
    }
  }

  if (closeBox && removeBox) {
    const closeMeta = {
      id: 'drawer-close',
      identifier: 'data-testid=drawer-close',
      bbox: closeBox,
    }
    const removeMeta = {
      id: 'drawer-remove',
      identifier: 'data-testid=drawer-remove',
      bbox: removeBox,
    }
    const intersection = rectIntersection(closeBox, removeBox)
    if (intersection.width > OVERLAP_TOLERANCE && intersection.height > OVERLAP_TOLERANCE) {
      overlaps.push({
        a: 'drawer-close',
        b: 'drawer-remove',
        intersection,
        aMeta: closeMeta,
        bMeta: removeMeta,
        rule: 'close-remove-overlap',
      })
      closeRemoveStatus.overlap = true
    }
    const distance = rectDistance(closeBox, removeBox)
    closeRemoveStatus.distance = Number(distance.toFixed(2))
    if (distance < CLOSE_REMOVE_THRESHOLD) {
      spacing.push({
        a: 'drawer-close',
        b: 'drawer-remove',
        distance: Number(distance.toFixed(2)),
        threshold: CLOSE_REMOVE_THRESHOLD,
        aMeta: closeMeta,
        bMeta: removeMeta,
        rule: 'close-remove-spacing',
      })
    }

    if (viewport.name === 'mobile') {
      const closeTooSmall =
        closeBox.width < CLOSE_REMOVE_MIN_SIZE || closeBox.height < CLOSE_REMOVE_MIN_SIZE
      const removeTooSmall =
        removeBox.width < CLOSE_REMOVE_MIN_SIZE || removeBox.height < CLOSE_REMOVE_MIN_SIZE
      if (closeTooSmall) {
        sizeViolations.push({
          id: 'drawer-close',
          bbox: closeBox,
          minSize: CLOSE_REMOVE_MIN_SIZE,
          rule: 'close-hitbox-size',
        })
      }
      if (removeTooSmall) {
        sizeViolations.push({
          id: 'drawer-remove',
          bbox: removeBox,
          minSize: CLOSE_REMOVE_MIN_SIZE,
          rule: 'remove-hitbox-size',
        })
      }
    }
  }

  const offscreen = []
  const viewportRect = { width: viewport.width, height: viewport.height }
  const elementsToCheck = [
    { id: 'details-drawer', bbox: drawerBox },
    { id: 'drawer-close', bbox: closeBox },
  ]
  if (removeBox) {
    elementsToCheck.push({ id: 'drawer-remove', bbox: removeBox })
  }
  clickables.forEach((item) => elementsToCheck.push({ id: item.id, bbox: item.bbox }))

  elementsToCheck.forEach((item) => {
    if (!item.bbox) return
    if (isOffscreen(item.bbox, viewportRect)) {
      const meta = clickableMap.get(item.id)
      offscreen.push({ id: item.id, bbox: item.bbox, viewport })
      if (meta) {
        offscreen[offscreen.length - 1].meta = buildMeta(meta)
      }
    }
  })

  badgeCandidates.forEach((badge) => {
    if (!badge.onTop) return
    const badgeMeta = buildBadgeMeta(badge)
    const ignoreBadge =
      IGNORE_DEV_INDICATOR && badgeMeta.source === 'screenshot' && badgeMeta.label === 'overlay'
    if (drawerBox) {
      const intersection = rectIntersection(badge.bbox, drawerBox)
      if (
        intersection.width > OVERLAP_TOLERANCE &&
        intersection.height > OVERLAP_TOLERANCE &&
        !badge.insideDrawer
      ) {
        const entry = {
          rule: 'badge-overlap-drawer',
          badge: badgeMeta,
          target: { id: 'details-drawer', bbox: drawerBox },
          intersection,
        }
        if (ignoreBadge) {
          ignoredBadgeIssues.push({ ...entry, ignored: true })
        } else {
          badgeIssues.push(entry)
        }
      }
    }

    clickables.forEach((item) => {
      const intersection = rectIntersection(badge.bbox, item.bbox)
      if (intersection.width > OVERLAP_TOLERANCE && intersection.height > OVERLAP_TOLERANCE) {
        const entry = {
          rule: 'badge-overlap-clickable',
          badge: badgeMeta,
          target: buildMeta(item),
          intersection,
        }
        if (ignoreBadge) {
          ignoredBadgeIssues.push({ ...entry, ignored: true })
        } else {
          badgeIssues.push(entry)
        }
      }
    })

    if (closeBox) {
      const distance = rectDistance(badge.bbox, closeBox)
      if (distance < SPACING_THRESHOLD) {
        const entry = {
          rule: 'badge-proximity-close',
          badge: badgeMeta,
          target: { id: 'drawer-close', bbox: closeBox },
          distance: Number(distance.toFixed(2)),
          threshold: SPACING_THRESHOLD,
        }
        if (ignoreBadge) {
          ignoredBadgeIssues.push({ ...entry, ignored: true })
        } else {
          badgeIssues.push(entry)
        }
      }
    }
    if (removeBox) {
      const distance = rectDistance(badge.bbox, removeBox)
      if (distance < SPACING_THRESHOLD) {
        const entry = {
          rule: 'badge-proximity-remove',
          badge: badgeMeta,
          target: { id: 'drawer-remove', bbox: removeBox },
          distance: Number(distance.toFixed(2)),
          threshold: SPACING_THRESHOLD,
        }
        if (ignoreBadge) {
          ignoredBadgeIssues.push({ ...entry, ignored: true })
        } else {
          badgeIssues.push(entry)
        }
      }
    }
  })

  const clipping = await collectClipping(page)

  const screenshotExtras = []
  if (offscreen.length > 0) {
    const target = offscreen[0].meta || offscreen[0]
    screenshotExtras.push({
      label: 'offscreen',
      target,
    })
  }
  if (clipping.length > 0) {
    screenshotExtras.push({
      label: 'clipping',
      target: clipping[0],
    })
  }
  if (sizeViolations.length > 0) {
    screenshotExtras.push({
      label: 'hitbox',
      target: { bbox: sizeViolations[0].bbox },
    })
  }
  if (badgeIssues.length > 0) {
    screenshotExtras.push({
      label: 'badge',
      target: { bbox: badgeIssues[0].badge.bbox },
    })
  }

  const overlapsPath = path.join(viewportDir, 'overlaps.json')
  const spacingPath = path.join(viewportDir, 'spacing.json')
  const clippingPath = path.join(viewportDir, 'clipping.json')
  const offscreenPath = path.join(viewportDir, 'offscreen.json')
  const badgesPath = path.join(viewportDir, 'badges.json')
  const badgeIssuesPath = path.join(viewportDir, 'badge_issues.json')
  const ignoredBadgeIssuesPath = path.join(viewportDir, 'ignored_badge_issues.json')

  fs.writeFileSync(overlapsPath, JSON.stringify(overlaps, null, 2))
  fs.writeFileSync(spacingPath, JSON.stringify(spacing, null, 2))
  fs.writeFileSync(clippingPath, JSON.stringify(clipping, null, 2))
  fs.writeFileSync(offscreenPath, JSON.stringify(offscreen, null, 2))
  fs.writeFileSync(badgesPath, JSON.stringify(badgeCandidates, null, 2))
  fs.writeFileSync(badgeIssuesPath, JSON.stringify(badgeIssues, null, 2))
  fs.writeFileSync(ignoredBadgeIssuesPath, JSON.stringify(ignoredBadgeIssues, null, 2))

  const hardFailures = []
  offscreen.forEach((entry) => {
    const label = (entry.meta?.label || entry.meta?.ariaLabel || '').trim()
    const role = entry.meta?.role || ''
    if (label.toLowerCase() === 'documents' && role === 'tab') {
      hardFailures.push({
        rule: 'documents-tab-offscreen',
        message: 'Documents tab is offscreen on mobile drawer.',
        entry,
      })
    }
  })

  badgeIssues.forEach((issue) => {
    if (issue.rule === 'badge-overlap-clickable' || issue.rule === 'badge-overlap-drawer') {
      const targetId = issue.target?.id || 'unknown'
      hardFailures.push({
        rule: 'badge-overlap',
        message: `Badge overlaps ${targetId}.`,
        issue,
      })
    }
    if (issue.rule === 'badge-proximity-close' || issue.rule === 'badge-proximity-remove') {
      const targetId = issue.target?.id || 'unknown'
      hardFailures.push({
        rule: 'badge-proximity',
        message: `Badge too close to ${targetId}.`,
        issue,
      })
    }
  })

  if (closeRemoveStatus.overlap) {
    hardFailures.push({
      rule: 'close-remove-overlap',
      message: 'Drawer close and remove controls overlap.',
      status: closeRemoveStatus,
    })
  }

  if (
    closeRemoveStatus.distance !== null &&
    closeRemoveStatus.distance < CLOSE_REMOVE_THRESHOLD
  ) {
    hardFailures.push({
      rule: 'close-remove-spacing',
      message: `Drawer close/remove spacing below ${CLOSE_REMOVE_THRESHOLD}px.`,
      status: closeRemoveStatus,
    })
  }

  sizeViolations.forEach((violation) => {
    hardFailures.push({
      rule: violation.rule,
      message: `${violation.id} hitbox below ${violation.minSize}px.`,
      violation,
    })
  })

  const hasViolations =
    overlaps.length +
      spacing.length +
      clipping.length +
      offscreen.length +
      sizeViolations.length +
      badgeIssues.length >
    0

  const reportLines = [
    `# UI Layout Audit (${viewport.name})`,
    '',
    `Viewport: ${viewport.width}x${viewport.height}`,
    `Spacing threshold: ${SPACING_THRESHOLD}px`,
    `Overlap tolerance: ${OVERLAP_TOLERANCE}px`,
    `Close/remove spacing threshold: ${CLOSE_REMOVE_THRESHOLD}px`,
    `Close/remove min hitbox: ${CLOSE_REMOVE_MIN_SIZE}px`,
    '',
    '## Summary',
    `Overlaps: ${overlaps.length}`,
    `Spacing violations: ${spacing.length}`,
    `Clipping issues: ${clipping.length}`,
    `Offscreen elements: ${offscreen.length}`,
    `Hitbox size violations: ${sizeViolations.length}`,
    `Badge candidates: ${badgeCandidates.length}`,
    `Badge issues: ${badgeIssues.length}`,
    `Ignored badge issues: ${ignoredBadgeIssues.length}`,
    `Close/remove distance: ${closeRemoveStatus.distance ?? 'n/a'}px (threshold ${CLOSE_REMOVE_THRESHOLD})`,
    `Close/remove overlap: ${closeRemoveStatus.overlap ? 'YES' : 'NO'}`,
    `Hard failures: ${hardFailures.length}`,
    `Ignore dev indicator: ${IGNORE_DEV_INDICATOR ? 'YES' : 'NO'}`,
    `Any violations: ${hasViolations ? 'YES' : 'NO'}`,
    '',
    '## Key elements',
    `Drawer bbox: ${drawerBox ? JSON.stringify(drawerBox) : 'missing'}`,
    `Close bbox: ${closeBox ? JSON.stringify(closeBox) : 'missing'}`,
    `Remove bbox: ${removeBox ? JSON.stringify(removeBox) : 'missing'}`,
    '',
    '## Notable overlaps',
    ...overlaps.slice(0, 5).map((o) => `- ${o.a} overlaps ${o.b} (${o.rule || 'overlap'})`),
    '',
    '## Notable spacing violations',
    ...spacing.slice(0, 5).map((s) => `- ${s.a} ↔ ${s.b}: ${s.distance}px (${s.rule || 'spacing'})`),
    '',
    '## Notable clipping',
    ...clipping.slice(0, 5).map((c) => `- ${c.id}: "${c.label}" (overflow=${c.overflow}, whiteSpace=${c.whiteSpace})`),
    '',
    '## Notable offscreen',
    ...offscreen.slice(0, 5).map((o) => `- ${o.id}`),
    '',
    '## Badge issues',
    ...badgeIssues
      .slice(0, 5)
      .map((b) => `- ${b.rule}: ${b.badge?.label || b.badge?.id} ↔ ${b.target?.id || 'unknown'}`),
    '',
    '## Hitbox size violations',
    ...sizeViolations.map((s) => `- ${s.id}: ${Math.round(s.bbox.width)}x${Math.round(s.bbox.height)} (min ${s.minSize})`),
    '',
    '## Hard failures',
    ...hardFailures.map((f) => `- ${f.rule}: ${f.message}`),
    '',
  ]

  fs.writeFileSync(path.join(viewportDir, 'report.md'), reportLines.join('\n'))
  fs.writeFileSync(path.join(viewportDir, 'close-remove.json'), JSON.stringify(closeRemoveStatus, null, 2))
  fs.writeFileSync(path.join(viewportDir, 'hitbox.json'), JSON.stringify(sizeViolations, null, 2))
  fs.writeFileSync(path.join(viewportDir, 'hard_failures.json'), JSON.stringify(hardFailures, null, 2))

  for (const extra of screenshotExtras) {
    if (!extra.target || !extra.target.bbox) continue
    const outputPath = path.join(screenshotsDir, `${extra.label}-highlight.png`)
    await captureHighlight(page, extra.target, outputPath)
  }

  await browser.close()
}

async function main() {
  ensureDir(OUTPUT_ROOT)
  for (const viewport of VIEWPORTS) {
    await runViewport(viewport)
  }
  console.log(`Layout audit complete: ${OUTPUT_ROOT}`)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
