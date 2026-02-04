#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

STOP_WORDS = {
    'error', 'errors', 'failed', 'failure', 'cannot', 'could', 'would', 'should',
    'undefined', 'null', 'reading', 'properties', 'object', 'function', 'stack',
    'trace', 'unexpected', 'token', 'server', 'client', 'request', 'response',
    'chunk', 'load', 'loading', 'resource', 'internal', 'external', 'network',
    'script', 'module', 'webpack', 'dev', 'warn', 'warning', 'invalid',
}

CLASSIFICATION_ORDER = [
    'hydration-mismatch',
    'chunk-load-error',
    'cors-error',
    'auth-error',
    'api-contract-error',
    'route-404',
    'server-500',
    'render-crash',
    'unknown',
]


def load_json(path: Path):
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8') as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError:
            return []


def normalize_text(text: str) -> str:
    return text.lower() if text else ''


def iter_error_texts(console_errors, page_errors):
    for entry in page_errors:
        if entry.get('message'):
            yield entry['message']
        if entry.get('stack'):
            yield entry['stack']
    for entry in console_errors:
        if entry.get('text'):
            yield entry['text']


def classify(console_errors, page_errors, network_failures):
    texts = ' '.join(normalize_text(t) for t in iter_error_texts(console_errors, page_errors))

    if 'hydration' in texts:
        return 'hydration-mismatch'
    if 'chunkloaderror' in texts or 'loading chunk' in texts or 'chunk load' in texts:
        return 'chunk-load-error'
    if 'cors' in texts or 'access-control-allow-origin' in texts:
        return 'cors-error'

    if any(n.get('status') in (401, 403) for n in network_failures):
        return 'auth-error'

    if any(is_api_request(n.get('url', '')) and n.get('status', 0) >= 400 for n in network_failures):
        return 'api-contract-error'

    if any(is_route_404(n) for n in network_failures):
        return 'route-404'

    if any((n.get('status') or 0) >= 500 for n in network_failures):
        return 'server-500'

    if 'typeerror' in texts or 'referenceerror' in texts or 'cannot read properties' in texts:
        return 'render-crash'

    if page_errors or console_errors:
        return 'render-crash'

    return 'unknown'


def is_api_request(url: str) -> bool:
    path = urlparse(url).path
    return path.startswith('/api/') or path == '/api'


def is_app_route(path: str) -> bool:
    return path and not path.startswith('/_next') and not path.startswith('/favicon') and not path.startswith('/sw')


def is_route_404(network_entry) -> bool:
    status = network_entry.get('status')
    if status != 404:
        return False
    path = urlparse(network_entry.get('url', '')).path
    return is_app_route(path) and not is_api_request(network_entry.get('url', ''))


def extract_tokens(text: str) -> list[str]:
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9_]{3,}', text or '')
    cleaned = []
    for token in tokens:
        lower = token.lower()
        if lower in STOP_WORDS:
            continue
        cleaned.append(token)
    # Prefer PascalCase tokens (likely component names)
    pascal = [t for t in cleaned if re.match(r'[A-Z][A-Za-z0-9]+', t)]
    ordered = pascal + [t for t in cleaned if t not in pascal]
    seen = set()
    result = []
    for token in ordered:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= 5:
            break
    return result


def normalize_stack_path(raw_path: str) -> str:
    path = raw_path
    if path.startswith('webpack-internal://'):
        path = path.replace('webpack-internal://', '')
    path = path.lstrip('/').replace('(app)/', '').replace('(pages-dir-browser)/', '')
    path = path.replace('./', '')
    return path


def extract_stack_hints(stack: str, repo_root: Path) -> list[dict]:
    hints = []
    if not stack:
        return hints
    stack = stack.replace('file://', '')
    file_re = re.compile(r'([A-Za-z0-9_@+./-]+\.(?:ts|tsx|js|jsx))(?::(\d+))?(?::(\d+))?')
    for match in file_re.finditer(stack):
        raw_path = match.group(1)
        line = match.group(2)
        if os.path.isabs(raw_path):
            candidate = Path(raw_path).resolve()
        else:
            normalized = normalize_stack_path(raw_path)
            candidate = (repo_root / normalized).resolve()
        if candidate.exists():
            hint = {'path': str(candidate)}
            if line:
                hint['line'] = int(line)
            hints.append(hint)
    return hints


def search_repo_for_tokens(tokens: list[str], repo_root: Path) -> list[dict]:
    hints = []
    for token in tokens:
        try:
            result = subprocess.run(
                [
                    'rg', '-n', '-F', '-m', '1', token, str(repo_root),
                    '--glob', '!node_modules/*', '--glob', '!.next/*', '--glob', '!artifacts/*'
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            break
        if result.returncode not in (0, 1):
            continue
        if not result.stdout.strip():
            continue
        line = result.stdout.strip().splitlines()[0]
        parts = line.split(':', 2)
        if len(parts) >= 2:
            path = parts[0]
            try:
                line_no = int(parts[1])
            except ValueError:
                line_no = None
            hint = {'path': path}
            if line_no:
                hint['line'] = line_no
            hints.append(hint)
    return hints


def hint_for_route(url: str, repo_root: Path) -> list[dict]:
    path = urlparse(url).path.strip('/')
    if not path:
        candidates = [repo_root / 'app' / 'page.tsx', repo_root / 'app' / 'page.jsx', repo_root / 'app' / 'page.ts']
        return [{'path': str(c)} for c in candidates if c.exists()]

    segment = path.split('/')[0]
    try:
        result = subprocess.run(
            ['rg', '--files', '-g', f'app/**/{segment}/page.*', str(repo_root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    hints = []
    for line in result.stdout.strip().splitlines():
        if line:
            hints.append({'path': line})
    return hints


def build_error_entries(console_errors, page_errors, network_failures, repo_root: Path):
    entries = []

    for entry in page_errors:
        message = entry.get('message', '')
        stack = entry.get('stack')
        hints = extract_stack_hints(stack, repo_root)
        if not hints:
            hints = search_repo_for_tokens(extract_tokens(message), repo_root)
        entries.append({
            'type': 'pageerror',
            'message': message,
            'stack': stack,
            'source_hints': hints,
        })

    for entry in console_errors:
        message = entry.get('text', '')
        hints = search_repo_for_tokens(extract_tokens(message), repo_root)
        entries.append({
            'type': 'console',
            'message': message,
            'stack': None,
            'source_hints': hints,
        })

    for entry in network_failures:
        url = entry.get('url', '')
        status = entry.get('status', '')
        message = f"{entry.get('method', 'GET')} {url} -> {status} {entry.get('statusText', '')}".strip()
        hints = hint_for_route(url, repo_root)
        entries.append({
            'type': 'network',
            'message': message,
            'stack': None,
            'source_hints': hints,
        })

    return entries


def summarize_failed_requests(network_failures):
    results = []
    for entry in network_failures:
        url = entry.get('url', '')
        status = entry.get('status', 0)
        hint = ''
        if status in (401, 403):
            hint = 'Auth required or forbidden'
        elif status == 404:
            hint = 'Route or API not found'
        elif status >= 500:
            hint = 'Server error (SSR, API, or backend)'
        results.append({
            'url': url,
            'status': status,
            'method': entry.get('method', 'GET'),
            'hint': hint,
        })
    return results


def build_root_causes(classification, top_errors, failed_requests):
    causes = []
    if classification == 'hydration-mismatch':
        causes.append('Hydration mismatch between server-rendered HTML and client state.')
    elif classification == 'chunk-load-error':
        causes.append('Failed to load a JS chunk (stale build or network/asset issue).')
    elif classification == 'cors-error':
        causes.append('CORS misconfiguration blocking API requests.')
    elif classification == 'auth-error':
        causes.append('Auth enforcement blocking requests or redirecting to login.')
    elif classification == 'api-contract-error':
        causes.append('API route returned an error (contract mismatch or backend failure).')
    elif classification == 'route-404':
        causes.append('Route not found or missing page route handler.')
    elif classification == 'server-500':
        causes.append('Server-side error during page/API render.')
    elif classification == 'render-crash':
        causes.append('Unhandled client-side exception during render or interaction.')

    if not causes and top_errors:
        causes.append('Unhandled error detected; inspect top errors for clues.')

    if failed_requests and classification not in ('auth-error', 'route-404'):
        causes.append('Failed network requests may contribute to the crash.')

    return causes[:3]


def build_fix_plan(classification, top_errors, failed_requests):
    plan = []
    if classification == 'hydration-mismatch':
        plan.append('Check for client-only state that differs on first render; gate with useEffect or suppressHydrationWarning.')
    elif classification == 'chunk-load-error':
        plan.append('Clear build cache and ensure client loads the latest assets; verify _next/static availability.')
    elif classification == 'cors-error':
        plan.append('Update API/server CORS headers for the failing origin; confirm preflight handling.')
    elif classification == 'auth-error':
        plan.append('Verify auth guard logic and session seed; ensure test path is allowed or uses dev auth.')
    elif classification == 'api-contract-error':
        plan.append('Inspect failing API response and align client expectations with the payload shape/status codes.')
    elif classification == 'route-404':
        plan.append('Ensure the route exists and is mounted in the app router; confirm baseURL and path.')
    elif classification == 'server-500':
        plan.append('Check server logs for stack trace; fix the first thrown error on the failing route.')
    elif classification == 'render-crash':
        plan.append('Locate the throwing component from stack trace; add guards for undefined/null data.')
    else:
        plan.append('Inspect console/page errors and network failures for the first actionable issue.')

    if failed_requests:
        plan.append('Re-run after fixing failed requests to confirm the UI anchor renders.')

    return plan[:3]


def write_markdown(artifact_dir: Path, triage):
    lines = [
        '# UI Smoke Triage',
        '',
        f"Classification: {triage['classification']}",
        '',
        '## Top errors',
    ]

    for entry in triage['top_errors']:
        lines.append(f"- [{entry['type']}] {entry['message']}")
        for hint in entry.get('source_hints', [])[:3]:
            if 'line' in hint:
                lines.append(f"  - Hint: {hint['path']}:{hint['line']}")
            else:
                lines.append(f"  - Hint: {hint['path']}")

    lines.extend(['', '## Failed requests'])
    for request in triage['failed_requests']:
        hint = f" ({request['hint']})" if request.get('hint') else ''
        lines.append(f"- {request['method']} {request['url']} -> {request['status']}{hint}")

    lines.extend(['', '## Likely root causes'])
    for cause in triage['likely_root_causes']:
        lines.append(f"- {cause}")

    lines.extend(['', '## Minimal fix plan'])
    for step in triage['minimal_fix_plan']:
        lines.append(f"- {step}")

    output_path = artifact_dir / 'triage.md'
    output_path.write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='Triage ui-smoke artifacts and emit structured output.')
    parser.add_argument('artifacts', help='Path to artifacts/ui-smoke/<timestamp>')
    parser.add_argument('repo_root', help='Path to repo root for search hints')
    args = parser.parse_args()

    artifact_dir = Path(args.artifacts).resolve()
    repo_root = Path(args.repo_root).resolve()

    console_errors = [
        entry for entry in load_json(artifact_dir / 'console.json')
        if entry.get('type') == 'error'
    ]
    page_errors = load_json(artifact_dir / 'pageerrors.json')
    network_failures = load_json(artifact_dir / 'network.json')

    classification = classify(console_errors, page_errors, network_failures)

    error_entries = build_error_entries(console_errors, page_errors, network_failures, repo_root)
    top_errors = error_entries[:3]

    failed_requests = summarize_failed_requests(network_failures)
    likely_root_causes = build_root_causes(classification, top_errors, failed_requests)
    minimal_fix_plan = build_fix_plan(classification, top_errors, failed_requests)

    triage = {
        'classification': classification,
        'top_errors': top_errors,
        'failed_requests': failed_requests,
        'likely_root_causes': likely_root_causes,
        'minimal_fix_plan': minimal_fix_plan,
    }

    (artifact_dir / 'triage.json').write_text(json.dumps(triage, indent=2), encoding='utf-8')
    write_markdown(artifact_dir, triage)


if __name__ == '__main__':
    main()
