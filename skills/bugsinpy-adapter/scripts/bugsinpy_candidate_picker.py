import json
import sys
from pathlib import Path


HARD_SKIP_TOKENS = [
    "pywin32",
    "pypiwin32",
    "pkg-resources==0.0.0",
    "tensorflow",
    "torch",
]
SOFT_SKIP_TOKENS = ["lxml"]


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _parse_bug_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    if not path.exists():
        return info
    for line in _read_text(path).splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        info[key.strip()] = value.strip().strip('"')
    return info


def _parse_python_version(version: str | None) -> tuple[int, int] | None:
    if not version:
        return None
    try:
        parts = tuple(int(p) for p in version.split(".")[:2])
        if len(parts) == 2:
            return parts
    except Exception:
        return None
    return None


def _load_request() -> dict:
    payload = sys.stdin.read().strip()
    if not payload:
        return {}
    return json.loads(payload)


def _score_candidate(req_text: str, runner: str) -> int:
    score = 0
    if runner in {"unittest", "tox"}:
        score += 2
    if "git+" not in req_text and "http" not in req_text:
        score += 1
    req_lines = [
        line
        for line in req_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if len(req_lines) <= 20:
        score += 1
    if any(token in req_text for token in SOFT_SKIP_TOKENS):
        score -= 2
    return score


def main() -> None:
    request = _load_request()
    bugsinpy_root = Path(request.get("bugsinpy_root", "/mnt/Storage/Repos/BugsInPy"))
    require_non_pytest = bool(request.get("require_non_pytest", True))
    top_n = int(request.get("top_n", 5))
    min_python = request.get("min_python_version", "3.7")
    hard_skip_tokens = request.get("hard_skip_tokens", HARD_SKIP_TOKENS)
    soft_skip_tokens = request.get("soft_skip_tokens", SOFT_SKIP_TOKENS)

    min_python_parts = _parse_python_version(min_python) or (3, 7)

    projects_root = bugsinpy_root / "projects"
    candidates: list[dict[str, object]] = []

    if not projects_root.exists():
        print(
            json.dumps(
                {"candidates": [], "note": "BugInPy projects root not found."},
                indent=2,
                sort_keys=True,
            )
        )
        return

    for project_dir in projects_root.iterdir():
        bugs_dir = project_dir / "bugs"
        if not bugs_dir.is_dir():
            continue
        for bug_dir in bugs_dir.iterdir():
            if not bug_dir.is_dir():
                continue
            run_test = bug_dir / "run_test.sh"
            reqs = bug_dir / "requirements.txt"
            bug_info = bug_dir / "bug.info"
            setup_sh = bug_dir / "setup.sh"
            if not run_test.exists():
                continue
            test_cmd = _read_text(run_test).strip()
            if not test_cmd:
                continue
            if require_non_pytest and "pytest" in test_cmd:
                continue

            runner = "other"
            if "unittest" in test_cmd:
                runner = "unittest"
            elif "tox" in test_cmd:
                runner = "tox"
            elif "nosetests" in test_cmd or "nose" in test_cmd:
                runner = "nose"

            if setup_sh.exists():
                setup_text = _read_text(setup_sh).lower()
                if "pip install unittest" in setup_text:
                    continue

            req_text = _read_text(reqs).lower() if reqs.exists() else ""
            req_lines = [
                line
                for line in req_text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            has_requirements = bool(req_lines)
            if not has_requirements and not setup_sh.exists():
                continue

            if any(token in req_text for token in hard_skip_tokens):
                continue

            if any(token in req_text for token in soft_skip_tokens):
                pass

            info = _parse_bug_info(bug_info)
            py_ver = info.get("python_version")
            py_parts = _parse_python_version(py_ver)
            if py_parts and py_parts < min_python_parts:
                continue

            score = _score_candidate(req_text, runner)
            candidates.append(
                {
                    "project": project_dir.name,
                    "bug_id": bug_dir.name,
                    "runner": runner,
                    "score": score,
                    "test_cmd": test_cmd,
                    "python_version": py_ver,
                    "requirements_count": len(req_lines),
                    "risk": "lxml" if "lxml" in req_text else "",
                }
            )

    candidates.sort(
        key=lambda x: (-int(x["score"]), str(x["project"]), str(x["bug_id"]))
    )
    top_candidates = candidates[:top_n]

    note = None
    selected = None
    if top_candidates and all(entry["project"] == "black" for entry in top_candidates):
        note = "No safe non-black candidates found in top picks; stopping without selection."
    elif top_candidates:
        selected = top_candidates[0]

    output = {
        "candidates": top_candidates,
        "selected": selected,
        "note": note,
        "require_non_pytest": require_non_pytest,
        "top_n": top_n,
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
