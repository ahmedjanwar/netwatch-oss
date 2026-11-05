#!/usr/bin/env python3
"""Prepare a Netwatch release: bump the version and turn CHANGELOG's "Unreleased" section into notes.

    python3 scripts/release.py patch|minor|major [--notes notes.md] [--date 2026-11-05]
    python3 scripts/release.py current [--notes notes.md]   # release the version already in the code

Prints the new version. Used by .github/workflows/release.yml; run it by hand only to preview.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "gateway" / "app" / "version.py"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r'__version__ = "(\d+)\.(\d+)\.(\d+)"')


class ReleaseError(Exception):
    pass


def read_version(text: str) -> tuple[int, int, int]:
    m = VERSION_RE.search(text)
    if not m:
        raise ReleaseError(f"no __version__ in {VERSION_FILE.name}")
    return tuple(int(x) for x in m.groups())


def bump(version: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if kind == "major":
        return major + 1, 0, 0
    if kind == "minor":
        return major, minor + 1, 0
    if kind == "patch":
        return major, minor, patch + 1
    raise ReleaseError(f"unknown bump {kind!r}: use patch, minor or major")


def fmt(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def _unreleased(text: str) -> tuple[int, int]:
    """Start and end of the text under "## Unreleased" (up to the next "## " heading)."""
    m = re.search(r"^## Unreleased[ \t]*\n", text, re.M)
    if not m:
        raise ReleaseError('CHANGELOG.md has no "## Unreleased" section')
    nxt = re.search(r"^## ", text[m.end():], re.M)
    return m.end(), m.end() + nxt.start() if nxt else len(text)


def changelog_release(text: str, version: str, date: str) -> str:
    """Move everything under "Unreleased" into a new "## <version> (<date>)" section."""
    start, end = _unreleased(text)
    items = text[start:end].strip()
    if not items:
        raise ReleaseError('Nothing under "## Unreleased" in CHANGELOG.md: add what changed first')
    return text[:start] + f"\n## {version} ({date})\n\n{items}\n\n" + text[end:]


def notes(text: str, version: str) -> str:
    """The changelog section for a version, without its heading."""
    m = re.search(rf"^## {re.escape(version)}\b.*\n", text, re.M)
    if not m:
        raise ReleaseError(f'CHANGELOG.md has no "## {version}" section')
    nxt = re.search(r"^## ", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)].strip() + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("bump", choices=["patch", "minor", "major", "current"])
    p.add_argument("--notes", help="write the release notes to this file")
    p.add_argument("--date", default=datetime.date.today().isoformat())
    a = p.parse_args(argv)
    try:
        vtext, ctext = VERSION_FILE.read_text(), CHANGELOG.read_text()
        current = read_version(vtext)
        if a.bump == "current":
            new = fmt(current)
            start, end = _unreleased(ctext)
            if ctext[start:end].strip():
                raise ReleaseError('"## Unreleased" isn\'t empty: release with patch, minor or major instead')
        else:
            new = fmt(bump(current, a.bump))
            ctext = changelog_release(ctext, new, a.date)
            VERSION_FILE.write_text(VERSION_RE.sub(f'__version__ = "{new}"', vtext))
            CHANGELOG.write_text(ctext)
        if a.notes:
            Path(a.notes).write_text(notes(ctext, new))
    except ReleaseError as e:
        print(f"release: {e}", file=sys.stderr)
        return 1
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
