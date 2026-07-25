#!/usr/bin/env python3
"""Cut an Orion release: bump the version, write the changelog, commit, tag, push.

Every user-requested change ships through this script, so the version in
`orion/__init__.py`, the `CHANGELOG.md` entry, the git tag, and `origin/main` can
never drift apart. See docs/VERSIONING.md.

    python scripts/release.py patch -m "Fix vault ingestion on empty notes"
    python scripts/release.py minor -m "Add finance plugin" \\
        -a "Ledger import + spend widget" -c "Router now prices DeepSeek cache hits"
    python scripts/release.py --set 1.0.0 -m "Platform is stable"
    python scripts/release.py patch -m "..." --dry-run     # show, touch nothing

Notes land under Keep-a-Changelog headings: -a/--added, -c/--changed, -f/--fixed,
-r/--removed (all repeatable). With none given, the headline goes under "Changed".
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "orion" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
MARKER = "<!-- releases -->"
BRANCH = "main"
REMOTE = "origin"

VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class ReleaseError(RuntimeError):
    """Anything that should stop the release with a readable message."""


# --------------------------------------------------------------------------- git


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def ensure_repo_ready(push: bool) -> None:
    if not (ROOT / ".git").exists():
        raise ReleaseError("not a git repository — run git init first")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != BRANCH:
        raise ReleaseError(f"on branch {branch!r}; releases are cut from {BRANCH!r}")
    if push and not git("remote", check=False):
        raise ReleaseError(f"no {REMOTE} remote configured — cannot push")


def has_changes() -> bool:
    return bool(git("status", "--porcelain"))


# ----------------------------------------------------------------------- version


def read_version() -> str:
    match = VERSION_RE.search(INIT.read_text(encoding="utf-8"))
    if not match:
        raise ReleaseError(f"no __version__ found in {INIT}")
    return match.group(1)


def next_version(current: str, part: str) -> str:
    match = SEMVER_RE.match(current)
    if not match:
        raise ReleaseError(
            f"current version {current!r} is not MAJOR.MINOR.PATCH — pass --set explicitly"
        )
    major, minor, patch = (int(g) for g in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(version: str) -> None:
    text = INIT.read_text(encoding="utf-8")
    INIT.write_text(VERSION_RE.sub(f'__version__ = "{version}"', text, count=1), encoding="utf-8")


# --------------------------------------------------------------------- changelog


def render_entry(version: str, headline: str, sections: dict[str, list[str]]) -> str:
    lines = [f"## [{version}] — {date.today().isoformat()}", "", f"{headline}", ""]
    for heading in ("Added", "Changed", "Fixed", "Removed"):
        notes = sections.get(heading)
        if not notes:
            continue
        lines.append(f"### {heading}")
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    return "\n".join(lines)


def write_changelog(entry: str) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    if MARKER not in text:
        raise ReleaseError(f"{CHANGELOG.name} is missing its {MARKER} insertion marker")
    head, tail = text.split(MARKER, 1)
    CHANGELOG.write_text(f"{head}{MARKER}\n\n{entry}\n{tail.lstrip()}", encoding="utf-8")


# ---------------------------------------------------------------------- release


def build_sections(args: argparse.Namespace) -> dict[str, list[str]]:
    sections = {
        "Added": list(args.added),
        "Changed": list(args.changed),
        "Fixed": list(args.fixed),
        "Removed": list(args.removed),
    }
    if not any(sections.values()):
        sections["Changed"] = [args.message]
    return sections


def commit_message(version: str, headline: str, sections: dict[str, list[str]],
                   trailers: list[str]) -> str:
    body = [f"v{version} — {headline}", ""]
    for heading, notes in sections.items():
        body.extend(f"- {heading.lower()}: {note}" for note in notes)
    if trailers:
        body.append("")
        body.extend(trailers)
    return "\n".join(body).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump the version, update the changelog, commit, tag, and push.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "part", nargs="?", default="patch", choices=["major", "minor", "patch"],
        help="which semver component to bump (default: patch)",
    )
    parser.add_argument("--set", dest="explicit", metavar="X.Y.Z",
                       help="use this exact version instead of bumping")
    parser.add_argument("-m", "--message", required=True,
                       help="one-line headline for the release")
    parser.add_argument("-a", "--added", action="append", default=[], metavar="NOTE")
    parser.add_argument("-c", "--changed", action="append", default=[], metavar="NOTE")
    parser.add_argument("-f", "--fixed", action="append", default=[], metavar="NOTE")
    parser.add_argument("-r", "--removed", action="append", default=[], metavar="NOTE")
    parser.add_argument("--trailer", action="append", default=[], metavar="LINE",
                       help="extra commit trailer, e.g. Co-Authored-By: ... (repeatable)")
    parser.add_argument("--no-push", action="store_true", help="commit and tag but do not push")
    parser.add_argument("--allow-empty", action="store_true",
                       help="release even when only the version/changelog changed")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen")
    args = parser.parse_args()

    push = not args.no_push
    ensure_repo_ready(push)

    current = read_version()
    version = args.explicit or next_version(current, args.part)
    if not SEMVER_RE.fullmatch(version):
        raise ReleaseError(f"{version!r} is not a bare MAJOR.MINOR.PATCH version")
    tag = f"v{version}"
    if tag in git("tag", "--list", tag).split():
        raise ReleaseError(f"tag {tag} already exists")
    if not has_changes() and not args.allow_empty:
        raise ReleaseError("working tree is clean — nothing to release (use --allow-empty)")

    sections = build_sections(args)
    entry = render_entry(version, args.message, sections)
    message = commit_message(version, args.message, sections, args.trailer)

    if args.dry_run:
        print(f"{current} -> {version}  (tag {tag}, push={push})\n")
        print(entry)
        print("--- commit message ---")
        print(message)
        return 0

    write_version(version)
    write_changelog(entry)
    git("add", "-A")
    git("commit", "-m", message)
    git("tag", "-a", tag, "-m", f"{tag} — {args.message}")
    if push:
        git("push", REMOTE, BRANCH, "--follow-tags")
        print(f"released {tag} → {REMOTE}/{BRANCH}")
    else:
        print(f"committed and tagged {tag} (not pushed)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        sys.exit(1)
