from pathlib import Path


def test_baseline_script_exists_and_executable():
    script = Path("scripts/baseline/run_baseline.sh")
    assert script.exists()
    assert script.stat().st_mode & 0o111
