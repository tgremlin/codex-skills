---
name: console-network-triage
description: Triage ui-smoke-artifacts output without running Playwright by classifying failures, extracting top errors, searching the repo for source hints, and writing triage.md/triage.json. Use when a UI smoke run failed and you need deterministic, structured diagnosis and minimal fix ideas.
---

# Console Network Triage

## Overview

Generate deterministic triage artifacts from ui-smoke output by analyzing console/page errors and failed requests, then searching the repo for source hints.

## Workflow

1. Locate the ui-smoke artifacts folder.
   - Use a specific run folder: `artifacts/ui-smoke/<timestamp>`.

2. Copy the script into the repo (if it does not exist).
   - `scripts/ui-smoke-triage.py`

3. Run the triage script.
   - `python3 scripts/ui-smoke-triage.py <artifacts-path> <repo-root>`

4. Review outputs.
   - `triage.json` for structured output.
   - `triage.md` for a human summary.

## Output schema

```json
{
  "classification": "...",
  "top_errors": [
    {"type":"console|pageerror|network","message":"...","stack":"...","source_hints":[{"path":"...","line":123}]}
  ],
  "failed_requests": [{"url":"...","status":500,"method":"GET","hint":"..."}],
  "likely_root_causes": ["..."],
  "minimal_fix_plan": ["..."]
}
```

## Notes

- Do not run Playwright from this skill.
- Keep output deterministic: same inputs yield same triage.

## Resources

### scripts/
- `ui-smoke-triage.py`: triage generator.
