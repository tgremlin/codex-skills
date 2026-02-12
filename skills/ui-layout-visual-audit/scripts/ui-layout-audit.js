#!/usr/bin/env node
/* eslint-disable no-console */
const fs = require('fs')
const path = require('path')
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
    const nodes = Array.from(document.querySelectorAll('span, div, button'))
    const drawer = document.querySelector('[data-testid="details-drawer"]')
    return nodes
      .map((node, index) => {
        const text = (node.textContent || '').trim()
        const testId = node.getAttribute('data-testid') || ''
        const ariaLabel = node.getAttribute('aria-label') || ''
        const role = node.getAttribute('role') || ''
        const className = node.className || ''
        const strongHint =
          /badge|avatar|profile/i.test(testId) ||
          /profile|avatar/i.test(ariaLabel) ||
          (role === 'img' && /avatar|profile/i.test(text))
        const smallLetterBadge =
          text &&
          text.length <= 2 &&
          /[A-Za-z]/.test(text) &&
          className.includes('rounded')
        if (!strongHint && !smallLetterBadge) return null
        const rect = node.getBoundingClientRect()
        const centerX = rect.x + rect.width / 2
        const centerY = rect.y + rect.height / 2
        const topNode = document.elementFromPoint(centerX, centerY)
        const onTop = !!topNode && (topNode === node || node.contains(topNode))
        const insideDrawer = drawer ? drawer.contains(node) : false
        return {
          id: testId || `badge-${index + 1}`,
          label: text,
          testId: testId || null,
          ariaLabel: ariaLabel || null,
          role: role || null,
          className,
          tagName: node.tagName ? node.tagName.toLowerCase() : null,
          bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          insideDrawer,
          onTop,
        }
      })
      .filter(Boolean)
  })
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
  const badgeCandidates = uniqueIds(badgeCandidatesRaw).filter((item) => item.bbox.width && item.bbox.height)

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
    role: badge.role || null,
    className: badge.className,
    tagName: badge.tagName || null,
    bbox: badge.bbox,
    insideDrawer: badge.insideDrawer,
    onTop: badge.onTop,
  })

  const overlaps = []
  const spacing = []
  const badgeIssues = []
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
    if (drawerBox) {
      const intersection = rectIntersection(badge.bbox, drawerBox)
      if (
        intersection.width > OVERLAP_TOLERANCE &&
        intersection.height > OVERLAP_TOLERANCE &&
        !badge.insideDrawer
      ) {
        badgeIssues.push({
          rule: 'badge-overlap-drawer',
          badge: badgeMeta,
          target: { id: 'details-drawer', bbox: drawerBox },
          intersection,
        })
      }
    }

    clickables.forEach((item) => {
      const intersection = rectIntersection(badge.bbox, item.bbox)
      if (intersection.width > OVERLAP_TOLERANCE && intersection.height > OVERLAP_TOLERANCE) {
        badgeIssues.push({
          rule: 'badge-overlap-clickable',
          badge: badgeMeta,
          target: buildMeta(item),
          intersection,
        })
      }
    })

    if (closeBox) {
      const distance = rectDistance(badge.bbox, closeBox)
      if (distance < SPACING_THRESHOLD) {
        badgeIssues.push({
          rule: 'badge-proximity-close',
          badge: badgeMeta,
          target: { id: 'drawer-close', bbox: closeBox },
          distance: Number(distance.toFixed(2)),
          threshold: SPACING_THRESHOLD,
        })
      }
    }
    if (removeBox) {
      const distance = rectDistance(badge.bbox, removeBox)
      if (distance < SPACING_THRESHOLD) {
        badgeIssues.push({
          rule: 'badge-proximity-remove',
          badge: badgeMeta,
          target: { id: 'drawer-remove', bbox: removeBox },
          distance: Number(distance.toFixed(2)),
          threshold: SPACING_THRESHOLD,
        })
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

  fs.writeFileSync(overlapsPath, JSON.stringify(overlaps, null, 2))
  fs.writeFileSync(spacingPath, JSON.stringify(spacing, null, 2))
  fs.writeFileSync(clippingPath, JSON.stringify(clipping, null, 2))
  fs.writeFileSync(offscreenPath, JSON.stringify(offscreen, null, 2))
  fs.writeFileSync(badgesPath, JSON.stringify(badgeCandidates, null, 2))
  fs.writeFileSync(badgeIssuesPath, JSON.stringify(badgeIssues, null, 2))

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
    `Close/remove distance: ${closeRemoveStatus.distance ?? 'n/a'}px (threshold ${CLOSE_REMOVE_THRESHOLD})`,
    `Close/remove overlap: ${closeRemoveStatus.overlap ? 'YES' : 'NO'}`,
    `Hard failures: ${hardFailures.length}`,
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
