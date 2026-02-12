from pathlib import Path


def test_version_file_exists_and_is_semver():
    version = Path("VERSION")
    assert version.exists()
    value = version.read_text(encoding="utf-8").strip()
    parts = value.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_compat_doc_exists():
    assert Path("docs/compat.md").exists()
