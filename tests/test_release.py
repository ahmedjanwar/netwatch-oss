"""The release script: version bumps and turning "Unreleased" into release notes."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parent.parent / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

Intro text.

## Unreleased

- **Faster map:** fewer redraws.
- Fixed a typo.

## 0.1.0 (2026-11-05)

First public release.
"""


@pytest.mark.parametrize("kind, expected", [
    ("patch", (0, 1, 1)), ("minor", (0, 2, 0)), ("major", (1, 0, 0)),
])
def test_version_bumps_follow_semver(kind, expected):
    assert release.bump((0, 1, 0), kind) == expected


def test_a_minor_bump_resets_the_patch_number():
    assert release.bump((1, 4, 7), "minor") == (1, 5, 0)


def test_the_version_is_read_from_the_code():
    assert release.read_version('__version__ = "2.10.3"\n') == (2, 10, 3)
    with pytest.raises(release.ReleaseError):
        release.read_version("nothing here")


def test_unreleased_changes_become_a_dated_version_section():
    out = release.changelog_release(CHANGELOG, "0.2.0", "2026-12-01")
    assert "## Unreleased\n\n## 0.2.0 (2026-12-01)\n\n- **Faster map:** fewer redraws.\n- Fixed a typo.\n\n## 0.1.0" in out
    assert out.startswith("# Changelog\n\nIntro text.\n")


def test_a_release_with_nothing_unreleased_is_refused():
    empty = release.changelog_release(CHANGELOG, "0.2.0", "2026-12-01")
    with pytest.raises(release.ReleaseError, match="Nothing under"):
        release.changelog_release(empty, "0.2.1", "2026-12-02")


def test_release_notes_are_that_versions_section():
    out = release.changelog_release(CHANGELOG, "0.2.0", "2026-12-01")
    assert release.notes(out, "0.2.0") == "- **Faster map:** fewer redraws.\n- Fixed a typo.\n"
    assert release.notes(out, "0.1.0") == "First public release.\n"
    with pytest.raises(release.ReleaseError):
        release.notes(out, "9.9.9")


def test_the_projects_own_changelog_and_version_agree():
    """The version in the code always has a section in CHANGELOG.md."""
    version = release.fmt(release.read_version(release.VERSION_FILE.read_text()))
    assert release.notes(release.CHANGELOG.read_text(), version).strip()
