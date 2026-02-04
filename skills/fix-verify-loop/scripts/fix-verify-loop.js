#!/usr/bin/env node
/* eslint-disable no-console */
const fs = require('fs')
const path = require('path')
const { spawnSync } = require('child_process')

const cwd = process.cwd()
const now = () => new Date().toISOString()
const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
const packetRoot = process.env.FIX_VERIFY_DIR
  ? path.resolve(process.env.FIX_VERIFY_DIR)
  : path.join(cwd, 'artifacts', 'fix-verify', timestamp)

const packetMetaPath = path.join(packetRoot, 'packet.json')
let packetId = process.env.FIX_VERIFY_RUN_ID
if (!packetId && fs.existsSync(packetMetaPath)) {
  try {
    packetId = JSON.parse(fs.readFileSync(packetMetaPath, 'utf8')).packetId
  } catch (error) {
    packetId = undefined
  }
}
if (!packetId) {
  packetId = timestamp
}

const beforeMarker = path.join(packetRoot, 'before', 'paths.json')
const phase = process.env.FIX_VERIFY_PHASE
  ? process.env.FIX_VERIFY_PHASE
  : fs.existsSync(beforeMarker)
    ? 'after'
    : 'before'

const smokeCmd = process.env.FIX_VERIFY_SMOKE_CMD || './scripts/ui-smoke-docker.sh'
const layoutCmd = process.env.FIX_VERIFY_LAYOUT_CMD || './scripts/ui-smoke-docker.sh'
const snapshotCmd = process.env.FIX_VERIFY_SNAPSHOT_CMD || './scripts/ui-smoke-docker.sh'
const triageCmd = process.env.FIX_VERIFY_TRIAGE_CMD || 'python3 scripts/ui-smoke-triage.py'

const withSnapshots = process.env.FIX_VERIFY_WITH_SNAPSHOTS === '1'
const skipLayout = process.env.FIX_VERIFY_SKIP_LAYOUT === '1'
const skipSmoke = process.env.FIX_VERIFY_SKIP_SMOKE === '1'
const skipTriage = process.env.FIX_VERIFY_SKIP_TRIAGE === '1'

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true })
}

function writeJson(filePath, payload) {
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2))
}

function run(cmd, extraEnv = {}) {
  const result = spawnSync(cmd, {
    cwd,
    env: { ...process.env, ...extraEnv },
    shell: true,
    stdio: 'inherit',
  })
  return result.status === 0
}

function readJson(filePath) {
  if (!fs.existsSync(filePath)) return null
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

function copyIfExists(src, dest) {
  if (!fs.existsSync(src)) return false
  ensureDir(path.dirname(dest))
  fs.copyFileSync(src, dest)
  return true
}

function getGitInfo() {
  const result = spawnSync('git status --porcelain', { cwd, shell: true, encoding: 'utf8' })
  if (result.status !== 0) {
    return null
  }
  const status = result.stdout.trim()
  const diffStat = spawnSync('git diff --stat', { cwd, shell: true, encoding: 'utf8' })
  const diffNames = spawnSync('git diff --name-only', { cwd, shell: true, encoding: 'utf8' })
  return {
    status,
    diffStat: diffStat.stdout.trim(),
    diffNames: diffNames.stdout.trim(),
  }
}

function smokeArtifactsPath(runId) {
  return path.join(cwd, 'artifacts', 'ui-smoke', runId)
}

function layoutArtifactsPath(runId) {
  return path.join(cwd, 'artifacts', 'ui-layout', runId)
}

function summarizeSmoke(smokeDir) {
  const consoleLog = readJson(path.join(smokeDir, 'console.json')) || []
  const pageErrors = readJson(path.join(smokeDir, 'pageerrors.json')) || []
  const network = readJson(path.join(smokeDir, 'network.json')) || []
  const pwa = readJson(path.join(smokeDir, 'pwa.json')) || null
  return {
    consoleErrors: consoleLog.filter((entry) => entry.type === 'error').length,
    pageErrors: pageErrors.length,
    failedRequests: network.length,
    pwa,
  }
}

function summarizeLayout(layoutDir) {
  const viewports = ['mobile', 'desktop']
  const summary = []
  let ok = true

  viewports.forEach((viewport) => {
    const viewportDir = path.join(layoutDir, viewport)
    const overlaps = readJson(path.join(viewportDir, 'overlaps.json')) || []
    const spacing = readJson(path.join(viewportDir, 'spacing.json')) || []
    const badgeIssues = readJson(path.join(viewportDir, 'badge_issues.json')) || []
    const hardFailures = readJson(path.join(viewportDir, 'hard_failures.json')) || []
    const closeRemove = readJson(path.join(viewportDir, 'close-remove.json')) || null

    const closeRemoveOk = closeRemove
      ? closeRemove.overlap === false && closeRemove.distance >= closeRemove.threshold
      : false

    const viewportOk =
      overlaps.length === 0 &&
      spacing.length === 0 &&
      badgeIssues.length === 0 &&
      hardFailures.length === 0 &&
      closeRemoveOk

    if (!viewportOk) ok = false

    summary.push({
      viewport,
      overlaps: overlaps.length,
      spacing: spacing.length,
      badgeIssues: badgeIssues.length,
      hardFailures: hardFailures.length,
      closeRemove,
      ok: viewportOk,
    })
  })

  return { ok, summary }
}

function summarizeTriage(smokeDir) {
  const triage = readJson(path.join(smokeDir, 'triage.json'))
  if (!triage) return null
  return {
    classification: triage.classification,
    topErrors: triage.top_errors || [],
    likelyRootCauses: triage.likely_root_causes || [],
    minimalFixPlan: triage.minimal_fix_plan || [],
  }
}

function writeFinalReport({ packetDir, beforePaths, afterPaths, smokeSummary, layoutSummary, triageSummary, snapshotStatus, gitInfo }) {
  const lines = [
    '# Fix/Verify Packet',
    '',
    `Packet ID: ${packetId}`,
    `Generated: ${now()}`,
    '',
    '## What was broken',
  ]

  if (triageSummary) {
    lines.push(`Classification: ${triageSummary.classification || 'unknown'}`)
    if (triageSummary.topErrors.length) {
      lines.push('Top errors:')
      triageSummary.topErrors.slice(0, 3).forEach((entry) => {
        lines.push(`- [${entry.type}] ${entry.message}`)
      })
    }
    if (triageSummary.likelyRootCauses.length) {
      lines.push('Likely root causes:')
      triageSummary.likelyRootCauses.forEach((cause) => lines.push(`- ${cause}`))
    }
  } else {
    lines.push('No triage data (smoke passed on first run or triage skipped).')
  }

  lines.push('', '## What changed')
  if (gitInfo) {
    lines.push('Changed files:')
    lines.push(gitInfo.diffNames || '(no diff)')
    if (gitInfo.diffStat) {
      lines.push('', 'Diff summary:')
      lines.push(gitInfo.diffStat)
    }
  } else {
    lines.push('Git info unavailable.')
  }

  lines.push('', '## Verification proof')
  if (smokeSummary) {
    lines.push(
      `Smoke: console errors=${smokeSummary.consoleErrors}, page errors=${smokeSummary.pageErrors}, failed requests=${smokeSummary.failedRequests}`,
    )
    if (smokeSummary.pwa) {
      lines.push(
        `PWA: SW supported=${smokeSummary.pwa.serviceWorker?.supported}, registrations=${smokeSummary.pwa.serviceWorker?.registrationCount}, chunkErrors=${(smokeSummary.pwa.chunkErrors || []).length}, assetFailures=${(smokeSummary.pwa.assetFailures || []).length}`,
      )
    }
  } else {
    lines.push('Smoke: missing summary.')
  }

  if (layoutSummary) {
    lines.push(`Layout audit ok: ${layoutSummary.ok ? 'YES' : 'NO'}`)
    layoutSummary.summary.forEach((item) => {
      lines.push(
        `- ${item.viewport}: overlaps=${item.overlaps}, spacing=${item.spacing}, badgeIssues=${item.badgeIssues}, hardFailures=${item.hardFailures}, closeRemoveOk=${item.closeRemove?.overlap === false && item.closeRemove?.distance >= item.closeRemove?.threshold}`,
      )
    })
  } else {
    lines.push('Layout audit: not run.')
  }

  if (snapshotStatus) {
    lines.push(`Snapshots: ${snapshotStatus}`)
  }

  lines.push('', '## Evidence paths')
  if (beforePaths) {
    lines.push(`Before smoke: ${beforePaths.smoke || 'n/a'}`)
    if (beforePaths.triage) lines.push(`Before triage: ${beforePaths.triage}`)
  }
  if (afterPaths) {
    lines.push(`After smoke: ${afterPaths.smoke || 'n/a'}`)
    if (afterPaths.layout) lines.push(`Layout audit: ${afterPaths.layout}`)
    if (afterPaths.snapshots) lines.push(`Snapshots: ${afterPaths.snapshots}`)
  }

  fs.writeFileSync(path.join(packetDir, 'final.md'), lines.join('\n'))
}

function main() {
  ensureDir(packetRoot)
  writeJson(packetMetaPath, { packetId, createdAt: now() })

  const phaseDir = path.join(packetRoot, phase)
  ensureDir(phaseDir)

  if (phase === 'after' && !fs.existsSync(path.join(packetRoot, 'before'))) {
    console.error('Cannot run after phase without before artifacts. Set FIX_VERIFY_PHASE=before first.')
    process.exit(1)
  }

  const paths = { phase, packetId }

  if (!skipSmoke) {
    const smokeRunId = `${packetId}-${phase}-smoke`
    const env = { UI_SMOKE_RUN_ID: smokeRunId }
    const ok = run(smokeCmd, env)
    const smokeDir = smokeArtifactsPath(smokeRunId)
    paths.smoke = smokeDir
    copyIfExists(path.join(smokeDir, 'report.md'), path.join(phaseDir, 'ui-smoke-report.md'))
    copyIfExists(path.join(smokeDir, 'pwa.json'), path.join(phaseDir, 'pwa.json'))

    if (!ok) {
      if (!skipTriage) {
        const triageOk = run(`${triageCmd} ${smokeDir} ${cwd}`)
        if (triageOk) {
          copyIfExists(path.join(smokeDir, 'triage.md'), path.join(phaseDir, 'triage.md'))
        }
      }
      writeJson(path.join(phaseDir, 'paths.json'), paths)
      console.error('Smoke failed. Fix issues, then rerun with FIX_VERIFY_DIR to continue.')
      process.exit(1)
    }
  }

  let layoutSummary = null
  if (!skipLayout) {
    const layoutRunId = `${packetId}-${phase}-layout`
    const env = {
      UI_LAYOUT_RUN_ID: layoutRunId,
      UI_DOCKER_CMD: 'node scripts/ui-layout-audit.js',
    }
    const ok = run(layoutCmd, env)
    const layoutDir = layoutArtifactsPath(layoutRunId)
    paths.layout = layoutDir
    if (ok) {
      copyIfExists(path.join(layoutDir, 'mobile', 'report.md'), path.join(phaseDir, 'layout-mobile-report.md'))
      copyIfExists(path.join(layoutDir, 'desktop', 'report.md'), path.join(phaseDir, 'layout-desktop-report.md'))
      layoutSummary = summarizeLayout(layoutDir)
    }
  }

  let snapshotStatus = null
  if (withSnapshots) {
    const env = {
      UI_DOCKER_CMD: 'node scripts/ui-regression-snapshots.js',
    }
    const ok = run(snapshotCmd, env)
    paths.snapshots = path.join(cwd, 'tests', 'visual', 'diffs')
    snapshotStatus = ok ? 'pass' : 'diffs detected'
    if (!ok) {
      console.error('Snapshot diffs detected. Resolve or update baselines intentionally.')
    }
  }

  writeJson(path.join(phaseDir, 'paths.json'), paths)

  if (phase === 'after') {
    const beforePaths = readJson(path.join(packetRoot, 'before', 'paths.json'))
    const afterPaths = paths
    const smokeSummary = summarizeSmoke(afterPaths.smoke)
    const triageSummary = beforePaths?.smoke ? summarizeTriage(beforePaths.smoke) : null
    const gitInfo = getGitInfo()

    writeFinalReport({
      packetDir: packetRoot,
      beforePaths,
      afterPaths,
      smokeSummary,
      layoutSummary,
      triageSummary,
      snapshotStatus,
      gitInfo,
    })

    if (layoutSummary && !layoutSummary.ok) {
      console.error('Layout audit failed. See final.md for details.')
      process.exit(1)
    }

    if (withSnapshots && snapshotStatus !== 'pass') {
      process.exit(1)
    }
  }

  if (phase === 'before') {
    console.log(`Before phase complete. Resume with FIX_VERIFY_DIR=${packetRoot}`)
  }
}

main()
