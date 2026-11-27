#!/usr/bin/env python3
"""Prepare Orion release metadata. Commit, tag and push require explicit flags.

Normal development commits do not require a release. See docs/VERSIONING.md.

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
        description="Prepare version/changelog changes; publication is explicit.",
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
    parser.add_argument("--prepare-only", action="store_true", help="prepare metadata only (the default)")
    parser.add_argument("--unreleased", action="store_true",
                       help="record development notes without changing the version")
    parser.add_argument("--commit", action="store_true", help="commit only the prepared metadata")
    parser.add_argument("--tag", action="store_true", help="create a local release tag (requires --commit)")
    parser.add_argument("--push", action="store_true", help="push main and this tag atomically (requires --tag)")
    parser.add_argument("--no-push", action="store_true", help="compatibility flag; never publishes")
    parser.add_argument("--allow-empty", action="store_true",
                       help="release even when only the version/changelog changed")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen")
    args = parser.parse_args()

    if args.push and (not args.tag or not args.commit or args.no_push):
        raise ReleaseError("--push requires --commit --tag and cannot accompany --no-push")
    if args.tag and (not args.commit or args.unreleased):
        raise ReleaseError("--tag requires --commit and cannot tag unreleased notes")
    if args.prepare_only and (args.commit or args.tag or args.push):
        raise ReleaseError("--prepare-only cannot accompany commit, tag or push")
    push = args.push
    ensure_repo_ready(push)

    current = read_version()
    version = "Unreleased" if args.unreleased else args.explicit or next_version(current, args.part)
    if not args.unreleased and not SEMVER_RE.fullmatch(version):
        raise ReleaseError(f"{version!r} is not a bare MAJOR.MINOR.PATCH version")
    tag = f"v{version}"
    if not args.unreleased and tag in git("tag", "--list", tag).split():
        raise ReleaseError(f"tag {tag} already exists")
    if not args.unreleased and tuple(map(int, version.split("."))) <= tuple(map(int, current.split("."))):
        raise ReleaseError("new version must be greater than the current version")
    if MARKER not in CHANGELOG.read_text(encoding="utf-8"):
        raise ReleaseError(f"{CHANGELOG.name} is missing its {MARKER} insertion marker")
    if args.commit:
        changed = git("diff", "--name-only", "HEAD", "--", ".", ":(exclude)orion/__init__.py",
                      ":(exclude)CHANGELOG.md")
        untracked = git("ls-files", "--others", "--exclude-standard")
        if changed or untracked:
            raise ReleaseError("commit implementation changes first; --commit includes release metadata only")
    if not has_changes() and not args.allow_empty:
        raise ReleaseError("working tree is clean — nothing to release (use --allow-empty)")

    sections = build_sections(args)
    entry = render_entry(version, args.message, sections)
    message = commit_message(version, args.message, sections, args.trailer)

    if args.dry_run:
        print(f"{current} -> {version}  (commit={args.commit}, tag={args.tag}, push={push})\n")
        print(entry)
        print("--- commit message ---")
        print(message)
        return 0

    if not args.unreleased:
        write_version(version)
    write_changelog(entry)
    if args.commit:
        git("add", "--", "orion/__init__.py", "CHANGELOG.md")
        git("commit", "--only", "orion/__init__.py", "CHANGELOG.md", "-m", message)
    if args.tag:
        git("tag", "-a", tag, "-m", f"{tag} — {args.message}")
    if push:
        git("push", "--atomic", REMOTE, BRANCH, f"refs/tags/{tag}")
        print(f"released {tag} → {REMOTE}/{BRANCH}")
    else:
        print(f"prepared {version} (commit={args.commit}, tag={args.tag}; nothing pushed)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        sys.exit(1)
